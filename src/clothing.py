"""Garment colors from confident body keypoints, excluding bbox background."""
from pathlib import Path
import json
import cv2
import numpy as np
from cache_io import atomic_npz,load_npz

COLOR_NAMES = ['dark','gray','light','red','orange','yellow','green','cyan','blue','purple']
VERSION = 1


def palette(pixels):
    hsv = cv2.cvtColor(pixels.reshape(-1,1,3), cv2.COLOR_BGR2HSV).reshape(-1,3)
    h,s,v = hsv.T
    labels = np.full(len(h), 8)
    labels[(h < 10) | (h >= 170)] = 3
    labels[(h >= 10) & (h < 25)] = 4
    labels[(h >= 25) & (h < 35)] = 5
    labels[(h >= 35) & (h < 80)] = 6
    labels[(h >= 80) & (h < 100)] = 7
    labels[(h >= 140) & (h < 170)] = 9
    labels[(s < 45) & (v >= 70) & (v < 185)] = 1
    labels[(s < 55) & (v >= 185)] = 2
    labels[v < 70] = 0
    hist = np.bincount(labels,minlength=len(COLOR_NAMES)).astype(np.float32)
    return hist / max(hist.sum(),1)


def garment_features(crop, points):
    h,w = crop.shape[:2]
    masks = [np.zeros((h,w),np.uint8),np.zeros((h,w),np.uint8)]
    good = points[:,2] >= .45
    # Both shoulders and hips needed: do not invent colors for clipped torsos.
    if good[[5,6,11,12]].all():
        shoulders = points[[5,6],:2].mean(axis=0)
        hips = points[[11,12],:2].mean(axis=0)
        length = np.linalg.norm(hips-shoulders)
        radius = int(min(w*.16,length*.20))
        if radius >= 3 and length >= h*.12:
            start = shoulders*.80+hips*.20
            end = shoulders*.18+hips*.82
            cv2.line(masks[0],tuple(start.astype(int)),tuple(end.astype(int)),1,max(3,radius*2))
    for hip,knee,ankle in [(11,13,15),(12,14,16)]:
        if not good[[hip,knee]].all():
            continue
        a,b = points[hip,:2],points[knee,:2]
        length = np.linalg.norm(b-a)
        radius = int(min(w*.08,length*.13))
        if radius < 3 or length < h*.10:
            continue
        start,end = .85*a+.15*b,.12*a+.88*b
        cv2.line(masks[1],tuple(start.astype(int)),tuple(end.astype(int)),1,max(3,radius*2))
        if good[ankle]:
            c = points[ankle,:2]
            cv2.line(masks[1],tuple((.85*b+.15*c).astype(int)),tuple((.35*b+.65*c).astype(int)),1,max(3,radius*2))
    histograms,valid = [],[]
    for mask in masks:
        pixels = crop[mask.astype(bool)]
        usable = len(pixels) >= 80
        histograms.append(palette(pixels) if usable else np.zeros(len(COLOR_NAMES),np.float32))
        valid.append(usable)
    return np.asarray(histograms),np.asarray(valid),masks


