"""Recoverable feature-cache storage and cancellable decoder checks."""
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile
import zlib

import numpy as np


def atomic_bytes(path, payload):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_npz(path, **arrays):
    """A reader sees the previous complete archive or the new complete archive."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, **arrays)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_npz(path):
    try:
        with Path(path).open('rb') as stream, np.load(stream, allow_pickle=False) as archive:
            return {key: archive[key] for key in archive.files}
    except (OSError, TypeError, ValueError, EOFError, zipfile.BadZipFile, zlib.error):
        return None


def load_person_cache(folder):
    """Incomplete arrays/crops are a cache miss, never a permanent clip error."""
    folder = Path(folder)
    data = load_npz(folder / 'data.npz')
    if data is None:
        return None
    try:
        count = len(data['embeddings'])
        shapes = {'embeddings': (count, 512), 'colors': (count, 384),
                  'times': (count,), 'confidence': (count,), 'tracks': (count,),
                  'decoded_frames': (), 'sampled_frames': ()}
        if any(data[key].shape != shape or not np.issubdtype(data[key].dtype, np.number)
               or not np.isfinite(data[key]).all() for key, shape in shapes.items()):
            return None
        if not 0 < data['sampled_frames'] <= data['decoded_frames']:
            return None
        if (data['times'] < 0).any() or (data['confidence'] < 0).any() or (data['confidence'] > 1).any():
            return None
        if not np.equal(data['tracks'], np.floor(data['tracks'])).all():
            return None
        # JPEGs are committed before data.npz. Check both endpoints to detect a
        # truncated crop cheaply without decoding every cached person again.
        for index in range(count):
            with (folder / f'{index}.jpg').open('rb') as stream:
                if stream.read(2) != b'\xff\xd8':
                    return None
                stream.seek(-2, 2)
                if stream.read(2) != b'\xff\xd9':
                    return None
    except (KeyError, TypeError, ValueError, OSError):
        return None
    return data


def invalidate_person_cache(folder):
    """Remove commit markers before refreshing crops; old vectors cannot survive."""
    folder = Path(folder)
    (folder / 'data.npz').unlink(missing_ok=True)
    for path in folder.glob('clothing_v*.npz'):
        path.unlink(missing_ok=True)


def run_cancellable(command, stop_check, timeout=120):
    """communicate drains pipes while allowing stop requests between short waits."""
    stop_check()
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        while True:
            stop_check()
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(.25, remaining))
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
