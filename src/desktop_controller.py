"""Worker supervision and safe manual review for the native desktop UI."""
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path
from filelock import FileLock,Timeout
from app_common import (ReviewStore,app_root,copy_exact,load_report,remove_from_output,
                        save_json,persist_report,target_identity,validate_folders,video_files,
                        pending_videos,validate_excluded_folder,validate_review_copy)

HIDDEN=getattr(subprocess,'CREATE_NO_WINDOW',0)


class DesktopController:
    def __init__(self,root=None):
        self.root=Path(root or app_root())
        self.runtime=self.root/'runtime'
        self.runtime.mkdir(parents=True,exist_ok=True)
        self.events=queue.Queue()
        self.process=None
        self.thread=None
        self._active=threading.Event()
        self.report=None
        self.output=None
        self.excluded=None
        self.stop_file=None
        self.log_file=None
        self.rows=[]
        self.summary={}
        self.store=None

    @property
    def running(self):
        return self._active.is_set()

    def load(self,report):
        if self.running:raise ValueError('任务进行中，不能切换报告')
        report=Path(report)
        summary,rows=load_report(report)
        output=Path(summary.get('output_dir',self.root/'output'))
        excluded=Path(summary.get('excluded_dir',self.root/'manual_excluded'))
        source=Path(summary.get('input_dir',self.root/'input/waiting_for_filter_videos'))
        references=Path(summary.get('reference_dir',self.root/'input/reference_video'))
        identity=summary.get('target_id')
        if not identity and references.is_dir():
            identity=target_identity(video_files(references))
        decisions_file=(summary.get('decisions_file') or summary.get('settings',{}).get('decisions_file')
            or self.runtime/'review_decisions.json')
        store=ReviewStore(decisions_file,identity) if identity else None
        manual_file=report/'manual_review.json'
        manual=json.loads(manual_file.read_text(encoding='utf-8')) if manual_file.exists() else {}
        for row in rows:
            row['source_path']=row.get('source_path') or str(source/row['file'])
            if row['file'] in manual:
                row.update(manual[row['file']])
            # Actual files determine whether a result is still in the output folder.
            row['copied']=bool(row['copied'] and (output/row['file']).exists())
        summary['copied']=sum(row['copied'] for row in rows)
        self.report,self.summary,self.rows=report,summary,rows
        self.output,self.excluded,self.store=output,excluded,store
        return self.summary,self.rows

    def latest(self):
        pointer=self.root/'reports/latest.txt'
        if pointer.exists():
            report=Path(pointer.read_text(encoding='utf-8').strip())
            if (report/'results.csv').exists():
                return self.load(report)
        return None

    def start(self,reference,source,output,threshold=.72,refresh=False,archive=True,limit=0,no_latest=False,excluded=None):
        if self.running:
            raise ValueError('已有任务正在运行')
        refs,candidates=validate_folders(reference,source,output)
        excluded=validate_excluded_folder(excluded or self.root/'manual_excluded',reference,source,output)
        candidates,skipped=pending_videos(candidates,ReviewStore(self.runtime/'review_decisions.json',target_identity(refs)))
        python=self.root/'.venv/Scripts/python.exe'
        backend=self.root/'src/filter_videos.py'
        if not python.exists() or not backend.exists():
            raise ValueError('运行环境不完整。请将 EXE 保留在项目目录，并先运行 setup.ps1')
        token=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
        report=self.root/'reports'/token
        self.stop_file=self.runtime/f'stop_{token}.flag'
        self.log_file=self.runtime/f'run_{token}.log'
        output=Path(output).resolve()
        lock=FileLock(self.runtime/'filter.lock',timeout=0)
        try:
            with lock:
                output.mkdir(parents=True,exist_ok=True)
                args=[str(python),'-u',str(backend),'--reference-dir',str(Path(reference).resolve()),
                    '--input-dir',str(Path(source).resolve()),'--output-dir',str(output),
                    '--report-dir',str(report),'--stop-file',str(self.stop_file),
                    '--decisions-file',str(self.runtime/'review_decisions.json'),'--events',
                    '--threshold',str(threshold),'--excluded-dir',str(excluded)]
                if refresh:args.append('--refresh')
                if archive:args.append('--archive-output')
                if limit:args.extend(['--limit',str(limit)])
                if no_latest:args.append('--no-latest')
                environment=os.environ.copy()
                environment.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
                self.process=subprocess.Popen(args,cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',bufsize=1,
                    creationflags=HIDDEN,env=environment)
        except Timeout:
            raise ValueError('另一项筛选正在运行，请先停止或等待完成') from None
        self.report,self.output=report,output
        self.excluded=excluded
        self.summary={'input_dir':str(Path(source).resolve()),'reference_dir':str(Path(reference).resolve()),
            'output_dir':str(output),'excluded_dir':str(excluded),'skipped_excluded':skipped,
            'target_id':target_identity(refs),'total':min(limit,len(candidates)) if limit else len(candidates)}
        self.store=ReviewStore(self.runtime/'review_decisions.json',self.summary['target_id'])
        self.rows=[]
        self._active.set()
        self.thread=threading.Thread(target=self._read,args=(self.process,self.log_file,self.stop_file),daemon=True)
        self.thread.start()

    def _read(self,process,log_file,stop_file):
        code=1
        try:
            with log_file.open('w',encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if line.startswith('@event '):
                        try:self.events.put(json.loads(line[7:]))
                        except json.JSONDecodeError:self.events.put({'type':'log','message':line.strip()})
                    else:self.events.put({'type':'log','message':line.strip()})
            code=process.wait()
        except Exception as exc:
            self.events.put({'type':'fatal','message':str(exc)})
        finally:
            # No orphan worker may continue writing after the UI enables actions.
            try:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
            except OSError as exc:
                code=1
                self.events.put({'type':'fatal','message':str(exc)})
            finally:
                try:
                    if process.stdout:process.stdout.close()
                    if stop_file:stop_file.unlink(missing_ok=True)
                except OSError as exc:
                    self.events.put({'type':'log','message':f'清理临时文件失败：{exc}'})
                finally:
                    self._active.clear()
                    self.events.put({'type':'exit','code':code})

    def stop(self):
        if self.running and self.stop_file:
            self.stop_file.write_text('stop',encoding='ascii')

    def review(self,rows,decision):
        if self.running:
            raise ValueError('请在筛选停止或完成后复核')
        if not self.report or not self.output or not self.store:
            raise ValueError('请先打开一份筛选结果')
        changes={}
        manual_file=self.report/'manual_review.json'
        if manual_file.exists():changes=json.loads(manual_file.read_text(encoding='utf-8'))
        completed=[]
        try:
            with FileLock(self.runtime/'filter.lock',timeout=0):
                excluded=validate_excluded_folder(self.excluded or self.root/'manual_excluded',
                    self.summary.get('reference_dir',self.root/'input/reference_video'),
                    self.summary.get('input_dir',self.root/'input/waiting_for_filter_videos'),self.output)
                try:
                    for row in rows:
                        source=Path(row['source_path'])
                        if decision=='keep':
                            validate_review_copy(source,excluded/row['file'])
                            copy_exact(source,self.output/row['file'])
                            remove_from_output(source,excluded/row['file'],self.report/'restored_from_excluded')
                            status,copied='manual_kept',True
                        elif decision=='exclude':
                            validate_review_copy(source,self.output/row['file'])
                            copy_exact(source,excluded/row['file'])
                            remove_from_output(source,self.output/row['file'],self.report/'removed_from_output')
                            status,copied='manual_excluded',False
                        else:raise ValueError('Unknown review action')
                        self.store.set(source,decision)
                        patch={'status':status,'copied':copied,'manual_decision':decision}
                        self.summary['excluded_dir']=str(excluded)
                        changes[row['file']]=patch
                        row.update(patch)
                        completed.append(row['file'])
                        save_json(manual_file,changes)
                finally:
                    if completed:
                        self.summary.update(processed=len(self.rows),copied=sum(r['copied'] for r in self.rows),errors=sum(r['status']=='error' for r in self.rows))
                        persist_report(self.rows,self.report,self.summary)
        except Timeout:
            raise ValueError('筛选任务正在运行，暂时不能修改输出') from None
        return completed
