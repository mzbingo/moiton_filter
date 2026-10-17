import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from app_common import ReviewStore,archive_output,load_report,target_identity
from run_workflow import execute
from desktop_controller import DesktopController


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.refs=self.root/'refs';self.refs.mkdir();(self.refs/'ref.ts').write_bytes(b'reference')
        self.inputs=self.root/'inputs';self.inputs.mkdir()
        self.source=self.inputs/'one.ts';self.source.write_bytes(b'original')
        self.output=self.root/'output';self.output.mkdir();(self.output/'old.ts').write_bytes(b'previous')
        self.args=Namespace(reference_dir=self.refs,input_dir=self.inputs,output_dir=self.output,
            excluded_dir=self.root/'excluded',report_dir=self.root/'reports/test',decisions_file=self.root/'decisions.json',
            stop_file=self.root/'stop.flag',limit=0,no_latest=True,archive_output=True,events=False,
            fps=5,dense_fps=12.5,upper_color=.6,lower_color=.65,threshold=.72)
        self.store=ReviewStore(self.args.decisions_file,target_identity([self.refs/'ref.ts']))

    def test_manual_keep_skips_all_models_and_preserves_bytes(self):
        self.store.set(self.source,'keep');factory=Mock(side_effect=AssertionError('model must not run'))
        self.assertEqual(execute(self.args,self.root,factory,Mock()),0)
        factory.assert_not_called()
        self.assertEqual((self.output/'one.ts').read_bytes(),b'original')
        self.assertEqual((self.root/'reports/archives/test/old.ts').read_bytes(),b'previous')
        summary,rows=load_report(self.args.report_dir)
        self.assertEqual(rows[0]['status'],'manual_kept');self.assertEqual(summary['manual_kept'],1)

    def test_all_excluded_leaves_output_untouched_without_models(self):
        self.store.set(self.source,'exclude');factory=Mock(side_effect=AssertionError('model must not run'))
        self.assertEqual(execute(self.args,self.root,factory,Mock()),0)
        self.assertEqual((self.output/'old.ts').read_bytes(),b'previous')
        self.assertFalse((self.root/'reports/archives').exists())
        summary,rows=load_report(self.args.report_dir)
        self.assertEqual(rows,[]);self.assertEqual(summary['skipped_excluded'],1)

    def test_setup_failure_exports_report_before_touching_output(self):
        with self.assertRaisesRegex(RuntimeError,'bad model'):
            execute(self.args,self.root,Mock(side_effect=RuntimeError('bad model')),Mock())
        self.assertEqual((self.output/'old.ts').read_bytes(),b'previous')
        summary,rows=load_report(self.args.report_dir)
        self.assertEqual(summary['status'],'failed');self.assertEqual(summary['fatal_error'],'bad model')
        self.assertEqual(rows,[])

    def test_cancel_during_model_setup_leaves_readable_report(self):
        def factory(args):
            args.stop_file.write_text('stop');return Mock(device='cpu')
        self.assertEqual(execute(self.args,self.root,factory,Mock()),0)
        summary,_=load_report(self.args.report_dir)
        self.assertTrue(summary['cancelled'])
        self.assertTrue((self.output/'old.ts').exists())

    def test_archive_rolls_back_partial_moves(self):
        from shutil import move
        (self.output/'second.ts').write_bytes(b'second')
        calls=0
        def fail_second(source,target):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('disk error')
            return move(source,target)
        with patch('app_common.shutil.move',side_effect=fail_second),self.assertRaises(OSError):
            archive_output(self.output,self.root/'backup')
        self.assertEqual({p.name for p in self.output.iterdir()},{'old.ts','second.ts'})

    def test_failed_load_keeps_current_review_state(self):
        controller=DesktopController(self.root);controller.report=self.root/'previous-report'
        controller.rows=[{'file':'previous.ts'}]
        with self.assertRaises(FileNotFoundError):controller.load(self.root/'missing-report')
        self.assertEqual(controller.report,self.root/'previous-report')
        self.assertEqual(controller.rows,[{'file':'previous.ts'}])

    def test_reader_failure_stops_worker_before_exit_event(self):
        import io
        controller=DesktopController(self.root);controller._active.set()
        process=Mock();process.stdout=io.StringIO('');process.poll.return_value=None
        controller._read(process,self.root/'missing/log.txt',self.root/'stop.flag')
        process.terminate.assert_called_once();process.wait.assert_called_once()
        self.assertFalse(controller.running)
        events=[]
        while not controller.events.empty():events.append(controller.events.get_nowait())
        self.assertEqual(events[-1],{'type':'exit','code':1})

    def test_stop_flag_cleanup_failure_does_not_leave_controller_running(self):
        import io
        controller=DesktopController(self.root);controller._active.set()
        process=Mock();process.stdout=io.StringIO('');process.poll.return_value=0;process.wait.return_value=0
        stop_file=Mock();stop_file.unlink.side_effect=PermissionError('locked flag')
        controller._read(process,self.root/'run.log',stop_file)
        self.assertFalse(controller.running)
        events=[]
        while not controller.events.empty():events.append(controller.events.get_nowait())
        self.assertEqual(events[-1],{'type':'exit','code':0})

    def test_review_uses_custom_decisions_store_from_report(self):
        self.store.set(self.source,'keep')
        execute(self.args,self.root,Mock(),Mock())
        controller=DesktopController(self.root);controller.load(self.args.report_dir)
        controller.review(controller.rows,'exclude')
        self.assertEqual(self.store.get(self.source),'exclude')
        self.assertFalse((self.root/'runtime/review_decisions.json').exists())

    def test_review_checks_conflicting_counterpart_before_copy(self):
        self.store.set(self.source,'keep')
        execute(self.args,self.root,Mock(),Mock())
        controller=DesktopController(self.root);controller.load(self.args.report_dir)
        row=controller.rows[0]
        controller.excluded.mkdir();(controller.excluded/'one.ts').write_bytes(b'different')
        (controller.output/'one.ts').unlink()
        row.update(status='manual_excluded',copied=False,manual_decision='exclude')
        self.store.set(self.source,'exclude')
        with self.assertRaises(ValueError):controller.review([row],'keep')
        self.assertFalse((controller.output/'one.ts').exists())
        self.assertEqual((controller.excluded/'one.ts').read_bytes(),b'different')
        self.assertEqual(self.store.get(self.source),'exclude')
        (controller.excluded/'one.ts').unlink()
        (controller.output/'one.ts').write_bytes(b'different output')
        with self.assertRaises(ValueError):controller.review([row],'exclude')
        self.assertFalse((controller.excluded/'one.ts').exists())
        self.assertEqual((controller.output/'one.ts').read_bytes(),b'different output')


if __name__=='__main__':unittest.main()
