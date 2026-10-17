import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from app_common import load_report, persist_report
from run_report import RunReport, recover_rows


def result(index=0, copied=False, status='review'):
    return dict(
        file=f'clip-{index}.ts', status=status, score=.75, reid_similarity=.75,
        color_similarity=.65, upper_color=.6, lower_color=.7, support_frames=2,
        time_seconds=2., track_id=1, detections=2, copied=copied, evidence='',
        error='decode failed' if status == 'error' else '', source_path='', manual_decision='',
    )


class RunReportTests(unittest.TestCase):
    def test_interrupted_run_recovers_durable_rows_and_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)
            summary = {'total': 3}
            with RunReport(report, summary) as writer:
                writer.append(result(0, copied=True, status='match'))
                writer.append(result(1, status='error'))
                self.assertEqual(summary['processed'], 2)
                self.assertEqual(summary['copied'], 1)
                self.assertEqual(summary['errors'], 1)
            # No finalize: the worker disappeared while CSV still had no rows.
            recovered_summary, rows = load_report(report)
            self.assertEqual(len(rows), 2)
            self.assertEqual(recovered_summary['status'], 'running')
            self.assertTrue(recovered_summary['recovered_from_journal'])
            self.assertEqual(recovered_summary['processed'], 2)
            self.assertEqual(recovered_summary['copied'], 1)
            self.assertEqual(recovered_summary['errors'], 1)

    def test_recovers_final_complete_object_without_newline_and_partial_utf8_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)
            journal = report / 'results.jsonl'
            first = json.dumps(result()).encode('utf-8')
            journal.write_bytes(first)
            self.assertEqual(recover_rows(report), [result()])
            journal.write_bytes(first + b'\n{"file":"' + b'\xe4\xb8')
            self.assertEqual(recover_rows(report), [result()])

    def test_rejects_malformed_complete_records_and_non_object_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)
            journal = report / 'results.jsonl'
            for payload in (b'{broken}\n{}\n', b'{broken}\n', b'[]'):
                with self.subTest(payload=payload):
                    journal.write_bytes(payload)
                    with self.assertRaises(ValueError):
                        recover_rows(report)

    def test_missing_and_empty_journal_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)
            self.assertIsNone(recover_rows(report))
            (report / 'results.jsonl').touch()
            self.assertEqual(recover_rows(report), [])

    def test_full_exports_only_at_start_and_finish(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('run_report.persist_report', wraps=persist_report) as export:
                writer = RunReport(Path(folder), {'total': 30})
                for index in range(30):
                    writer.append(result(index, copied=index % 2 == 0))
                self.assertEqual(export.call_count, 1)
                writer.finalize()
                writer.finalize()
                self.assertEqual(export.call_count, 2)
                summary, rows = load_report(folder)
                self.assertEqual((summary['processed'], summary['copied'], summary['errors']), (30, 15, 0))
                self.assertEqual(summary['status'], 'completed')
                self.assertEqual(len(rows), 30)
                self.assertNotIn('recovered_from_journal', summary)
                with self.assertRaises(RuntimeError):
                    writer.append(result(31))

    def test_summary_checkpoints_by_elapsed_time_or_row_count(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [10.0]
            with patch('run_report.time.monotonic', side_effect=lambda: now[0]):
                with RunReport(Path(folder), {}) as writer:
                    with patch('run_report.save_json') as checkpoint:
                        for index in range(24):
                            writer.append(result(index))
                        self.assertEqual(checkpoint.call_count, 0)
                        writer.append(result(24))
                        self.assertEqual(checkpoint.call_count, 1)
                        now[0] += 2.1
                        writer.append(result(25))
                        self.assertEqual(checkpoint.call_count, 2)
                        writer.append(result(26))
                        self.assertEqual(checkpoint.call_count, 2)

    def test_finalize_cancel_and_failed_preserve_completed_results(self):
        for status in ('cancelled', 'failed'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                writer = RunReport(Path(folder), {'total': 3})
                writer.append(result(copied=True, status='match'))
                writer.finalize(status, fatal_error='worker failure' if status == 'failed' else None)
                summary, rows = load_report(folder)
                self.assertEqual(summary['status'], status)
                self.assertEqual(summary['cancelled'], status == 'cancelled')
                self.assertEqual((summary['processed'], summary['copied']), (1, 1))
                self.assertEqual(len(rows), 1)
                if status == 'failed':
                    self.assertEqual(summary['fatal_error'], 'worker failure')

    def test_appended_result_is_not_changed_by_later_caller_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            writer = RunReport(Path(folder), {})
            row = result()
            writer.append(row)
            row['status'] = 'manual_excluded'
            writer.finalize()
            self.assertEqual(load_report(folder)[1][0]['status'], 'review')


if __name__ == '__main__':
    unittest.main()
