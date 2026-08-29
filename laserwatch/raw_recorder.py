from __future__ import annotations
import logging,queue,threading
from pathlib import Path
from typing import Optional
import numpy as np
log=logging.getLogger(__name__)
class HDF5FrameRecorder:
    def __init__(self,queue_size=16):
        self.queue_size=max(2,int(queue_size)); self._q=queue.Queue(maxsize=self.queue_size); self._thread:Optional[threading.Thread]=None; self._stop=threading.Event(); self._path=None; self._active=False
        self.frames_written=0; self.frames_dropped=0; self.bytes_written_uncompressed=0; self.last_error=''; self._expected_shape=None; self._expected_dtype=None
    @property
    def active(self): return bool(self._active and self._thread is not None and self._thread.is_alive())
    @property
    def path(self): return self._path
    @property
    def queue_depth(self):
        try:return int(self._q.qsize())
        except Exception:return 0
    def start(self,path):
        self.stop(timeout_s=10.0)
        if self._thread is not None and self._thread.is_alive(): raise RuntimeError('Previous HDF5 writer is still running')
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); self._path=path; self._q=queue.Queue(maxsize=self.queue_size); self._stop.clear(); self._active=True
        self.frames_written=0; self.frames_dropped=0; self.bytes_written_uncompressed=0; self.last_error=''; self._expected_shape=None; self._expected_dtype=None
        self._thread=threading.Thread(target=self._run,name=f'HDF5Recorder:{path.name}',daemon=True); self._thread.start()
    def submit(self,frame,timestamp_ns,frame_id):
        if not self.active or frame is None or not isinstance(frame,np.ndarray) or frame.size==0:return False
        try:
            if self._q.full(): self.frames_dropped+=1; return False
            self._q.put_nowait((frame.copy(),int(timestamp_ns),int(frame_id))); return True
        except queue.Full: self.frames_dropped+=1; return False
        except Exception as exc: self.last_error=str(exc); log.exception('Raw frame submit failed'); return False
    def stop(self,timeout_s=10.0):
        if self._thread is None: self._active=False; return
        self._stop.set()
        try:self._q.put_nowait(None)
        except queue.Full:pass
        self._thread.join(timeout=max(0.1,float(timeout_s)))
        if self._thread.is_alive():
            self.last_error=self.last_error or f'HDF5 writer did not stop within {timeout_s:.1f} s'; log.warning(self.last_error); return
        self._active=False; self._thread=None
    def _run(self):
        try: import h5py
        except Exception as exc: self.last_error=f'h5py unavailable: {exc}'; self._active=False; return
        h5=None
        try:
            if self._path is None: raise RuntimeError('Recorder path is not set')
            h5=h5py.File(self._path,'w'); frames_ds=None; ts_ds=h5.create_dataset('timestamp_ns',shape=(0,),maxshape=(None,),dtype='i8',chunks=True); id_ds=h5.create_dataset('frame_id',shape=(0,),maxshape=(None,),dtype='i8',chunks=True)
            h5.attrs['format']='LaserWatch raw frames'; h5.attrs['version']='0.7'; h5.attrs['timestamp_clock']='time.perf_counter_ns'
            while True:
                if self._stop.is_set() and self._q.empty(): break
                try:item=self._q.get(timeout=0.2)
                except queue.Empty:continue
                if item is None:continue
                frame,timestamp_ns,frame_id=item
                if frames_ds is None:
                    self._expected_shape=tuple(frame.shape); self._expected_dtype=frame.dtype; frames_ds=h5.create_dataset('frames',shape=(0,)+frame.shape,maxshape=(None,)+frame.shape,dtype=frame.dtype,chunks=(1,)+frame.shape,compression='lzf',shuffle=True); frames_ds.attrs['shape']=frame.shape; frames_ds.attrs['dtype']=str(frame.dtype)
                elif tuple(frame.shape)!=self._expected_shape or frame.dtype!=self._expected_dtype: raise ValueError(f'Frame format changed during raw recording: expected {self._expected_shape}/{self._expected_dtype}, got {frame.shape}/{frame.dtype}')
                n=self.frames_written; frames_ds.resize((n+1,)+self._expected_shape); ts_ds.resize((n+1,)); id_ds.resize((n+1,)); frames_ds[n]=frame; ts_ds[n]=timestamp_ns; id_ds[n]=frame_id; self.frames_written+=1; self.bytes_written_uncompressed+=int(frame.nbytes)
                if self.frames_written%16==0:h5.flush()
            h5.attrs['frames_written']=self.frames_written; h5.attrs['frames_dropped']=self.frames_dropped; h5.attrs['uncompressed_bytes']=self.bytes_written_uncompressed; h5.flush()
        except Exception as exc:self.last_error=str(exc);log.exception('HDF5 raw recording failed')
        finally:
            if h5 is not None:
                try:h5.close()
                except Exception:log.exception('Failed to close HDF5 file')
            self._active=False
