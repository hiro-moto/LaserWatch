import math
from types import SimpleNamespace

import numpy as np

from laserwatch.timeseries import TimeSeriesBuffer
from laserwatch.workers import AnalysisThread


# ----------------------------------------------------------------------
# Multi-camera beam-profile association + low-rate profile cadence.
# Centroid/result analysis may run at camera rate, while profiles are generated
# at ~5 Hz and reused between updates without cross-camera contamination.
# ----------------------------------------------------------------------
class FakeAnalyzer:
    def __init__(self, camera_name):
        self.camera_name = camera_name
        self._last_profile = None
        self.profile_calls = 0

    def analyze(self, frame, timestamp_ns, frame_id):
        self.profile_calls += 1
        self._last_profile = {
            "frame_id": int(frame_id),
            "camera": self.camera_name,
            "x_intensity": np.asarray([frame_id], dtype=np.float64),
        }
        return SimpleNamespace(frame_id=int(frame_id), cx_um=1.0, cy_um=2.0)

    def get_last_profile(self, frame_id=None):
        data = self._last_profile
        if data is None:
            return None
        if frame_id is not None and int(data["frame_id"]) != int(frame_id):
            return None
        return dict(data)


cam_a = FakeAnalyzer("A")
cam_b = FakeAnalyzer("B")
worker_a = AnalysisThread(cam_a, profile_hz=5.0)
worker_b = AnalysisThread(cam_b, profile_hz=5.0)

# 30-Hz result stream should request about 5 profile calculations per second.
base_ns = 1_000_000_000
due_frames = []
for i in range(30):
    ts = base_ns + int(i * 1e9 / 30.0)
    if worker_a._profile_due(ts):
        due_frames.append(i)
assert 4 <= len(due_frames) <= 6, due_frames

# A new acquisition run should earn a fresh profile immediately after reset.
worker_a._clear_profile_cache()
assert worker_a._profile_due(base_ns + 2_000_000_000)

# Production BeamAnalyzer exposes _cache_cross_sections. Verify the worker can
# suppress only that display-only callback without changing analyze() signature.
class SuppressionAnalyzer:
    def __init__(self):
        self.profile_calls = 0

    def _cache_cross_sections(self, *args, **kwargs):
        self.profile_calls += 1

    def analyze(self, frame, timestamp_ns, frame_id):
        self._cache_cross_sections(None)
        return SimpleNamespace(frame_id=frame_id, cx_um=1.0, cy_um=1.0)

    def get_last_profile(self, frame_id=None):
        return None


supp = SuppressionAnalyzer()
supp_worker = AnalysisThread(supp, profile_hz=5.0)
supp_worker._analyze_frame(None, 0, 1, compute_profile=False)
assert supp.profile_calls == 0
supp_worker._analyze_frame(None, 0, 2, compute_profile=True)
assert supp.profile_calls == 1

# Simulate generated profile frames with GUI results arriving in between.
for worker, analyzer in ((worker_a, cam_a), (worker_b, cam_b)):
    analyzer.analyze(None, 0, 10)
    worker._cache_profile_for_frame(10, 100)
    analyzer.analyze(None, 0, 16)
    worker._cache_profile_for_frame(16, 160)

# A result between profile frames reuses the preceding profile, never a future
# frame and never the other camera's profile.
pa13 = cam_a.get_last_profile(13)
pb13 = cam_b.get_last_profile(13)
assert pa13 is not None and pa13["frame_id"] == 10 and pa13["camera"] == "A"
assert pb13 is not None and pb13["frame_id"] == 10 and pb13["camera"] == "B"
assert cam_a.get_last_profile(16)["camera"] == "A"
assert cam_b.get_last_profile(16)["camera"] == "B"


# ----------------------------------------------------------------------
# FFT: dominant frequency is preserved, amplitude is intuitive [µm], and DC is
# omitted entirely from returned frequency bins.
# ----------------------------------------------------------------------
buf = TimeSeriesBuffer(max_points=10000)
fs = 100.0
freq0 = 7.0
amp_x = 3.0
base_ns = 10_000_000_000
for i in range(1000):
    t = i / fs
    rr = SimpleNamespace(
        timestamp_ns=base_ns + int(t * 1e9),
        cx_um=25.0 + amp_x * math.sin(2.0 * math.pi * freq0 * t),
        cy_um=-12.0 + 0.5 * math.sin(2.0 * math.pi * 3.0 * t),
        d4sigma_x_um=100.0,
        d4sigma_y_um=110.0,
        integrated=1000.0,
        peak=200.0,
    )
    buf.append_result(rr)

fft = buf.pointing_fft(None)
f = np.asarray(fft["f"], dtype=np.float64)
ax = np.asarray(fft["amp_x"], dtype=np.float64)
assert len(f) > 10
assert np.all(f > 0.0), f[:3]
peak_i = int(np.argmax(ax))
peak_f = float(f[peak_i])
assert abs(peak_f - freq0) < 0.2, peak_f
assert abs(float(ax[peak_i]) - amp_x) < 0.2, ax[peak_i]

print("v0.8.6 decoupled profile cadence, multi-camera association, and DC-free FFT tests: PASS")
