import math
import time
from types import SimpleNamespace

import numpy as np

from laserwatch.analysis import BeamAnalyzer
from laserwatch.exposure_optimizer import ExposureOptimizer
from laserwatch.models import CameraSettings
from laserwatch.profile_utils import profile_key
from laserwatch.timeseries import TimeSeriesBuffer

# ----------------------------------------------------------------------
# Auto exposure state machine
# ----------------------------------------------------------------------

opt = ExposureOptimizer(
    valid_raw_values=list(range(-12, -1)),
    target_fraction=0.80,
    settle_frames=0,
    required_good_frames=2,
    max_iterations=10,
)
opt.start(-8)

def result(frac, sat=0.0):
    return SimpleNamespace(
        full_scale=255.0,
        raw_peak=255.0 * frac,
        saturation_fraction=sat,
        raw_peak_fraction=frac,
    )

# Too dim -> longer exposure.
d = opt.feed(result(0.20))
assert d.new_raw is not None and d.new_raw > -8, d

# Simulate target result twice -> lock.
d = opt.feed(result(0.80))
assert not d.done
d = opt.feed(result(0.81))
assert d.done and d.success, d

# Saturation -> shorter.
opt.start(-5)
d = opt.feed(result(1.0, sat=0.01))
assert d.new_raw is not None and d.new_raw < -5, d

# ----------------------------------------------------------------------
# BeamResult raw full-scale path
# ----------------------------------------------------------------------

img = np.zeros((100, 120), dtype=np.uint8)
img[45:55, 55:65] = 200

an = BeamAnalyzer(CameraSettings(pixel_size_um_x=1.0, pixel_size_um_y=1.0))
an.settings.threshold_fraction = 0.0
an.settings.low_signal_fraction = 0.0
r = an.analyze(img, time.perf_counter_ns(), 1)

assert r.raw_peak == 200.0
assert r.full_scale == 255.0
assert abs(r.raw_peak_fraction - 200.0 / 255.0) < 1e-12

# ----------------------------------------------------------------------
# Stability statistics
# ----------------------------------------------------------------------

buf = TimeSeriesBuffer(max_points=100)
base = 1_000_000_000

for i, (x, y, inten) in enumerate([
    (0.0, 0.0, 100.0),
    (1.0, 0.0, 110.0),
    (-1.0, 0.0, 90.0),
]):
    rr = SimpleNamespace(
        timestamp_ns=base + i * 1_000_000_000,
        cx_um=x,
        cy_um=y,
        d4sigma_x_um=100.0 + i,
        d4sigma_y_um=120.0 + i,
        integrated=inten,
        peak=200.0,
    )
    buf.append_result(rr)

s = buf.statistics()
assert s["count"] == 3
assert abs(s["mean_x"]) < 1e-12
assert abs(s["ptp_x"] - 2.0) < 1e-12
assert math.isfinite(s["radial_rms"])
assert math.isfinite(s["intensity_cv_percent"])

# ----------------------------------------------------------------------
# Stable profile key
# ----------------------------------------------------------------------

k1 = profile_key(r"\\?\usb#vid_1234&pid_5678#ABC")
k2 = profile_key(r"\\?\usb#vid_1234&pid_5678#ABC")
k3 = profile_key(r"\\?\usb#vid_1234&pid_5678#XYZ")
assert k1 == k2
assert k1 != k3
assert len(k1) == 20

print("v0.5 auto exposure, raw peak, stability stats, and profile-key tests: PASS")
