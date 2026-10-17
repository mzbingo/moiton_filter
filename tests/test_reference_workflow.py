import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from run_workflow import build_reference


class ReferenceWorkflowTests(unittest.TestCase):
    def check_distinct_reference_evidence(self, relative_sources):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / 'report'
            report.mkdir()
            refs, extracted = [], []
            for number, relative in enumerate(relative_sources, 1):
                source = root / 'references' / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(f'video-{number}'.encode())
                refs.append(source)
                cache = root / f'cache-{number}'
                cache.mkdir()
                for index in range(2):
                    (cache / f'{index}.jpg').write_bytes(f'crop-{number}-{index}'.encode())
                extracted.append(({
                    'embeddings': np.array([[1., 0.], [1., 0.]], dtype=np.float32),
                    'colors': np.ones((2, 3), dtype=np.float32),
                    'times': np.array([0., .2]),
                    'tracks': np.array([1, 1]),
                }, cache))
            clothes = np.zeros((2, 2, 3), dtype=np.float32)
            clothes[:, :, 0] = 1.
            pipeline = Mock()
            pipeline.extract.side_effect = extracted
            pipeline.clothing.extract.return_value = (clothes, np.ones((2, 2), dtype=bool))
            args = Namespace(stop_file=None, events=False, dense_fps=12.5,
                             upper_color=.6, lower_color=.65, threshold=.72)
            reference, metadata, checks = build_reference(args, pipeline, refs, report, lambda _: {})
            self.assertEqual(reference['embeddings'].shape, (4, 2))
            self.assertEqual(len(checks), 2)
            self.assertTrue(all(check['passed'] for check in checks))
            all_evidence = []
            for number, (source, item) in enumerate(zip(refs, metadata), 1):
                self.assertEqual(item['file'], source.name)
                self.assertEqual(item['source_path'], str(source.resolve()))
                self.assertEqual(item['detections'], 2)
                self.assertEqual(len(item['evidence']), 2)
                for index, relative in enumerate(item['evidence']):
                    self.assertFalse(Path(relative).is_absolute())
                    self.assertEqual(relative, f'evidence/ref_{number}_{source.stem}_{index}.jpg')
                    self.assertEqual((report / relative).read_bytes(), f'crop-{number}-{index}'.encode())
                all_evidence.extend(item['evidence'])
            self.assertEqual(len(set(all_evidence)), 4)
            self.assertEqual(len(list((report / 'evidence').glob('*.jpg'))), 4)

    def test_same_stem_with_different_extensions_keeps_both_references(self):
        self.check_distinct_reference_evidence(['person.ts', 'person.mp4'])

    def test_same_filename_in_subdirectories_keeps_both_references(self):
        self.check_distinct_reference_evidence(['morning/person.ts', 'evening/person.ts'])


if __name__ == '__main__':
    unittest.main()
