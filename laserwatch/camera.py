from __future__ import annotations
import logging,time
from typing import Optional
import cv2
from PySide6.QtCore import QMutex,QMutexLocker,QThread,Signal
from .diagnostics import fourcc_to_text
from .models import CameraSettings
log=logging.getLogger(__name__)
class CameraThread(QThread):
    frame_ready=Signal(object,object,object); status=Signal(str); camera_error=Signal(str); actual_exposure_raw=Signal(int); capture_info=Signal(object)
    def __init__(self,settings,parent=None):
        super().__init__(parent); self.settings=settings; self._running=False; self._cap:Optional[cv2.VideoCapture]=None; self._control_mutex=QMutex(); self._pending_exposure_raw=None; self._pending_gain=None
        self.frames_captured=0; self.read_failures=0; self.reconnect_count=0; self.backend_name=''; self.last_capture_info={}
    def stop(self): self._running=False; self.requestInterruption()
    def set_exposure_raw(self,value):
        with QMutexLocker(self._control_mutex): self.settings.exposure_raw=int(value); self._pending_exposure_raw=int(value)
    def set_gain(self,value):
        with QMutexLocker(self._control_mutex): self.settings.gain=float(value); self._pending_gain=float(value)
    def _apply_pending_controls(self):
        if self._cap is None:return
        with QMutexLocker(self._control_mutex): exposure_raw=self._pending_exposure_raw; gain=self._pending_gain; self._pending_exposure_raw=None; self._pending_gain=None
        if exposure_raw is not None:
            try:
                ok=self._cap.set(cv2.CAP_PROP_EXPOSURE,float(exposure_raw))
                if not ok:self.camera_error.emit('Camera rejected exposure setting')
                else:
                    actual=self._cap.get(cv2.CAP_PROP_EXPOSURE)
                    if actual==actual:self.actual_exposure_raw.emit(int(round(actual)))
            except Exception:log.exception('Exposure setting failed');self.camera_error.emit('Exposure setting failed')
        if gain is not None:
            try:
                if not self._cap.set(cv2.CAP_PROP_GAIN,gain):self.camera_error.emit('Camera rejected gain setting')
            except Exception:log.exception('Gain setting failed');self.camera_error.emit('Gain setting failed')
    def _emit_capture_info(self,cap,backend_name):
        try:
            info={'backend':backend_name,'actual_width':int(round(cap.get(cv2.CAP_PROP_FRAME_WIDTH))),'actual_height':int(round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))),'actual_fps':float(cap.get(cv2.CAP_PROP_FPS)),'fourcc':fourcc_to_text(cap.get(cv2.CAP_PROP_FOURCC)),'buffer_size':float(cap.get(cv2.CAP_PROP_BUFFERSIZE)),'exposure_raw':float(cap.get(cv2.CAP_PROP_EXPOSURE)),'gain':float(cap.get(cv2.CAP_PROP_GAIN))}
        except Exception as exc:info={'backend':backend_name,'capture_info_error':str(exc)}
        self.last_capture_info=info; self.capture_info.emit(info)
    def _open_camera(self):
        backends=[]
        if hasattr(cv2,'CAP_DSHOW'):backends.append(('DirectShow',cv2.CAP_DSHOW))
        if hasattr(cv2,'CAP_MSMF'):backends.append(('MediaFoundation (fallback)',cv2.CAP_MSMF))
        backends.append(('Default (fallback)',cv2.CAP_ANY))
        for name,backend in backends:
            if not self._running or self.isInterruptionRequested():return None
            cap=None
            try:
                self.status.emit(f'OPENING:{name}');cap=cv2.VideoCapture(self.settings.camera_index,backend)
                if not cap.isOpened():cap.release();continue
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,self.settings.width);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,self.settings.height);cap.set(cv2.CAP_PROP_FPS,self.settings.fps);cap.set(cv2.CAP_PROP_BUFFERSIZE,1)
                try:
                    if backend==getattr(cv2,'CAP_DSHOW',object()):cap.set(cv2.CAP_PROP_AUTO_EXPOSURE,0.25)
                    cap.set(cv2.CAP_PROP_EXPOSURE,float(self.settings.exposure_raw));cap.set(cv2.CAP_PROP_GAIN,self.settings.gain)
                except Exception:log.exception('Initial camera controls failed')
                self.backend_name=name;self._emit_capture_info(cap,name);return cap
            except Exception:
                log.exception('Camera open attempt failed: backend=%s',name)
                if cap is not None:
                    try:cap.release()
                    except Exception:pass
        return None
    def run(self):
        self._running=True;frame_id=0;consecutive_failures=0
        try:
            while self._running and not self.isInterruptionRequested():
                if self._cap is None or not self._cap.isOpened():
                    if frame_id>0:self.reconnect_count+=1
                    self._cap=self._open_camera()
                    if self._cap is None:
                        self.status.emit('RECONNECTING');self.camera_error.emit(f'Camera {self.settings.camera_index} could not be opened. Retrying...')
                        for _ in range(10):
                            if not self._running or self.isInterruptionRequested():break
                            time.sleep(0.1)
                        continue
                    self.status.emit('RUNNING');consecutive_failures=0
                self._apply_pending_controls()
                try:ok,frame=self._cap.read();ts=time.perf_counter_ns()
                except Exception:log.exception('Camera read raised an exception');ok,frame=False,None;ts=time.perf_counter_ns()
                if not ok or frame is None:
                    self.read_failures+=1;consecutive_failures+=1
                    if consecutive_failures==1 or consecutive_failures%10==0:self.camera_error.emit(f'Frame read failed ({consecutive_failures}); reconnecting if persistent.')
                    if consecutive_failures>=5:
                        try:self._cap.release()
                        except Exception:log.exception('Camera release failed during reconnect')
                        self._cap=None;self.status.emit('RECONNECTING');consecutive_failures=0;time.sleep(0.2)
                    else:time.sleep(0.02)
                    continue
                consecutive_failures=0;frame_id+=1;self.frames_captured+=1;self.frame_ready.emit(frame,ts,frame_id)
        except Exception:log.exception('Fatal exception in camera thread');self.camera_error.emit('Unexpected camera-thread error. See LaserWatch log.')
        finally:
            if self._cap is not None:
                try:self._cap.release()
                except Exception:log.exception('Camera release failed')
            self._cap=None;self.status.emit('STOPPED')
