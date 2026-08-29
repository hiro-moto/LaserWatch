from __future__ import annotations

from collections import OrderedDict
import logging
import math
import queue
import threading

import numpy as np
from PySide6.QtCore import QThread, Signal

from .analysis import BeamAnalyzer

log = logging.getLogger(__name__)


class AnalysisThread(QThread):
    result_ready = Signal(object)
    analysis_error = Signal(str)

    def __init__(self, analyzer: BeamAnalyzer, parent=None, profile_hz: float = 5.0):
        super().__init__(parent)
        self.analyzer = analyzer
        self._q = queue.Queue(maxsize=1)
        self._running = False
        self.frames_submitted = 0
        self.frames_processed = 0
        self.frames_dropped = 0
        self.failures = 0

        # Beam centroid/size/pointing remains the high-rate analysis path.
        # Cross-section generation is display-only and is intentionally
        # decimated so it cannot unnecessarily reduce the FFT bandwidth.
        hz = float(profile_hz)
        self.profile_hz = hz if math.isfinite(hz) and hz > 0.0 else 5.0
        self._profile_interval_ns = max(1, int(round(1e9 / self.profile_hz)))
        self._last_profile_timestamp_ns = None
        self._last_processed_frame_id = None

        # BeamAnalyzer currently builds the profile inside analyze(). Save its
        # builder so skipped profile frames can bypass only that display-only
        # step while preserving the established analyze() API and all centroid,
        # D4sigma, exposure and quality calculations. Each BeamAnalyzer belongs
        # to exactly one AnalysisThread, so this temporary per-instance override
        # is not shared between cameras.
        self._profile_builder = getattr(analyzer, "_cache_cross_sections", None)

        # Keep a few generated profiles per camera so a busy GUI can display the
        # newest profile at or before a BeamResult's frame id.
        self._profile_cache = OrderedDict()
        self._profile_cache_lock = threading.RLock()
        self._profile_cache_limit = 16
        self._original_get_last_profile = getattr(analyzer, "get_last_profile", None)
        if callable(self._original_get_last_profile):
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

    def _profile_due(self, timestamp_ns: int) -> bool:
        """Return True at approximately ``profile_hz`` using capture time."""
        ts = int(timestamp_ns)
        last = self._last_profile_timestamp_ns
        if last is None or ts <= last or ts - last >= self._profile_interval_ns:
            self._last_profile_timestamp_ns = ts
            return True
        return False

    def _analyze_frame(self, frame, ts, frame_id, compute_profile: bool):
        builder = self._profile_builder
        if compute_profile or not callable(builder):
            return self.analyzer.analyze(frame, ts, frame_id)

        # Skip only BeamAnalyzer._cache_cross_sections for this frame. The main
        # analysis path remains unchanged. Always restore the bound method even
        # if analysis raises.
        self.analyzer._cache_cross_sections = lambda *_args, **_kwargs: None
        try:
            return self.analyzer.analyze(frame, ts, frame_id)
        finally:
            self.analyzer._cache_cross_sections = builder

    def _cache_profile_for_frame(self, frame_id: int, timestamp_ns: int = 0):
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
        data = dict(data)
        data["profile_timestamp_ns"] = int(timestamp_ns)
        with self._profile_cache_lock:
            self._profile_cache[fid] = data
            self._profile_cache.move_to_end(fid)
            while len(self._profile_cache) > self._profile_cache_limit:
                self._profile_cache.popitem(last=False)

    def _clear_profile_cache(self):
        with self._profile_cache_lock:
            self._profile_cache.clear()
        self._last_profile_timestamp_ns = None

    def get_profile_for_frame(self, frame_id=None):
        """Return the newest generated profile no newer than ``frame_id``.

        Profile generation runs at a lower rate than centroid analysis. Results
        between profile updates reuse the last completed profile; BeamProfileWidget
        ignores the repeated profile key, so pyqtgraph redraws only at profile_hz.
        """
        data = None
        with self._profile_cache_lock:
            if self._profile_cache:
                if frame_id is None:
                    _, data = next(reversed(self._profile_cache.items()))
                else:
                    try:
                        fid = int(frame_id)
                        for cached_fid, cached_data in reversed(self._profile_cache.items()):
                            if cached_fid <= fid:
                                data = cached_data
                                break
                    except Exception:
                        data = None

        if data is not None:
            return dict(data)

        getter = self._original_get_last_profile
        if callable(getter):
            try:
                return getter(frame_id)
            except Exception:
                log.exception("Beam profile lookup failed for frame %s", frame_id)
        return None

    @staticmethod
    def _result_has_valid_position(result) -> bool:
        try:
            return math.isfinite(float(result.cx_um)) and math.isfinite(float(result.cy_um))
        except Exception:
            return True

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
                    fid = int(frame_id)
                    if (
                        self._last_processed_frame_id is not None
                        and fid <= self._last_processed_frame_id
                    ):
                        # Camera restart / frame-counter reset: do not reuse a
                        # profile from the previous acquisition run.
                        self._clear_profile_cache()
                    self._last_processed_frame_id = fid

                    compute_profile = self._profile_due(ts)
                    result = self._analyze_frame(frame, ts, frame_id, compute_profile)
                    if self._result_has_valid_position(result):
                        if compute_profile:
                            self._cache_profile_for_frame(frame_id, ts)
                    else:
                        # Do not leave an old cross section on screen after beam
                        # loss. The next valid frame immediately earns a profile.
                        self._clear_profile_cache()
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
