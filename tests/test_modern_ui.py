"""Qt state and responsiveness regressions, without using the user's videos."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtWidgets import QApplication
from modern_app import ModernWindow
from app_common import persist_report


class ModernUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.window=ModernWindow(Path(self.temp.name))
        self.window.timer.stop()
        self.window.error=Mock()
        self.window.controller.report=Path(self.temp.name)/'report'
        self.window.controller.rows=[{
            'file':'sample.ts','source_path':str(Path(self.temp.name)/'sample.ts'),
            'status':'review','copied':False,'score':.8,'time_seconds':1.,
            'upper_color':.8,'lower_color':.8,'support_frames':2,'evidence':''}]
        self.window.set_filter('待确认');self.window.table.selectRow(0)

    def tearDown(self):
        self.window.close();self.application.processEvents();self.temp.cleanup()

    def test_settings_error_does_not_launch_worker(self):
        with patch.object(self.window,'save_settings',side_effect=OSError('disk full')):
            with patch.object(self.window.controller,'start') as start:
                self.window.start()
                start.assert_not_called()
        self.assertTrue(self.window.start_button.isEnabled())

    def test_empty_history_clears_previous_video(self):
        self.assertIsNotNone(self.window.preview_source)
        self.window.controller.rows=[];self.window.loaded()
        self.assertIsNone(self.window.preview_source)
        self.assertIsNone(self.window.selected_key)

    def test_filtered_out_selection_clears_preview(self):
        self.window.set_filter('已保留')
        self.assertIsNone(self.window.preview_source)
        self.assertEqual(self.window.table.rowCount(),0)

    def test_failure_report_and_reason_replace_previous_history(self):
        report=Path(self.temp.name)/'failed_run';report.mkdir()
        reason='参考视频没有清晰的人物上装和下装'
        persist_report([],report,{'status':'failed','fatal_error':reason,'target_id':'target'})
        self.window.report_label.setText('previous-report')
        self.window.controller.report=report;self.window.controller.rows=[]
        self.window.clear_preview();self.window.set_running(True)
        self.window.controller.events.put({'type':'started','total':2,'report':str(report)})
        self.window.poll()
        self.assertEqual(self.window.report_label.text(),str(report))
        # Model/reference setup failure emits fatal + exit, without finished.
        self.window.report_label.setText('previous-report')
        self.window.controller.events.put({'type':'fatal','message':reason})
        self.window.controller.events.put({'type':'exit','code':1})
        self.window.poll()
        self.assertEqual(self.window.report_label.text(),str(report))
        self.assertIn(reason,self.window.status.text())
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertIsNone(self.window.preview_source)
        self.assertEqual(self.window.table.rowCount(),0)

    def test_reference_preview_uses_report_target_not_next_task_settings(self):
        previous=Path(self.temp.name)/'previous_target'
        upcoming=Path(self.temp.name)/'next_target'
        self.window.controller.summary={'reference_dir':str(previous)}
        self.window.paths['reference'].setText(str(upcoming))
        with patch('modern_app.video_files',return_value=[]) as videos:
            with patch('modern_app.QMessageBox.information'):
                self.window.show_references()
        videos.assert_called_once_with(previous)
        self.assertEqual(self.window.paths['reference'].text(),str(upcoming))

    def test_reference_images_keep_same_stem_sources_separate(self):
        first=Path(self.temp.name)/'camera_a/clip.ts'
        second=Path(self.temp.name)/'camera_b/clip.mp4'
        report=self.window.controller.report
        (report/'evidence').mkdir(parents=True)
        names=['evidence/ref_1_clip_0.jpg','evidence/ref_2_clip_0.jpg','evidence/ref_clip_0.jpg']
        for name in names:(report/name).write_bytes(b'fixture')
        self.window.controller.summary={'references':[
            {'file':first.name,'source_path':str(first),'evidence':[names[0]]},
            {'file':second.name,'source_path':str(second),'evidence':[names[1]]}]}
        self.assertEqual(self.window.reference_images(first),[report/names[0]])
        self.assertEqual(self.window.reference_images(second),[report/names[1]])
        self.assertEqual(self.window.reference_images(Path(self.temp.name)/'other/clip.ts'),[])
        self.window.controller.summary={'references':[{'file':first.name,'detections':1}]}
        self.assertEqual(self.window.reference_images(first),[report/names[2]])

    def test_review_runs_off_ui_thread_and_blocks_conflicting_actions(self):
        entered=threading.Event();release=threading.Event();thread_ids=[]
        def review(rows,decision):
            thread_ids.append(threading.get_ident());entered.set()
            if not release.wait(3):raise TimeoutError('test review wait')
            rows[0].update(status='manual_kept',copied=True)
            return [rows[0]['file']]
        with patch.object(self.window.controller,'review',side_effect=review):
            self.window.review('keep')
            self.assertTrue(entered.wait(1))
            try:
                self.assertTrue(self.window.review_busy)
                self.assertFalse(self.window.start_button.isEnabled())
                self.assertNotEqual(thread_ids[0],threading.get_ident())
                self.window.navigate(3);self.application.processEvents()
                self.assertEqual(self.window.stack.currentIndex(),3)
                with patch.object(self.window.controller,'start') as start:
                    self.window.start();start.assert_not_called()
            finally:release.set()
            deadline=time.monotonic()+3
            while self.window.review_busy and time.monotonic()<deadline:
                self.window.poll();self.application.processEvents();time.sleep(.005)
        self.assertFalse(self.window.review_busy)
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertEqual(self.window.table.rowCount(),0)
        self.window.error.assert_not_called()


if __name__=='__main__':unittest.main()
