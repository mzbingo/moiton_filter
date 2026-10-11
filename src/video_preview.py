"""Read-only TS/MP4 preview through installed FFmpeg; no transcoded files."""
import json
import queue
import shutil
import subprocess
import threading

HIDDEN=getattr(subprocess,'CREATE_NO_WINDOW',0)
WIDTH,HEIGHT,FPS=640,360,15


class VideoPreview:
    def __init__(self):
        self.frames=queue.Queue(maxsize=2)
        self.events=queue.Queue()
        self.process=None
        self.thread=None
        self.generation=0
        self.position=0.
        self.duration=0.

    def stop(self):
        self.generation+=1
        if self.process and self.process.poll() is None:
            self.process.terminate()
        while not self.frames.empty():
            try:self.frames.get_nowait()
            except queue.Empty:break

    def play(self,path,start=0.):
        self.stop()
        generation=self.generation
        self.position=start
        self.thread=threading.Thread(target=self._read,args=(str(path),start,generation),daemon=True)
        self.thread.start()

    def _read(self,path,start,generation):
        try:
            ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
            if not ffmpeg or not ffprobe:
                raise ValueError('未找到 FFmpeg / FFprobe，无法预览视频')
            probe=subprocess.run([ffprobe,'-v','error','-show_entries','format=duration','-of','json',path],
                capture_output=True,text=True,creationflags=HIDDEN,timeout=10)
            if probe.returncode:raise ValueError('无法读取视频信息')
            duration=float(json.loads(probe.stdout)['format']['duration'])
            if generation!=self.generation:return
            start=max(0.,min(start,max(0.,duration-.1)))
            self.duration=duration
            self.events.put({'type':'duration','duration':duration,'generation':generation})
            command=[ffmpeg,'-hide_banner','-loglevel','error','-re','-ss',str(start),'-i',path,'-an',
                '-vf',f'scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps={FPS}',
                '-pix_fmt','rgb24','-f','rawvideo','pipe:1']
            process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,creationflags=HIDDEN)
            if generation!=self.generation:
                process.terminate();process.wait();return
            self.process=process
            size=WIDTH*HEIGHT*3
            index=0
            while generation==self.generation:
                frame=process.stdout.read(size)
                if len(frame)!=size or generation!=self.generation:break
                position=min(duration,start+index/FPS)
                index+=1
                self.position=position
                if self.frames.full():
                    try:self.frames.get_nowait()
                    except queue.Empty:pass
                self.frames.put((generation,frame,position))
            if process.poll() is None:process.terminate()
            code=process.wait()
            process.stdout.close()
            if generation==self.generation:
                self.events.put({'type':'ended','generation':generation,'code':code})
        except Exception as exc:
            if generation==self.generation:
                self.events.put({'type':'error','message':str(exc),'generation':generation})
