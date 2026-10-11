"""Shared desktop/worker operations. Standard library only; no model imports."""
import csv
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

VIDEO_EXTENSIONS = {'.ts','.mp4','.avi','.mov','.mkv'}


def app_root():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]


def video_files(folder):
    return sorted(p for p in Path(folder).rglob('*') if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def file_identity(path):
    path=Path(path).resolve()
    stat=path.stat()
    return [os.path.normcase(str(path)),stat.st_size,stat.st_mtime_ns]


def target_identity(references):
    return hashlib.sha256(json.dumps(sorted(file_identity(p) for p in references)).encode()).hexdigest()


def source_identity(path):
    return hashlib.sha256(json.dumps(file_identity(path)).encode()).hexdigest()


def save_json(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


class ReviewStore:
    def __init__(self,path,target_id):
        self.path,self.target_id=Path(path),target_id

    def _load(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding='utf-8'))

    def get(self,source):
        return self._load().get(self.target_id,{}).get(source_identity(source),{}).get('decision')

    def set(self,source,decision):
        if decision not in {'keep','exclude'}:
            raise ValueError('Invalid review decision')
        data=self._load()
        data.setdefault(self.target_id,{})[source_identity(source)]={
            'source':str(Path(source).resolve()),'decision':decision,'updated':time.strftime('%Y-%m-%d %H:%M:%S')}
        save_json(self.path,data)

    def excluded_sources(self):
        return {key for key,value in self._load().get(self.target_id,{}).items()
                if value.get('decision')=='exclude'}


def pending_videos(candidates,store):
    excluded=store.excluded_sources() if store else set()
    pending=[p for p in candidates if source_identity(p) not in excluded]
    return pending,len(candidates)-len(pending)


def validate_excluded_folder(excluded,*folders):
    excluded=Path(excluded).resolve()
    for folder in folders:
        folder=Path(folder).resolve()
        if excluded==folder or excluded.is_relative_to(folder) or folder.is_relative_to(excluded):
            raise ValueError('手动排除目录必须与参考、待筛选、输出目录分开，不能互相包含')
    return excluded


def copy_exact(source,destination):
    source,destination=Path(source),Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if source.resolve()==destination.resolve():
        raise ValueError('输出目录不能是原视频目录')
    if destination.exists():
        if file_digest(source)!=file_digest(destination):
            raise ValueError(f'输出已有同名不同内容的文件，未覆盖：{destination.name}')
        return
    partial=destination.with_name(destination.name+'.partial')
    shutil.copy2(source,partial)
    partial.replace(destination)


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def remove_from_output(source,output,backup):
    source,output,backup=Path(source),Path(output),Path(backup)
    if not output.exists():
        return None
    if source.resolve()==output.resolve() or file_digest(source)!=file_digest(output):
        raise ValueError('输出文件与原视频不一致，未移动，请手动检查')
    backup.mkdir(parents=True,exist_ok=True)
    destination=backup/output.name
    if destination.exists():
        destination=backup/f'{output.stem}_{time.time_ns()}{output.suffix}'
    shutil.move(str(output),str(destination))
    return destination


def validate_folders(reference,source,output):
    reference,source,output=map(lambda p:Path(p).resolve(),(reference,source,output))
    for folder in (reference,source):
        if not folder.is_dir():
            raise ValueError(f'文件夹不存在：{folder}')
    for folder in (reference,source):
        if output==folder or output.is_relative_to(folder) or folder.is_relative_to(output):
            raise ValueError('输出目录必须与参考、待筛选目录分开，不能互相包含')
    refs,candidates=video_files(reference),video_files(source)
    if not refs or not candidates:
        raise ValueError('参考目录和待筛选目录中都需要有视频')
    if len({p.name.casefold() for p in candidates})!=len(candidates):
        raise ValueError('待筛选目录包含同名视频，请先避免文件名冲突')
    return refs,candidates


def load_report(report):
    report=Path(report)
    summary=json.loads((report/'summary.json').read_text(encoding='utf-8'))
    with (report/'results.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.DictReader(stream))
    for row in rows:
        row['copied']=str(row.get('copied','')).lower()=='true'
        for key in ('score','upper_color','lower_color','time_seconds'):
            row[key]=float(row.get(key) or 0)
        row['support_frames']=int(row.get('support_frames') or 0)
    return summary,rows


import html

def persist_report(rows, report, summary):
    keys = ['file','status','score','reid_similarity','color_similarity','upper_color','lower_color','support_frames','time_seconds','track_id','detections','copied','evidence','error','source_path','manual_decision']
    with (report/'results.csv.tmp').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    (report/'results.csv.tmp').replace(report/'results.csv')
    save_json(report/'summary.json',summary)
    cards = []
    for row in sorted(rows, key=lambda r: (not r['copied'], -r['score'])):
        image = f'<img loading="lazy" src="{html.escape(row["evidence"])}">' if row['evidence'] else ''
        if row['copied']:
            video_url=(Path(summary.get('output_dir',app_root()/'output'))/row['file']).resolve().as_uri()
        else:
            video_url=Path(row.get('source_path') or app_root()/'input/waiting_for_filter_videos'/row['file']).resolve().as_uri()
        cards.append(f'<article>{image}<b>{html.escape(row["file"])}</b><p>{row["status"]} | ReID {row["score"]:.3f} | {row["time_seconds"]:.2f}s</p><p>上装 {row["upper_color"]:.2f} / 下装 {row["lower_color"]:.2f} / 支持帧 {row["support_frames"]}</p><a href="{video_url}">原视频文件</a><p>{html.escape(row["error"])}</p></article>')
    (report/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>人物筛选复核</title><style>body{font-family:system-ui;background:#eee;padding:20px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}article{background:white;padding:12px;overflow-wrap:anywhere}img{height:240px;max-width:100%;object-fit:contain;display:block}</style><h1>人物筛选复核</h1><p>match 和 manual_kept 为保留结果；review 需要人工确认；manual_excluded 为手动排除；error 为处理错误。评分不是身份概率。</p><p><a href="reference_profile.json">参考人物颜色特征</a> · <a href="reference_checks.json">参考一致性检查</a></p><div class="grid">'+''.join(cards)+'</div>', encoding='utf-8')
