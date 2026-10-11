"""Local single-outfit person retrieval. Originals are never modified."""
import argparse
import csv
import hashlib
import html
import json
import shutil
import subprocess
import time
from urllib.parse import quote
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision import transforms
from ultralytics import YOLO
from vendor.osnet import osnet_x1_0
from clothing import ClothingExtractor, verified_match, COLOR_NAMES
from app_common import ReviewStore,validate_folders,save_json
from events import StopRequested,check_stop,emit
from filelock import FileLock,Timeout

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {'.ts', '.mp4', '.avi', '.mov', '.mkv'}
PIPELINE_VERSION = 3


def files(folder):
    return sorted(p for p in folder.rglob('*') if p.suffix.lower() in EXTENSIONS and p.is_file())


def fingerprint(path):
    st = path.stat()
    return [str(path.resolve()), st.st_size, st.st_mtime_ns]


def colors(crop):
    """Separate torso/legs histograms; background and accessories can affect these."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, w = hsv.shape[:2]
    parts = []
    for start, end in ((.22, .55), (.55, .90)):
        part = hsv[int(h*start):max(int(h*end), int(h*start)+1), int(w*.2):max(int(w*.8), int(w*.2)+1)]
        hist = cv2.calcHist([part], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
        hist = np.sqrt(hist / max(hist.sum(), 1))
        parts.append(hist)
    return np.concatenate(parts) / np.sqrt(2)


class Pipeline:
    def __init__(self, args):
        self.args = args
        self.device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
        print(f'Device: {self.device}', flush=True)
        self.detector = YOLO(str(ROOT / 'models/yolo11s.pt'))
        self.reid = osnet_x1_0(num_classes=1, pretrained=False)
        state = torch.load(ROOT / 'models/osnet_msmt17.pth', map_location='cpu', weights_only=True)
        state = state.get('state_dict', state)
        state = {k.removeprefix('module.'): v for k, v in state.items()}
        state = {k: v for k, v in state.items() if not k.startswith('classifier.')}
        missing, unexpected = self.reid.load_state_dict(state, strict=False)
        if unexpected or any(not k.startswith('classifier.') for k in missing):
            raise RuntimeError(f'Incompatible ReID weights: {missing}, {unexpected}')
        self.reid.to(self.device).eval()
        self.clothing = ClothingExtractor(YOLO(str(ROOT/'models/yolo11s-pose.pt')),self.device,refresh=args.refresh,
            stop_check=lambda:check_stop(getattr(args,'stop_file',None)))
        self.transform = transforms.Compose([transforms.Resize((256, 128)), transforms.ToTensor(),
            transforms.Normalize([.485, .456, .406], [.229, .224, .225])])

    @torch.inference_mode()
    def features(self, crops):
        tensors = torch.stack([self.transform(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))) for c in crops]).to(self.device)
        # Flip averaging reduces sensitivity to walking direction.
        features = self.reid(tensors) + self.reid(torch.flip(tensors, [3]))
        return torch.nn.functional.normalize(features, dim=1).cpu().numpy()

    def extract(self, path, rate, reference=False):
        check_stop(getattr(self.args,'stop_file',None))
        signature = [PIPELINE_VERSION, fingerprint(path), rate, reference, self.args.imgsz,
                     fingerprint(ROOT/'models/yolo11s.pt'), fingerprint(ROOT/'models/osnet_msmt17.pth'),
                     fingerprint(ROOT/'tracker.yaml')]
        key = hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:24]
        folder = ROOT/'cache'/key
        folder.mkdir(parents=True, exist_ok=True)
        if (folder/'data.npz').exists() and not self.args.refresh:
            with np.load(folder/'data.npz', allow_pickle=False) as data:
                return {k: data[k] for k in data.files}, folder
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError('Cannot open video')
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not np.isfinite(fps) or fps <= 0:
            cap.release()
            raise RuntimeError('Invalid video FPS')
        expected = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        next_sample = 0.
        embeddings, histograms, timestamps, confidences, tracks = [], [], [], [], []
        count, sampled = 0, 0
        last_update=0.
        # Tracker state must not leak between different clips.
        if hasattr(self.detector.predictor, 'trackers'):
            for tracker in self.detector.predictor.trackers:
                tracker.reset()
        try:
            while cap.grab():
                check_stop(getattr(self.args,'stop_file',None))
                index = count
                count += 1
                if time.monotonic()-last_update > 1:
                    emit(self.args,'clip_progress',file=path.name,decoded=count,sampled=sampled)
                    last_update=time.monotonic()
                timestamp = cap.get(cv2.CAP_PROP_POS_MSEC)/1000
                if not np.isfinite(timestamp) or timestamp < 0:
                    timestamp = index/fps
                if timestamp+.004 < next_sample:
                    continue
                next_sample = timestamp+1/rate
                ok, frame = cap.retrieve()
                if not ok:
                    raise RuntimeError(f'Failed to decode frame {index}')
                sampled += 1
                result = self.detector.track(frame, persist=True, tracker=str(ROOT/'tracker.yaml'),
                    classes=[0], conf=.30 if reference else .15, imgsz=self.args.imgsz,
                    device=self.device, verbose=False)[0]
                crops, info = [], []
                for box in result.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
                    if x2-x1 < 10 or y2-y1 < 24:
                        continue
                    if reference and (y2-y1 < frame.shape[0]*.30 or x2-x1 < frame.shape[1]*.06):
                        continue
                    crops.append(frame[y1:y2, x1:x2].copy())
                    info.append((float(box.conf[0]), int(box.id[0]) if box.id is not None else -1))
                if not crops:
                    continue
                if reference:
                    # The target is the only foreground person in the supplied references.
                    # Avoid distant seated people/reflections contaminating the gallery.
                    largest = max(range(len(crops)), key=lambda i: crops[i].shape[0]*crops[i].shape[1])
                    crops, info = [crops[largest]], [info[largest]]
                vectors = self.features(crops)
                for crop, vector, (confidence, track) in zip(crops, vectors, info):
                    n = len(embeddings)
                    cv2.imwrite(str(folder/f'{n}.jpg'), crop)
                    embeddings.append(vector)
                    histograms.append(colors(crop))
                    timestamps.append(timestamp)
                    confidences.append(confidence)
                    tracks.append(track)
        finally:
            cap.release()
        if not count:
            raise RuntimeError('No frames decoded')
        if expected > 0 and count < expected*.90:
            # MPEG-TS headers can still advertise 25fps when exposure reduces
            # the actual frame rate. Verify decoded count, not an estimated count.
            probe = subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
                '-show_entries','stream=nb_read_frames','-of','json',str(path)],
                capture_output=True,text=True,timeout=120)
            if probe.returncode or probe.stderr.strip():
                raise RuntimeError(f'Frame verification failed: {probe.stderr[:300]}')
            actual = int(json.loads(probe.stdout)['streams'][0]['nb_read_frames'])
            if actual != count:
                raise RuntimeError(f'Decoding mismatch: OpenCV {count}, FFprobe {actual}')
        data = dict(embeddings=np.asarray(embeddings, dtype=np.float32).reshape(-1,512),
            colors=np.asarray(histograms, dtype=np.float32).reshape(-1,384),
            times=np.asarray(timestamps), confidence=np.asarray(confidences), tracks=np.asarray(tracks),
            decoded_frames=np.asarray(count), sampled_frames=np.asarray(sampled))
        np.savez_compressed(folder/'data.npz', **data)
        return data, folder

    def verify(self, data, folder, gallery):
        if not len(data['embeddings']):
            return verified_match(data,gallery,np.empty((0,2,10)),np.empty((0,2),bool))
        scores = (data['embeddings'] @ gallery['embeddings'].T).max(axis=1)
        indices = np.flatnonzero(scores >= self.args.threshold-.10)
        if not len(indices):
            indices = np.asarray([scores.argmax()])
        clothes,valid = self.clothing.extract(folder,len(scores),indices)
        result = verified_match(data,gallery,clothes,valid,
            upper=self.args.upper_color,lower=self.args.lower_color,reid=self.args.threshold)
        result['uncertain'] = bool(((scores >= self.args.threshold) & ~valid.all(axis=1)).any())
        if result['uncertain'] and not result['passed'] and not result.get('single_frame',False):
            unknown = np.flatnonzero((scores >= self.args.threshold) & ~valid.all(axis=1))
            i = int(unknown[scores[unknown].argmax()])
            similarities = data['embeddings'][i] @ gallery['embeddings'].T
            j = int(similarities.argmax())
            agreement = np.minimum(clothes[i],gallery['clothes'][j]).sum(axis=1)
            result.update(index=i,score=float(similarities[j]),upper=float(agreement[0]),lower=float(agreement[1]))
        return result


def reference_profile(reference):
    descriptions = {}
    for part, label in enumerate(['上身区域','下身区域']):
        distribution = reference['clothes'][reference['valid'][:,part],part].mean(axis=0)
        totals = dict(zip(COLOR_NAMES,distribution.tolist()))
        descriptions[label] = [{'color':name,'fraction':round(value,3)} for name,value in sorted(totals.items(),key=lambda pair:-pair[1])[:4]]
    return {'method':'基于肩、髋、膝关键点的身体区域颜色统计与 OSNet 外观向量',
            'color_regions':descriptions, 'gender':'未推断',
            'limitations':'上下装颜色必须分别匹配同一参考样本；款式和头发长度请核对参考截图。'}


def copy_original(source, destination):
    if destination.exists():
        def digest(path):
            with path.open('rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest(source) != digest(destination):
            raise RuntimeError(f'Output collision; existing file differs: {destination}')
        return
    temporary = destination.with_name(destination.name+'.partial')
    shutil.copy2(source, temporary)
    temporary.replace(destination)


from app_common import persist_report as write_reports


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=float, default=.72)
    parser.add_argument('--upper-color',type=float,default=.60)
    parser.add_argument('--lower-color',type=float,default=.65)
    parser.add_argument('--fps', type=float, default=5)
    parser.add_argument('--dense-fps', type=float, default=12.5)
    parser.add_argument('--imgsz', type=int, default=1280)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--reference-dir',type=Path,default=ROOT/'input/reference_video')
    parser.add_argument('--input-dir',type=Path,default=ROOT/'input/waiting_for_filter_videos')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'output')
    parser.add_argument('--report-dir',type=Path)
    parser.add_argument('--stop-file',type=Path)
    parser.add_argument('--decisions-file',type=Path)
    parser.add_argument('--events',action='store_true')
    parser.add_argument('--no-latest',action='store_true')
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1 or args.fps <= 0 or args.dense_fps < args.fps or not 0 <= args.upper_color <= 1 or not 0 <= args.lower_color <= 1:
        parser.error('Invalid threshold or sampling settings')
    return args


def run(args):
    check_stop(args.stop_file)
    torch.set_num_threads(4)
    refs,candidates=validate_folders(args.reference_dir,args.input_dir,args.output_dir)
    output=args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    report=args.report_dir
    # A unique suffix prevents two same-second launches sharing one report.
    if args.report_dir is None:
        report=ROOT/'reports'/f'{time.strftime("%Y%m%d_%H%M%S")}_{time.time_ns()%1000000:06d}'
    report.mkdir(parents=True)
    (report/'evidence').mkdir()
    from app_common import target_identity
    target_id=target_identity(refs)
    decisions=ReviewStore(args.decisions_file,target_id) if args.decisions_file else None
    if args.limit:
        candidates=candidates[:args.limit]
    emit(args,'started',report=str(report.resolve()),total=len(candidates),output=str(output))
    emit(args,'phase',message='正在加载人物检测与衣着匹配模型')
    pipeline = Pipeline(args)
    check_stop(args.stop_file)
    emit(args,'device',device=pipeline.device,name=torch.cuda.get_device_name(0) if pipeline.device.startswith('cuda') else 'CPU')
    gallery, reference_info = [], []
    for i,ref in enumerate(refs,1):
        check_stop(args.stop_file)
        emit(args,'phase',message=f'建立参考人物：{i}/{len(refs)} · {ref.name}')
        data, folder = pipeline.extract(ref, args.dense_fps, True)
        if not len(data['embeddings']):
            raise RuntimeError(f'No reference person detected: {ref.name}')
        gallery.append(data)
        data['clothes'],data['valid'] = pipeline.clothing.extract(folder,len(data['embeddings']))
        if data['valid'].all(axis=1).sum() < 2:
            raise RuntimeError(f'Insufficient clear garment reference crops: {ref.name}')
        indices = np.linspace(0, len(data['embeddings'])-1, min(8,len(data['embeddings'])), dtype=int)
        for i in indices:
            shutil.copy2(folder/f'{i}.jpg', report/'evidence'/f'ref_{ref.stem}_{i}.jpg')
        reference_info.append({'file':ref.name, 'detections':len(data['embeddings'])})
        print(f'Reference {ref.name}: {len(data["embeddings"])} crops', flush=True)
    reference = {k: np.concatenate([d[k] for d in gallery]) for k in ['embeddings','colors','clothes','valid']}
    (report/'reference_profile.json').write_text(json.dumps(reference_profile(reference), ensure_ascii=False, indent=2), encoding='utf-8')
    reference_checks = []
    if len(gallery) > 1:
        for i, data in enumerate(gallery):
            others = {k: np.concatenate([d[k] for j,d in enumerate(gallery) if j != i]) for k in ['embeddings','colors','clothes','valid']}
            check = verified_match(data,others,data['clothes'],data['valid'],
                upper=args.upper_color,lower=args.lower_color,reid=args.threshold)
            reference_checks.append({'file':refs[i].name,**check})
    (report/'reference_checks.json').write_text(json.dumps(reference_checks, indent=2), encoding='utf-8')
    rows = []
    settings={key:str(value) if isinstance(value,Path) else value for key,value in vars(args).items()}
    summary = {'settings':settings, 'device':pipeline.device, 'references':reference_info,
               'reference_crops':len(reference['embeddings']), 'report':str(report),
               'reference_checks':reference_checks,'reference_dir':str(args.reference_dir.resolve()),
               'input_dir':str(args.input_dir.resolve()),'output_dir':str(output),'target_id':target_id,
               'processed':0,'copied':0,'errors':0,'total':len(candidates),'cancelled':False}
    write_reports(rows,report,summary)
    for n, path in enumerate(candidates, 1):
        if args.stop_file and args.stop_file.exists():
            summary['cancelled']=True
            break
        emit(args,'phase',message=f'筛选 {n}/{len(candidates)} · {path.name}')
        row = dict(file=path.name,status='error',score=0.,reid_similarity=0.,color_similarity=0.,
                   upper_color=0.,lower_color=0.,support_frames=0,time_seconds=0.,track_id=-1,detections=0,copied=False,evidence='',error='',
                   source_path=str(path.resolve()),manual_decision='')
        try:
            data, folder = pipeline.extract(path, args.fps)
            decision = pipeline.verify(data,folder,reference)
            if not decision['passed']:
                data, folder = pipeline.extract(path, args.dense_fps)
                decision = pipeline.verify(data,folder,reference)
            score,index = decision['score'],decision['index']
            row.update(score=score,reid_similarity=score,upper_color=decision['upper'],lower_color=decision['lower'],
                       support_frames=decision['frames'],detections=len(data['embeddings']))
            row['status'] = 'match' if decision['passed'] else 'review' if decision.get('uncertain',False) or decision.get('single_frame',False) else 'no_match' if index >= 0 else 'no_person'
            if index >= 0:
                row.update(time_seconds=float(data['times'][index]), track_id=int(data['tracks'][index]))
                image_name = f'evidence/{path.name}.jpg'
                shutil.copy2(folder/f'{index}.jpg', report/image_name)
                row['evidence'] = image_name
            manual=decisions.get(path) if decisions else None
            row['manual_decision']=manual or ''
            if manual=='exclude':
                row['status']='manual_excluded'
            elif manual=='keep':
                row['status']='manual_kept'
            if manual=='keep' or (decision['passed'] and manual!='exclude'):
                copy_original(path, output/path.name)
                row['copied'] = True
        except StopRequested:
            summary['cancelled']=True
            break
        except Exception as exc:
            row.update(status='error',error=str(exc))
        rows.append(row)
        summary.update(processed=len(rows),copied=sum(r['copied'] for r in rows),errors=sum(r['status']=='error' for r in rows))
        write_reports(rows, report, summary)
        emit(args,'result',row=row,processed=len(rows),total=len(candidates),copied=summary['copied'],errors=summary['errors'])
        print(f'[{n}/{len(candidates)}] {path.name}: {row["status"]} {row["score"]:.3f} {row["error"]}', flush=True)
    write_reports(rows,report,summary)
    if not args.no_latest:
        (ROOT/'reports/latest.txt').write_text(str(report.resolve()),encoding='utf-8')
    emit(args,'finished',**summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 2 if summary['errors'] else 0


def main():
    args=parse_args()
    (ROOT/'runtime').mkdir(exist_ok=True)
    try:
        with FileLock(ROOT/'runtime/filter.lock',timeout=0):
            return run(args)
    except StopRequested:
        emit(args,'cancelled',message='已停止，已提取的特征可在下次继续使用')
        return 0
    except Timeout:
        emit(args,'fatal',message='已有筛选任务正在运行，请等待完成或停止该任务')
        return 1
    except Exception as exc:
        emit(args,'fatal',message=str(exc))
        if not args.events:
            raise
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
