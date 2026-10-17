"""Read-only TS/MP4 preview through installed FFmpeg; no transcoded files."""
import json
import queue
import shutil
import subprocess
import tempfile
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
        self._lock=threading.Lock()

    @staticmethod
    def _terminate(process):
        if process and process.poll() is None:
            try:process.terminate()
            except OSError:pass

    def stop(self):
        with self._lock:
            self.generation+=1
            process=self.process
            self.process=None
            while not self.frames.empty():
                try:self.frames.get_nowait()
                except queue.Empty:break
        self._terminate(process)

    def play(self,path,start=0.):
        self.stop()
        with self._lock:
            generation=self.generation
            self.position=max(0.,start)
            self.duration=0.
        self.thread=threading.Thread(target=self._read,args=(str(path),start,generation),daemon=True)
        self.thread.start()

    def _spawn(self,command,generation,**kwargs):
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,creationflags=HIDDEN,**kwargs)
        with self._lock:
            current=generation==self.generation
            if current:self.process=process
        if not current:self._terminate(process)
        return process

    def _release(self,process):
        if process is None:return
        self._terminate(process)
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=2)
        finally:
            if process.stdout:process.stdout.close()
            if process.stderr:process.stderr.close()
            with self._lock:
                if self.process is process:self.process=None

    def _read(self,path,start,generation):
        process=None
        try:
            ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
            if not ffmpeg or not ffprobe:
                raise ValueError('未找到 FFmpeg / FFprobe，无法预览视频')
            process=self._spawn([ffprobe,'-v','error','-show_entries','format=duration','-of','json',path],
                generation,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            stdout,_=process.communicate(timeout=10)
            if generation!=self.generation:return
            if process.returncode:raise ValueError('无法读取视频信息')
            duration=float(json.loads(stdout)['format']['duration'])
            self._release(process);process=None
            start=max(0.,min(start,max(0.,duration-.1)))
            with self._lock:
                if generation!=self.generation:return
                self.duration=duration;self.position=start
                self.events.put({'type':'duration','duration':duration,'generation':generation})
            command=[ffmpeg,'-hide_banner','-loglevel','error','-re','-ss',str(start),'-i',path,'-an',
                '-vf',f'scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps={FPS}',
                '-pix_fmt','rgb24','-f','rawvideo','pipe:1']
            # Capture diagnostics without a full stderr PIPE blocking the decoder.
            # This temporary stream is removed automatically; it contains no video.
            with tempfile.TemporaryFile() as errors:
                process=self._spawn(command,generation,stdout=subprocess.PIPE,stderr=errors)
                size=WIDTH*HEIGHT*3
                index=0
                while generation==self.generation:
                    frame=process.stdout.read(size)
                    if len(frame)!=size:break
                    position=min(duration,start+index/FPS)
                    index+=1
                    with self._lock:
                        if generation!=self.generation:break
                        self.position=position
                        if self.frames.full():
                            try:self.frames.get_nowait()
                            except queue.Empty:pass
                        self.frames.put_nowait((generation,frame,position))
                if generation!=self.generation:return
                code=process.wait(timeout=2)
                if code:
                    errors.seek(0)
                    detail=errors.read(2000).decode('utf-8',errors='replace').strip()
                    raise ValueError(f'视频解码失败（退出码 {code}）'+(f'：{detail}' if detail else ''))
                if not index:raise ValueError('视频没有可播放的画面')
                self.events.put({'type':'ended','generation':generation,'code':code})
        except Exception as exc:
            if generation==self.generation:
                self.events.put({'type':'error','message':str(exc),'generation':generation})
        finally:
            self._release(process)
