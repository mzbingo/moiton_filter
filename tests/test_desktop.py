import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from app_common import ReviewStore,copy_exact,load_report,persist_report,validate_folders,pending_videos
from desktop_controller import DesktopController


class DesktopTests(unittest.TestCase):
    def test_decisions_are_target_and_source_specific(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'clip.ts';source.write_bytes(b'original')
            first=ReviewStore(root/'decisions.json','person-a')
            first.set(source,'exclude')
            self.assertEqual(first.get(source),'exclude')
            self.assertIsNone(ReviewStore(root/'decisions.json','person-b').get(source))
            source.write_bytes(b'changed-source')
            self.assertIsNone(first.get(source))

    def test_manual_review_copies_backs_up_and_persists_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);report=root/'report';report.mkdir()
            inputs=root/'input';inputs.mkdir()
            source=inputs/'clip.ts';source.write_bytes(b'original-video-bytes')
            row=dict(file='clip.ts',status='review',score=.7,reid_similarity=.7,color_similarity=0,
                upper_color=.6,lower_color=.7,support_frames=1,time_seconds=2.,track_id=1,detections=2,
                copied=False,evidence='',error='',source_path=str(source),manual_decision='')
            summary=dict(input_dir=str(inputs),output_dir=str(root/'output'),target_id='target')
            persist_report([row],report,summary)
            controller=DesktopController(root);controller.load(report)
            controller.review(controller.rows,'keep')
            self.assertEqual((root/'output/clip.ts').read_bytes(),source.read_bytes())
            self.assertTrue(load_report(report)[1][0]['copied'])
            controller.review(controller.rows,'exclude')
            self.assertFalse((root/'output/clip.ts').exists())
            self.assertEqual((report/'removed_from_output/clip.ts').read_bytes(),source.read_bytes())
            controller.load(report)
            self.assertEqual(controller.rows[0]['status'],'manual_excluded')
            self.assertEqual(controller.store.get(source),'exclude')
            self.assertEqual((root/'manual_excluded/clip.ts').read_bytes(),source.read_bytes())
            self.assertEqual(pending_videos([source],controller.store),([],1))
            self.assertIn('manual_excluded',(report/'review.html').read_text(encoding='utf-8'))
            controller.review(controller.rows,'keep')
            self.assertFalse((root/'manual_excluded/clip.ts').exists())
            self.assertEqual(pending_videos([source],controller.store),([source],0))

    def test_outputs_cannot_overlap_inputs_or_overwrite_different_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ref=root/'ref';src=root/'src';ref.mkdir();src.mkdir()
            (ref/'r.ts').write_bytes(b'ref');(src/'s.ts').write_bytes(b'src')
            with self.assertRaises(ValueError):validate_folders(ref,src,src/'output')
            output=root/'output.ts';output.write_bytes(b'unrelated')
            with self.assertRaises(ValueError):copy_exact(src/'s.ts',output)
            self.assertEqual(output.read_bytes(),b'unrelated')


if __name__=='__main__':unittest.main()
