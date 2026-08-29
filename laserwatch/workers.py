from __future__ import annotations

from collections import OrderedDict
import logging
import queue
import threading

import numpy as np
from PySide6.QtCore import QThread, Signal

from .analysis import BeamAnalyzer

log = logging.getLogger(__name__)


class AnalysisThread(QThread):
    result_ready = Signal(object)
    analysis_error = Signal(str)

    def __init__(self, analyzer: BeamAnalyzer, parent=None):
        super().__init__(parent)
        self.analyzer = analyzer
        self._q = queue.Queue(maxsize=1)
        self._running = False
        self.frames_submitted = 0
        self.frames_processed = 0
        self.frames_dropped = 0
        self.failures = 0

        # Beam-profile data used to live only in BeamAnalyzer._last_profile.
        # The GUI receives BeamResult asynchronously, so the analysis thread can
        # already be processing a newer frame by the time CameraPanel asks for
        # the matching profile. With multiple cameras the GUI event queue is
        # busier and that race becomes much easier to hit. Keep a small
        # per-analysis-thread frame-id cache so each BeamResult can retrieve the
        # profile generated from the same frame even after newer analysis starts.
        self._profile_cache = OrderedDict()
        self._profile_cache_lock = threading.RLock()
        self._profile_cache_limit = 16
        self._original_get_last_profile = getattr(analyzer, "get_last_profile", None)
        if callable(self._original_get_last_profile):
            # CameraPanel already calls analyzer.get_last_profile(frame_id), so
            # route that existing API through the stable frame-id cache without
            # widening the GUI signal or changing CameraPanel's public behavior.
            analyzer.get_last_profile = self.get_profile_for_frame

    @property
    def queue_depth(self):
        try:
            return self._q.qsize()
        except Exception:
            return 0

    def submit(self, frame: np.ndarray, timestamp_ns: int, frame_id: int):
        if not self._running:
            return
        self.frames_submitted += 1
        item = (frame, timestamp_ns, frame_id)
        try:
            self._q.put_nowait(item)
        except queue.Full:
            self.frames_dropped += 1
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(item)
            except queue.Full:
                self.frames_dropped += 1

    def stop(self):
        self._running = False
        self.requestInterruption()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            try:
                self._q.get_nowait()
                self._q.put_nowait(None)
            except Exception:
                pass

    def _cache_profile_for_frame(self, frame_id: int):
        getter = self._original_get_last_profile
        if not callable(getter):
            return
        try:
            data = getter(frame_id)
        except Exception:
            log.exception("Could not snapshot beam profile for frame %s", frame_id)
            return
        if data is None:
            return

        fid = int(frame_id)
        with self._profile_cache_lock:
            self._profile_cache[fid] = data
            self._profile_cache.move_to_end(fid)
            while len(self._profile_cache) > self._profile_cache_limit:
                self._profile_cache.popitem(last=False)

    def get_profile_for_frame(self, frame_id=None):
        """Return the profile associated with exactly ``frame_id``.

        Matching entries are consumed after the GUI reads them. A bounded cache
        also handles a short GUI backlog safely without retaining unbounded image
        profile arrays.
        """
        data = None
        with self._profile_cache_lock:
            if frame_id is None:
                if self._profile_cache:
                    _, data = next(reversed(self._profile_cache.items()))
            else:
                try:
                    data = self._profile_cache.pop(int(frame_id), None)
                except Exception:
                    data = None

        if data is not None:
            return dict(data)

        # Fallback keeps the original BeamAnalyzer API semantics for callers
        # outside the normal result-delivery path.
        getter = self._original_get_last_profile
        if callable(getter):
            try:
                return getter(frame_id)
            except Exception:
                log.exception("Beam profile lookup failed for frame %s", frame_id)
        return None

    def run(self):
        self._running = True
        try:
            while self._running and not self.isInterruptionRequested():
                try:
                    item = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item is None:
                    break
                frame, ts, frame_id = item
                try:
                    result = self.analyzer.analyze(frame, ts, frame_id)
                    # Snapshot the matching cross section before analyze() can
                    # start another frame and replace BeamAnalyzer._last_profile.
                    self._cache_profile_for_frame(frame_id)
                    self.frames_processed += 1
                    self.result_ready.emit(result)
                except Exception as exc:
                    self.failures += 1
                    log.exception("Analysis failed on frame %s", frame_id)
                    self.analysis_error.emit(f"Analysis failed: {exc}")
        except Exception:
            log.exception("Fatal exception in analysis thread")
            self.analysis_error.emit(
                "Unexpected analysis-thread error. See LaserWatch log."
            )
        finally:
            self._running = False
