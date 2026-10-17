"""Decoder lifecycle failures must not masquerade as successful playback."""
import io
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from video_preview import VideoPreview


class Process:
    def __init__(self,code=0):
        self.stdout=io.BytesIO();self.stderr=None;self.returncode=code;self.terminated=False
    def poll(self):return self.returncode
    def wait(self,timeout=None):return self.returncode
    def terminate(self):self.terminated=True;self.returncode=-1
    def kill(self):self.terminate()
    def communicate(self,timeout=None):return json.dumps({'format':{'duration':2}}).encode(),b''


class VideoPreviewTests(unittest.TestCase):
    def test_decoder_failure_is_error_and_process_is_released(self):
        probe,decoder=Process(),Process(7)
        preview=VideoPreview()
        with patch('video_preview.shutil.which',side_effect=lambda name:name):
            with patch('video_preview.subprocess.Popen',side_effect=[probe,decoder]):
                preview._read('test.ts',0,0)
        events=list(preview.events.queue)
        self.assertEqual([e['type'] for e in events],['duration','error'])
        self.assertIn('7',events[-1]['message'])
        self.assertIsNone(preview.process)
        self.assertTrue(decoder.stdout.closed)

    def test_empty_video_is_not_success(self):
        preview=VideoPreview()
        with patch('video_preview.shutil.which',side_effect=lambda name:name):
            with patch('video_preview.subprocess.Popen',side_effect=[Process(),Process()]):
                preview._read('test.ts',0,0)
        self.assertEqual(list(preview.events.queue)[-1]['type'],'error')

    def test_stop_terminates_probe_and_suppresses_obsolete_events(self):
        entered=threading.Event();release=threading.Event()
        class SlowProbe(Process):
            def __init__(self):super().__init__(None)
            def communicate(self,timeout=None):
                entered.set();release.wait(2)
                return b'{}',b''
            def terminate(self):super().terminate();release.set()
        probe=SlowProbe();preview=VideoPreview()
        with patch('video_preview.shutil.which',side_effect=lambda name:name):
            with patch('video_preview.subprocess.Popen',return_value=probe):
                preview.play('test.ts');self.assertTrue(entered.wait(1))
                preview.stop();preview.thread.join(2)
        self.assertFalse(preview.thread.is_alive())
        self.assertTrue(probe.terminated)
        self.assertTrue(preview.events.empty())
        self.assertIsNone(preview.process)


if __name__=='__main__':unittest.main()
