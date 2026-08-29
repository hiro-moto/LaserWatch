from __future__ import annotations
import logging,queue
import numpy as np
from PySide6.QtCore import QThread,Signal
from .analysis import BeamAnalyzer
log=logging.getLogger(__name__)
class AnalysisThread(QThread):
    result_ready=Signal(object);analysis_error=Signal(str)
    def __init__(self,analyzer,parent=None):
        super().__init__(parent);self.analyzer=analyzer;self._q=queue.Queue(maxsize=1);self._running=False;self.frames_submitted=0;self.frames_processed=0;self.frames_dropped=0;self.failures=0
    @property
    def queue_depth(self):
        try:return self._q.qsize()
        except Exception:return 0
    def submit(self,frame:np.ndarray,timestamp_ns:int,frame_id:int):
        if not self._running:return
        self.frames_submitted+=1;item=(frame,timestamp_ns,frame_id)
        try:self._q.put_nowait(item)
        except queue.Full:
            self.frames_dropped+=1
            try:self._q.get_nowait()
            except queue.Empty:pass
            try:self._q.put_nowait(item)
            except queue.Full:self.frames_dropped+=1
    def stop(self):
        self._running=False;self.requestInterruption()
        try:self._q.put_nowait(None)
        except queue.Full:
            try:self._q.get_nowait();self._q.put_nowait(None)
            except Exception:pass
    def run(self):
        self._running=True
        try:
            while self._running and not self.isInterruptionRequested():
                try:item=self._q.get(timeout=0.2)
                except queue.Empty:continue
                if item is None:break
                frame,ts,frame_id=item
                try:
                    result=self.analyzer.analyze(frame,ts,frame_id); self.frames_processed+=1; self.result_ready.emit(result)
                except Exception as exc:self.failures+=1;log.exception('Analysis failed on frame %s',frame_id);self.analysis_error.emit(f'Analysis failed: {exc}')
        except Exception:log.exception('Fatal exception in analysis thread');self.analysis_error.emit('Unexpected analysis-thread error. See LaserWatch log.')
        finally:self._running=False
