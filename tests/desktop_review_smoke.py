"""Exercise actual Tk buttons against disposable files, without changing real videos."""
import sys
import tempfile
import tkinter as tk
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from app_common import persist_report,pending_videos
from desktop_app import App

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
    window=tk.Tk();app=App(window,root);window.update()
    window.minsize(1000,640)
    window.geometry('1100x700');window.update()
    for button in (app.keep_button,app.exclude_button):
        assert button.winfo_ismapped()
        assert button.winfo_rooty()+button.winfo_height()<window.winfo_rooty()+window.winfo_height()
        assert button.winfo_rooty()<app.table.winfo_rooty()
    app.filter('待确认');window.update()
    app.table.selection_set(app.table.get_children()[0]);window.update()
    assert str(app.keep_button['state'])!='disabled'
    app.keep_button.invoke();window.update()
    assert (root/'output/candidate.ts').read_bytes()==b'original'
    app.filter('已保留');window.update()
    app.table.selection_set(app.table.get_children()[0]);window.update()
    app.exclude_button.invoke();window.update()
    assert (root/'manual_excluded/candidate.ts').read_bytes()==b'original'
    assert not (root/'output/candidate.ts').exists()
    assert pending_videos([source],app.controller.store)==([],1)
    assert source.read_bytes()==b'original'
    app.close()
print('GUI review buttons, exclusion folder and rescan skipping: PASS')
