"""Durable per-video results with a single final CSV/HTML export.

The JSONL journal survives an interrupted worker. Full exports are written only
when the run starts and finishes; the small summary is checkpointed periodically.
"""
import json
import os
import time
from pathlib import Path

from app_common import persist_report, save_json


JOURNAL_NAME = 'results.jsonl'
CHECKPOINT_SECONDS = 2.0
CHECKPOINT_ROWS = 25


class RunReport:
    def __init__(self, report: Path, summary: dict):
        self.report = Path(report)
        self.report.mkdir(parents=True, exist_ok=True)
        self.summary = summary
        self.rows = []
        self._finalized = False
        self._pending_rows = 0
        self._last_checkpoint = time.monotonic()
        # Exclusive creation prevents accidentally replacing a previous journal.
        self._journal = (self.report / JOURNAL_NAME).open('x', encoding='utf-8', newline='\n')
        self.summary.update(status='running', cancelled=False, processed=0, copied=0, errors=0)
        self.summary.pop('fatal_error', None)
        try:
            persist_report([], self.report, self.summary)
        except BaseException:
            self._journal.close()
            raise

    def append(self, row: dict):
        """Persist one completed result before advertising it in the summary."""
        if self._journal.closed or self._finalized:
            raise RuntimeError('Cannot append to a closed run report')
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False)
        snapshot = json.loads(encoded)
        self._journal.write(encoded + '\n')
        self._journal.flush()
        os.fsync(self._journal.fileno())
        self.rows.append(snapshot)
        self.summary['processed'] += 1
        self.summary['copied'] += int(bool(snapshot.get('copied')))
        self.summary['errors'] += int(snapshot.get('status') == 'error')
        self._pending_rows += 1
        now = time.monotonic()
        if self._pending_rows >= CHECKPOINT_ROWS or now - self._last_checkpoint >= CHECKPOINT_SECONDS:
            save_json(self.report / 'summary.json', self.summary)
            self._last_checkpoint = now
            self._pending_rows = 0

    def finalize(self, status='completed', fatal_error=None):
        """Close the journal and export every completed row, including on cancel."""
        if self._finalized:
            return self.summary
        if status not in {'completed', 'cancelled', 'failed'}:
            raise ValueError(f'Invalid run status: {status}')
        self.close()
        self.summary.update(
            status=status,
            cancelled=status == 'cancelled',
            processed=len(self.rows),
            copied=sum(bool(row.get('copied')) for row in self.rows),
            errors=sum(row.get('status') == 'error' for row in self.rows),
        )
        if fatal_error is not None:
            self.summary['fatal_error'] = str(fatal_error)
        persist_report(self.rows, self.report, self.summary)
        self._finalized = True
        return self.summary

    def close(self):
        """Release the stream without discarding recovery data on an exception."""
        if not self._journal.closed:
            try:
                self._journal.flush()
                os.fsync(self._journal.fileno())
            finally:
                self._journal.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def recover_rows(report: Path):
    """Read a journal; ignore only its final, incomplete JSON record.

    None means this is an older report without a journal. Malformed complete
    records raise instead of silently hiding lost or corrupted video results.
    """
    journal = Path(report) / JOURNAL_NAME
    try:
        stream = journal.open('rb')
    except FileNotFoundError:
        return None
    rows = []
    with stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                if not line.endswith(b'\n'):
                    break
                raise ValueError(f'Invalid result journal record at line {line_number}: {journal}') from error
            if not isinstance(row, dict):
                raise ValueError(f'Result journal record at line {line_number} must be an object: {journal}')
            rows.append(row)
    return rows
