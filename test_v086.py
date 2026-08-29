import math
from types import SimpleNamespace

import numpy as np

from laserwatch.timeseries import TimeSeriesBuffer
from laserwatch.workers import AnalysisThread


# ----------------------------------------------------------------------
# Multi-camera beam-profile association: a GUI backlog must not allow a newer
# analyzer profile to replace/clear the profile belonging to an older result.
# ----------------------------------------------------------------------
class FakeAnalyzer:
    def __init__(self, camera_name):
        self.camera_name = camera_name
        self._last_profile = None

    def analyze(self, frame, timestamp_ns, frame_id):
        self._last_profile = {
            "frame_id": int(frame_id),
            "camera": self.camera_name,
            "x_intensity": np.asarray([frame_id], dtype=np.float64),
        }
        return SimpleNamespace(frame_id=int(frame_id))

    def get_last_profile(self, frame_id=None):
        data = self._last_profile
        if data is None:
            return None
        if frame_id is not None and int(data["frame_id"]) != int(frame_id):
            return None
        return dict(data)


cam_a = FakeAnalyzer("A")
cam_b = FakeAnalyzer("B")
worker_a = AnalysisThread(cam_a)
worker_b = AnalysisThread(cam_b)

# Simulate each analysis worker producing frame 10, snapshotting it, then racing
# ahead to frame 11 before the GUI asks for frame 10's profile.
for worker, analyzer in ((worker_a, cam_a), (worker_b, cam_b)):
    analyzer.analyze(None, 0, 10)
    worker._cache_profile_for_frame(10)
    analyzer.analyze(None, 0, 11)
    worker._cache_profile_for_frame(11)

pa10 = cam_a.get_last_profile(10)
pb10 = cam_b.get_last_profile(10)
assert pa10 is not None and pa10["frame_id"] == 10 and pa10["camera"] == "A"
assert pb10 is not None and pb10["frame_id"] == 10 and pb10["camera"] == "B"
assert cam_a.get_last_profile(11)["camera"] == "A"
assert cam_b.get_last_profile(11)["camera"] == "B"


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
# Coherent-gain correction should recover the injected peak amplitude closely.
assert abs(float(ax[peak_i]) - amp_x) < 0.2, ax[peak_i]

print("v0.8.6 multi-camera profile association and DC-free FFT tests: PASS")
