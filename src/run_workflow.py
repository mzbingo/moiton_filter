"""Run orchestration, separate from feature extraction and matching.

The caller owns filter.lock throughout this workflow. Manual decisions are
resolved before model work; reports exist even if setup or decoding fails.
"""
import time
from pathlib import Path
import numpy as np
from app_common import (ReviewStore,archive_output,copy_exact,save_json,source_identity,
    target_identity,validate_excluded_folder,validate_folders)
from clothing import verified_match
from events import StopRequested,check_stop,emit
from run_report import RunReport


def build_reference(args,pipeline,refs,report,describe):
    gallery,info=[],[]
    for number,path in enumerate(refs,1):
        check_stop(args.stop_file)
        emit(args,'phase',message=f'建立参考人物：{number}/{len(refs)} · {path.name}')
        data,folder=pipeline.extract(path,args.dense_fps,True)
        if not len(data['embeddings']):raise RuntimeError(f'No reference person detected: {path.name}')
        data['clothes'],data['valid']=pipeline.clothing.extract(folder,len(data['embeddings']))
        if data['valid'].all(axis=1).sum()<2:
            raise RuntimeError(f'Insufficient clear garment reference crops: {path.name}')
        gallery.append(data)
        evidence=[]
        for index in np.linspace(0,len(data['embeddings'])-1,min(8,len(data['embeddings'])),dtype=int):
            relative=f'evidence/ref_{number}_{path.stem}_{index}.jpg'
            copy_exact(folder/f'{index}.jpg',report/relative)
            evidence.append(relative)
        info.append({'file':path.name,'source_path':str(path.resolve()),
            'detections':len(data['embeddings']),'evidence':evidence})
    reference={key:np.concatenate([data[key] for data in gallery]) for key in ('embeddings','colors','clothes','valid')}
    save_json(report/'reference_profile.json',describe(reference))
    checks=[]
    if len(gallery)>1:
        for index,data in enumerate(gallery):
            check_stop(args.stop_file)
            others={key:np.concatenate([part[key] for j,part in enumerate(gallery) if j!=index]) for key in reference}
            check=verified_match(data,others,data['clothes'],data['valid'],upper=args.upper_color,lower=args.lower_color,reid=args.threshold)
            checks.append({'file':refs[index].name,**check})
    save_json(report/'reference_checks.json',checks)
    return reference,info,checks


def new_result(path):
    return dict(file=path.name,status='error',score=0.,reid_similarity=0.,color_similarity=0.,upper_color=0.,lower_color=0.,
        support_frames=0,time_seconds=0.,track_id=-1,detections=0,copied=False,evidence='',error='',source_path=str(path.resolve()),manual_decision='')


def match_video(args,pipeline,path,reference,report,row):
    data,folder=pipeline.extract(path,args.fps)
    decision=pipeline.verify(data,folder,reference)
    if not decision['passed'] and args.dense_fps>args.fps:
        data,folder=pipeline.extract(path,args.dense_fps)
        decision=pipeline.verify(data,folder,reference)
    index=decision['index']
    row.update(score=decision['score'],reid_similarity=decision['score'],upper_color=decision['upper'],
        lower_color=decision['lower'],support_frames=decision['frames'],detections=len(data['embeddings']))
    row['status']='match' if decision['passed'] else 'review' if decision.get('uncertain',False) or decision.get('single_frame',False) else 'no_match' if index>=0 else 'no_person'
    if index>=0:
        row.update(time_seconds=float(data['times'][index]),track_id=int(data['tracks'][index]))
        evidence=f'evidence/{path.name}.jpg'
        copy_exact(folder/f'{index}.jpg',report/evidence)
        row['evidence']=evidence
    return decision['passed']


