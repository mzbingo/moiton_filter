from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cache_io import atomic_npz, load_person_cache, run_cancellable
from clothing import ClothingExtractor
from events import StopRequested
import filter_videos


def person_data(count=1):
    return dict(embeddings=np.zeros((count, 512), np.float32),
                colors=np.zeros((count, 384), np.float32),
                times=np.zeros(count), confidence=np.ones(count), tracks=np.arange(count),
                decoded_frames=np.asarray(1), sampled_frames=np.asarray(1))


def write_crops(folder, count):
    for index in range(count):
        cv2.imwrite(str(folder / f'{index}.jpg'), np.zeros((40, 20, 3), np.uint8))


class NoPoseModel:
    def __init__(self):
        self.calls = 0

    def predict(self, crops, **kwargs):
        self.calls += 1
        return [SimpleNamespace(keypoints=None) for _ in crops]


class OneFrameCapture:
    def __init__(self):
        self.grabbed = False

    def isOpened(self):
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FPS: 25, cv2.CAP_PROP_FRAME_COUNT: 1,
                cv2.CAP_PROP_POS_MSEC: 0}.get(prop, 0)

    def grab(self):
        if self.grabbed:
            return False
        self.grabbed = True
        return True

    def retrieve(self):
        return True, np.zeros((40, 40, 3), np.uint8)

    def release(self):
        pass


class CacheTests(unittest.TestCase):
    def test_failed_atomic_write_preserves_previous_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'data.npz'
            atomic_npz(cache, original=np.asarray([1, 2]))
            original = cache.read_bytes()
            with patch('cache_io.np.savez_compressed', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    atomic_npz(cache, replaced=np.asarray([3]))
            self.assertEqual(cache.read_bytes(), original)
            self.assertEqual(list(Path(tmp).iterdir()), [cache])

    def test_person_cache_rejects_truncation_missing_crop_and_invalid_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'data.npz').write_bytes(b'PK\x03\x04truncated')
            self.assertIsNone(load_person_cache(folder))
            atomic_npz(folder / 'data.npz', **person_data())
            self.assertIsNone(load_person_cache(folder))
            write_crops(folder, 1)
            self.assertIsNotNone(load_person_cache(folder))
            data = person_data()
            data['times'] = np.zeros(2)
            atomic_npz(folder / 'data.npz', **data)
            self.assertIsNone(load_person_cache(folder))
            atomic_npz(folder / 'data.npz', **person_data())
            crop = folder / '0.jpg'
            crop.write_bytes(crop.read_bytes()[:-4])
            self.assertIsNone(load_person_cache(folder))

    def test_clothing_full_hit_is_read_only_and_model_change_recomputes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, model = Path(tmp), NoPoseModel()
            write_crops(folder, 1)
            extractor = ClothingExtractor(model, 'cpu', cache_signature='weights-A')
            expected = extractor.extract(folder, 1)
            cache = folder / 'clothing_v1.npz'
            old = cache.stat().st_mtime_ns
            with patch('clothing.atomic_npz', side_effect=AssertionError('full hit must not write')):
                actual = extractor.extract(folder, 1)
            self.assertEqual(model.calls, 1)
            self.assertEqual(cache.stat().st_mtime_ns, old)
            np.testing.assert_array_equal(expected[0], actual[0])
            ClothingExtractor(model, 'cpu', cache_signature='weights-B').extract(folder, 1)
            self.assertEqual(model.calls, 2)
            cache.write_bytes(b'corrupt archive')
            ClothingExtractor(model, 'cpu', cache_signature='weights-B').extract(folder, 1)
            self.assertEqual(model.calls, 3)

    def test_cancelled_clothing_batch_is_checkpointed_and_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, model = Path(tmp), NoPoseModel()
            write_crops(folder, 17)

            def stop_after_batch():
                if model.calls:
                    raise StopRequested()

            extractor = ClothingExtractor(model, 'cpu', stop_check=stop_after_batch)
            with self.assertRaises(StopRequested):
                extractor.extract(folder, 17)
            with np.load(folder / 'clothing_v1.npz', allow_pickle=False) as cache:
                self.assertEqual(int(cache['done'].sum()), 16)
            ClothingExtractor(model, 'cpu').extract(folder, 17)
            self.assertEqual(model.calls, 2)

    def test_refresh_cancellation_invalidates_old_features_and_clothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'models').mkdir()
            for name in ['yolo11s.pt', 'osnet_msmt17.pth']:
                (root / 'models' / name).write_bytes(b'model')
            (root / 'tracker.yaml').write_text('tracker')
            source = root / 'clip.ts'
            source.write_bytes(b'video')
            pipeline = object.__new__(filter_videos.Pipeline)
            pipeline.args = SimpleNamespace(refresh=False, imgsz=1280, stop_file=None, events=False)
            pipeline.device = 'cpu'
            pipeline.detector = SimpleNamespace(predictor=None, track=lambda *a, **kw: [SimpleNamespace(boxes=[])])
            with patch.object(filter_videos, 'ROOT', root), patch.object(filter_videos.cv2, 'VideoCapture', return_value=OneFrameCapture()):
                _, folder = pipeline.extract(source, 5)
            atomic_npz(folder / 'clothing_v1.npz', hist=np.ones(1))
            pipeline.args.refresh = True
            capture = OneFrameCapture()
            with patch.object(filter_videos, 'ROOT', root), patch.object(filter_videos.cv2, 'VideoCapture', return_value=capture), patch.object(capture, 'grab', side_effect=StopRequested):
                with self.assertRaises(StopRequested):
                    pipeline.extract(source, 5)
            self.assertFalse((folder / 'data.npz').exists())
            self.assertFalse((folder / 'clothing_v1.npz').exists())
            # The next ordinary run must decode again instead of trusting a
            # partially refreshed generation; corrupt archives behave likewise.
            pipeline.args.refresh = False
            (folder / 'data.npz').write_bytes(b'truncated')
            with patch.object(filter_videos, 'ROOT', root), patch.object(filter_videos.cv2, 'VideoCapture', return_value=OneFrameCapture()) as opened:
                data, _ = pipeline.extract(source, 5)
            opened.assert_called_once()
            self.assertEqual(int(data['decoded_frames']), 1)
            self.assertIsNotNone(load_person_cache(folder))

    def test_decoder_process_is_stopped_on_cancellation(self):
        calls = 0

        def stop():
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise StopRequested()

        started = time.monotonic()
        with self.assertRaises(StopRequested):
            run_cancellable([sys.executable, '-c', 'import time; time.sleep(30)'], stop)
        self.assertLess(time.monotonic() - started, 3)


if __name__ == '__main__':
    unittest.main()
