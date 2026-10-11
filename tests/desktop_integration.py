"""Opt-in real GPU/FFmpeg test; never changes production output/latest pointer."""
import json
import queue
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from app_common import app_root,file_digest,video_files,load_report,save_json
from desktop_controller import DesktopController
from video_preview import VideoPreview,WIDTH,HEIGHT

root=app_root()
controller=DesktopController(root)
output=root/'runtime/integration_output'
controller.start(root/'input/reference_video',root/'input/waiting_for_filter_videos',output,
    limit=2,archive=False,no_latest=True)
events=[];deadline=time.monotonic()+180
while time.monotonic()<deadline:
    event=controller.events.get(timeout=60);events.append(event)
    if event['type']=='exit':break
else:controller.stop();raise AssertionError('worker timeout')
assert events[-1]['code']==0,events[-1]
summary,rows=load_report(controller.report)
assert len(rows)==2 and summary['errors']==0
assert any(e['type']=='device' for e in events)
assert sum(e['type']=='result' for e in events)==2
for row in rows:
    if row['copied']:assert file_digest(output/row['file'])==file_digest(row['source_path'])
player=VideoPreview();source=video_files(root/'input/reference_video')[0]
before=file_digest(source);player.play(source,1.)
generation,data,position=player.frames.get(timeout=20)
assert len(data)==WIDTH*HEIGHT*3 and position>=1.
player.stop();player.thread.join(timeout=10)
assert not player.thread.is_alive() and file_digest(source)==before
controller.start(root/'input/reference_video',root/'input/waiting_for_filter_videos',output,
    limit=2,archive=False,no_latest=True)
controller.stop();cancelled=[]
while True:
    event=controller.events.get(timeout=60);cancelled.append(event)
    if event['type']=='exit':break
assert cancelled[-1]['code']==0
assert any(e['type']=='cancelled' or (e['type']=='finished' and e.get('cancelled')) for e in cancelled)
save_json(root/'runtime/integration_test.json',{'ok':True,'processed':len(rows),'report':str(summary['report']),
    'gpu':summary['device'],'preview_frame_bytes':len(data),'cancel_ok':True})
print('GPU worker, exact copy, FFmpeg preview and cancellation: PASS')