def execute(args,root,pipeline_factory,describe,device_name=None):
    check_stop(args.stop_file)
    refs,all_candidates=validate_folders(args.reference_dir,args.input_dir,args.output_dir)
    excluded=validate_excluded_folder(args.excluded_dir,args.reference_dir,args.input_dir,args.output_dir)
    identity=target_identity(refs)
    decisions_file=(args.decisions_file or root/'runtime/review_decisions.json').resolve()
    decisions=ReviewStore(decisions_file,identity).snapshot()
    candidates=[(path,decisions.get(source_identity(path))) for path in all_candidates]
    skipped=sum(decision=='exclude' for _,decision in candidates)
    candidates=[item for item in candidates if item[1]!='exclude']
    if args.limit:candidates=candidates[:args.limit]
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=True)
    report=args.report_dir or root/'reports'/f'{time.strftime("%Y%m%d_%H%M%S")}_{time.time_ns()%1000000000:09d}'
    report.mkdir(parents=True);(report/'evidence').mkdir()
    summary={'settings':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},'device':'not_required',
        'report':str(report.resolve()),'reference_dir':str(args.reference_dir.resolve()),'input_dir':str(args.input_dir.resolve()),
        'output_dir':str(output),'excluded_dir':str(excluded),'decisions_file':str(decisions_file),
        'target_id':identity,'skipped_excluded':skipped,
        'references':[],'reference_crops':0,'reference_checks':[],'total':len(candidates),'manual_kept':0,
        'processed':0,'copied':0,'errors':0,'cancelled':False}
    writer=RunReport(report,summary)
    emit(args,'started',report=str(report.resolve()),total=len(candidates),output=str(output),skipped_excluded=skipped)
    status='completed'
    try:
        automatic=any(decision!='keep' for _,decision in candidates)
        pipeline,reference=None,None
        if automatic:
            emit(args,'phase',message='正在加载人物检测与衣着匹配模型')
            pipeline=pipeline_factory(args)
            check_stop(args.stop_file)
            summary['device']=pipeline.device
            emit(args,'device',device=pipeline.device,name=device_name(pipeline) if device_name else pipeline.device)
            reference,summary['references'],summary['reference_checks']=build_reference(args,pipeline,refs,report,describe)
            summary['reference_crops']=len(reference['embeddings'])
        check_stop(args.stop_file)
        # Archiving now occurs under the worker's lock, after reference setup succeeds.
        if candidates and getattr(args,'archive_output',False):
            backup=root/'reports/archives'/report.name
            count=archive_output(output,backup)
            if count:
                summary['backup_dir']=str(backup)
                emit(args,'backup',path=str(backup),count=count)
        for number,(path,manual) in enumerate(candidates,1):
            check_stop(args.stop_file)
            emit(args,'phase',message=f'筛选 {number}/{len(candidates)} · {path.name}')
            row=new_result(path)
            row['manual_decision']=manual or ''
            try:
                if manual=='keep':
                    row['status']='manual_kept'
                    passed=True
                else:passed=match_video(args,pipeline,path,reference,report,row)
                check_stop(args.stop_file)
                if passed:
                    copy_exact(path,output/path.name);row['copied']=True
                if manual=='keep':summary['manual_kept']+=1
            except StopRequested:raise
            except Exception as exc:row.update(status='error',error=str(exc))
            writer.append(row)
            emit(args,'result',row=row,processed=summary['processed'],total=len(candidates),copied=summary['copied'],errors=summary['errors'])
            print(f'[{number}/{len(candidates)}] {path.name}: {row["status"]} {row["score"]:.3f} {row["error"]}',flush=True)
    except StopRequested:
        status='cancelled'
    except Exception as exc:
        writer.finalize('failed',fatal_error=str(exc))
        raise
    writer.finalize(status)
    if not args.no_latest and (summary['processed'] or status=='completed'):
        pointer=root/'reports/latest.txt';temporary=pointer.with_suffix('.tmp')
        temporary.write_text(str(report.resolve()),encoding='utf-8');temporary.replace(pointer)
    emit(args,'finished',**summary)
    return 2 if summary['errors'] else 0
