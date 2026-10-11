"""Verify the latest output against original input bytes and the run manifest."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    report = Path((ROOT/'reports/latest.txt').read_text(encoding='utf-8').strip())
    with (report/'results.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows = list(csv.DictReader(stream))
    summary=json.loads((report/'summary.json').read_text(encoding='utf-8'))
    output=Path(summary.get('output_dir',ROOT/'output'))
    source_dir=Path(summary.get('input_dir',ROOT/'input/waiting_for_filter_videos'))
    by_name={r['file']:r for r in rows}
    selected = {r['file'] for r in rows if r['copied']=='True'}
    actual = {p.name for p in output.iterdir() if p.is_file()}
    failures = []
    for name in selected:
        source = Path(by_name[name].get('source_path') or source_dir/name)
        target = output/name
        if not source.exists() or not target.exists() or digest(source) != digest(target):
            failures.append(name)
    result = {'processed':len(rows),'statuses':dict(Counter(r['status'] for r in rows)),
              'copied':len(selected),'sha256_mismatches':failures,
              'missing':sorted(selected-actual),'extra':sorted(actual-selected)}
    (report/'output_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if failures or selected-actual or actual-selected else 0


if __name__=='__main__':
    raise SystemExit(main())
