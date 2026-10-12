"""Opt-in GPU and preview through the actual modern Qt event loop."""
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtWidgets import QApplication
from app_common import app_root,video_files,save_json
from modern_app import ModernWindow

application=QApplication(sys.argv);application.setStyle('Fusion')
root=app_root();window=ModernWindow(root);window.show();application.processEvents()
window.controller.start(root/'input/reference_video',root/'input/waiting_for_filter_videos',
    root/'runtime/modern_test_output',limit=2,archive=False,no_latest=True)
window.set_running(True);window.set_filter('全部')
deadline=time.monotonic()+120
while time.monotonic()<deadline:
    application.processEvents()
    if not window.controller.running and window.controller.events.empty() and window.start_button.isEnabled():break
    time.sleep(.025)
else:window.controller.stop();raise AssertionError('Qt worker timeout')
assert len(window.controller.rows)==2
assert window.table.rowCount()==2
assert window.keep_button.isEnabled()
assert not any(r['status']=='error' for r in window.controller.rows)
assert 'RTX' in window.device.text()
window.show_video(video_files(root/'input/reference_video')[0],position=1.)
window.toggle_play();deadline=time.monotonic()+20
while time.monotonic()<deadline:
    application.processEvents()
    if window.preview.original is not None:break
    time.sleep(.025)
else:raise AssertionError('Qt video preview timeout')
assert not window.preview.original.isNull()
window.toggle_play();assert not window.playing
save_json(root/'runtime/modern_integration.json',{'ok':True,'processed':len(window.controller.rows),'preview':True,'device':window.device.text()})
window.close();application.processEvents()
print('Modern Qt GPU progress, completion and video play/pause: PASS')