class ClothingExtractor:
    def __init__(self, model, device, refresh=False,stop_check=None,cache_signature=None):
        self.model,self.device = model,device
        self.refresh = refresh
        self.stop_check=stop_check or (lambda:None)
        self.cache_signature = json.dumps([VERSION,cache_signature],sort_keys=True)

    def extract(self, folder, count, indices=None):
        self.stop_check()
        indices = np.arange(count) if indices is None else np.asarray(indices,dtype=int)
        cache = Path(folder)/f'clothing_v{VERSION}.npz'
        hist = np.zeros((count,2,len(COLOR_NAMES)),np.float32)
        valid = np.zeros((count,2),bool)
        done = np.zeros(count,bool)
        if cache.exists() and not self.refresh:
            data = load_npz(cache)
            try:
                compatible = (data is not None and str(data['signature'].item()) == self.cache_signature
                    and data['hist'].shape == hist.shape and data['valid'].shape == valid.shape
                    and data['done'].shape == done.shape and data['valid'].dtype == np.bool_
                    and data['done'].dtype == np.bool_ and np.issubdtype(data['hist'].dtype,np.number)
                    and np.isfinite(data['hist']).all() and (data['hist'] >= 0).all()
                    and (data['hist'] <= 1).all())
            except (KeyError,TypeError,ValueError):
                compatible = False
            if compatible:
                hist,valid,done = data['hist'],data['valid'],data['done']
        pending = [int(i) for i in indices if not done[i]]
        for start in range(0,len(pending),16):
            self.stop_check()
            group = pending[start:start+16]
            crops = [cv2.imread(str(Path(folder)/f'{i}.jpg')) for i in group]
            if any(c is None for c in crops):
                raise RuntimeError('Missing cached person crop')
            results = self.model.predict(crops,imgsz=512,device=self.device,conf=.25,verbose=False)
            if len(results) != len(group):
                raise RuntimeError('Pose model returned an incomplete batch')
            for i,crop,result in zip(group,crops,results):
                if result.keypoints is not None and len(result.keypoints):
                    # Crop already contains one detected person; choose largest pose.
                    areas = (result.boxes.xyxy[:,2]-result.boxes.xyxy[:,0])*(result.boxes.xyxy[:,3]-result.boxes.xyxy[:,1])
                    person = int(areas.argmax())
                    points = result.keypoints.data[person].cpu().numpy()
                    hist[i],valid[i],_ = garment_features(crop,points)
                done[i] = True
            # Keep finished batches on cancellation, and leave a full cache hit
            # read-only instead of recompressing it for every review run.
            atomic_npz(cache,hist=hist,valid=valid,done=done,signature=np.asarray(self.cache_signature))
            self.stop_check()
        return hist,valid


def clothing_agreement(candidate, reference):
    # Histogram intersection is 1 for identical distributions, 0 for disjoint colors.
    return np.minimum(candidate[:,None,:,:],reference[None,:,:,:]).sum(axis=-1)


def verified_match(data, gallery, clothes, valid, upper=.60, lower=.65, reid=.72):
    count = len(data['embeddings'])
    if not count:
        return {'passed':False,'index':-1,'score':0.,'upper':0.,'lower':0.,'frames':0}
    similarities = data['embeddings'] @ gallery['embeddings'].T
    agreement = clothing_agreement(clothes,gallery['clothes'])
    qualified = (similarities >= reid) & (agreement[:,:,0] >= upper) & (agreement[:,:,1] >= lower)
    qualified &= valid.all(axis=1)[:,None] & gallery['valid'].all(axis=1)[None,:]
    # All three conditions must agree with the SAME reference crop.
    weighted = similarities*.65+agreement[:,:,0]*.15+agreement[:,:,1]*.20
    passing = qualified.any(axis=1)
    selected = -1
    support = 0
    for track in np.unique(data['tracks'][passing]):
        idx = np.flatnonzero(passing & (data['tracks']==track))
        # Untracked detections cannot be pooled into one identity.
        if track < 0 or len(idx) < 2 or np.ptp(data['times'][idx]) < .12:
            continue
        best = idx[np.max(np.where(qualified[idx],weighted[idx],-1),axis=1).argmax()]
        if selected < 0 or weighted[best][qualified[best]].max() > weighted[selected][qualified[selected]].max():
            selected,support = int(best),len(idx)
    passed = selected >= 0
    if not passed:
        if passing.any():
            selected = int(np.flatnonzero(passing)[0])
        else:
            reliable = valid.all(axis=1)[:,None] & gallery['valid'].all(axis=1)[None,:]
            if reliable.any():
                selected = int(np.unravel_index(np.where(reliable,weighted,-1).argmax(),weighted.shape)[0])
            else:
                selected = int(similarities.max(axis=1).argmax())
    row = selected
    allowed = qualified[row] if qualified[row].any() else gallery['valid'].all(axis=1)
    j = int(np.where(allowed,weighted[row],-1).argmax())
    return {'passed':passed,'single_frame':bool(passing.any() and not passed),'index':row,'score':float(similarities[row,j]),
            'upper':float(agreement[row,j,0]),'lower':float(agreement[row,j,1]),'frames':support}
