"""JSONL events for the desktop UI, alongside existing readable CLI output."""
import json
from pathlib import Path


class StopRequested(Exception):
    pass


def check_stop(path):
    if path and Path(path).exists():
        raise StopRequested('Stopped by user')


def emit(args,kind,**payload):
    if getattr(args,'events',False):
        print('@event '+json.dumps({'type':kind,**payload},ensure_ascii=False),flush=True)
