"""Local single-outfit person retrieval. Originals are never modified."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision import transforms
from ultralytics import YOLO
from vendor.osnet import osnet_x1_0
from clothing import ClothingExtractor, verified_match, COLOR_NAMES
from events import StopRequested,check_stop,emit
from filelock import FileLock,Timeout
from cache_io import atomic_bytes,atomic_npz,load_person_cache,invalidate_person_cache,run_cancellable

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_VERSION = 3


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
            stop_check=lambda:check_stop(getattr(args,'stop_file',None)),
            cache_signature=fingerprint(ROOT/'models/yolo11s-pose.pt'))
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
        if not self.args.refresh:
            cached = load_person_cache(folder)
            if cached is not None:
                return cached, folder
        # Crops and clothing depend on this extraction. A cancelled refresh must
        # never leave an old data marker paired with partially replaced crops.
        invalidate_person_cache(folder)
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
                    encoded, image = cv2.imencode('.jpg', crop)
                    if not encoded:
                        raise RuntimeError(f'Cannot encode person crop {n}')
                    atomic_bytes(folder/f'{n}.jpg', image.tobytes())
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
            probe = run_cancellable(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
                '-show_entries','stream=nb_read_frames','-of','json',str(path)],
                lambda:check_stop(getattr(self.args,'stop_file',None)),timeout=120)
            if probe.returncode or probe.stderr.strip():
                raise RuntimeError(f'Frame verification failed: {probe.stderr[:300]}')
            actual = int(json.loads(probe.stdout)['streams'][0]['nb_read_frames'])
            if actual != count:
                raise RuntimeError(f'Decoding mismatch: OpenCV {count}, FFprobe {actual}')
        data = dict(embeddings=np.asarray(embeddings, dtype=np.float32).reshape(-1,512),
            colors=np.asarray(histograms, dtype=np.float32).reshape(-1,384),
            times=np.asarray(timestamps), confidence=np.asarray(confidences), tracks=np.asarray(tracks),
            decoded_frames=np.asarray(count), sampled_frames=np.asarray(sampled))
        check_stop(getattr(self.args,'stop_file',None))
        atomic_npz(folder/'data.npz', **data)
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
    parser.add_argument('--excluded-dir',type=Path,default=ROOT/'manual_excluded')
    parser.add_argument('--events',action='store_true')
    parser.add_argument('--no-latest',action='store_true')
    parser.add_argument('--archive-output',action='store_true')
    args = parser.parse_args()
    if args.limit < 0 or args.imgsz <= 0 or not 0 <= args.threshold <= 1 or args.fps <= 0 or args.dense_fps < args.fps or not 0 <= args.upper_color <= 1 or not 0 <= args.lower_color <= 1:
        parser.error('Invalid threshold or sampling settings')
    return args


def run(args):
    from run_workflow import execute
    torch.set_num_threads(4)
    return execute(args, ROOT, Pipeline, reference_profile,
        device_name=lambda pipeline: torch.cuda.get_device_name(0) if pipeline.device.startswith('cuda') else 'CPU')


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
