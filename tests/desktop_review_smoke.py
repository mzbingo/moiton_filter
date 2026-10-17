"""Actual Qt controls on disposable files; no production videos changed."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint
from app_common import persist_report,pending_videos
from modern_app import ModernWindow
application=QApplication.instance() or QApplication(sys.argv)
application.setStyle('Fusion')
def wait_until(predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        application.processEvents()
        if predicate():return
        time.sleep(.01)
    raise AssertionError('Qt action timed out')

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);inputs=root/'input/waiting_for_filter_videos';inputs.mkdir(parents=True)
    refs=root/'input/reference_video';refs.mkdir();(refs/'ref.ts').write_bytes(b'reference')
    source=inputs/'candidate.ts';source.write_bytes(b'original')
    report=root/'reports/run';report.mkdir(parents=True)
    row=dict(file=source.name,status='review',score=.7,reid_similarity=.7,color_similarity=0.,
        upper_color=.6,lower_color=.7,support_frames=1,time_seconds=2.,track_id=1,detections=2,
        copied=False,evidence='',error='',source_path=str(source),manual_decision='')
    summary=dict(input_dir=str(inputs),reference_dir=str(refs),output_dir=str(root/'output'),target_id='target')
    persist_report([row],report,summary)
    (root/'reports/latest.txt').write_text(str(report),encoding='utf-8')
    window=ModernWindow(root);window.resize(1100,700);window.show();application.processEvents()
    for control in (window.keep_button,window.exclude_button):
        assert control.isVisible()
        bottom=control.mapTo(window,QPoint(control.width(),control.height()))
        assert bottom.x()<window.width() and bottom.y()<window.height()
        assert control.mapTo(window,QPoint(0,0)).y()<window.table.mapTo(window,QPoint(0,0)).y()
    for index in range(4):
        window.navigate(index);application.processEvents();assert window.stack.currentIndex()==index
    window.navigate(0)
    pending=list(window.filters)[1];kept=list(window.filters)[0];excluded=list(window.filters)[4]
    window.set_filter(pending);application.processEvents();assert window.table.rowCount()==1
    window.table.selectRow(0);application.processEvents();assert window.keep_button.isEnabled()
    window.keep_button.click();wait_until(lambda:not window.review_busy)
    assert (root/'output/candidate.ts').read_bytes()==b'original'
    window.set_filter(kept);window.table.selectRow(0);application.processEvents()
    window.exclude_button.click();wait_until(lambda:not window.review_busy)
    assert (root/'manual_excluded/candidate.ts').read_bytes()==b'original'
    assert not (root/'output/candidate.ts').exists()
    assert pending_videos([source],window.controller.store)==([],1)
    window.set_filter(excluded);assert window.table.rowCount()==1
    window.table.selectRow(0);window.keep_button.click();wait_until(lambda:not window.review_busy)
    assert (root/'output/candidate.ts').read_bytes()==b'original'
    assert not (root/'manual_excluded/candidate.ts').exists()
    window.search.setText('not-found');wait_until(lambda:window.table.rowCount()==0)
    assert window.empty.isVisible()
    window.search.clear();window.set_filter(kept);assert window.table.rowCount()==1
    window.set_running(True);assert not window.keep_button.isEnabled() and not window.start_button.isEnabled()
    window.set_running(False);assert window.keep_button.isEnabled()
    assert source.read_bytes()==b'original'
    window.close();application.processEvents()
print('Modern GUI small window, navigation, review, exclusion, restore, search, task state: PASS')
