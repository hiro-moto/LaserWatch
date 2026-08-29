import time
import numpy as np

from laserwatch.analysis import BeamAnalyzer
from laserwatch.dark_capture import DarkFrameAccumulator
from laserwatch.models import CameraSettings
from laserwatch.timeseries import TimeSeriesBuffer

# ---------- Multi-frame dark average ----------
acc = DarkFrameAccumulator()
acc.start(4)
for value in [10, 12, 14]:
    out = acc.add_frame(np.full((8, 9), value, dtype=np.uint16))
    assert out is None

out = acc.add_frame(np.full((8, 9), 16, dtype=np.uint16))
assert out is not None
assert out.dtype == np.float32
assert np.allclose(out, 13.0)

# ---------- ROI analysis ----------
h, w = 300, 400
cx, cy = 270.4, 130.7
sx, sy = 18.0, 12.0
x = np.arange(w, dtype=np.float32)
y = np.arange(h, dtype=np.float32)
img = 5 + 200 * np.exp(
    -0.5 * (((x - cx) / sx)[None, :] ** 2 + ((y - cy) / sy)[:, None] ** 2)
)
img = np.clip(img, 0, 255).astype(np.uint8)

cam = CameraSettings(pixel_size_um_x=2.0, pixel_size_um_y=2.0)
an = BeamAnalyzer(cam)
an.settings.background_level = 5.0
an.settings.threshold_fraction = 0.0
an.settings.roi = (220, 80, 100, 100)
res = an.analyze(img, time.perf_counter_ns(), 1)

assert abs(res.cx_px - cx) < 0.5, (res.cx_px, cx)
assert abs(res.cy_px - cy) < 0.5, (res.cy_px, cy)

# ---------- Trend buffer/window ----------
buf = TimeSeriesBuffer(max_points=10)
base = 1_000_000_000
for i in range(5):
    class R:
        timestamp_ns = base + i * 1_000_000_000
        cx_um = 10.0 + i
        cy_um = 20.0 + i
        d4sigma_x_um = 100.0
        d4sigma_y_um = 110.0
        integrated = 1234.0
        peak = 200.0
    buf.append_result(R())

arr = buf.arrays(window_s=2.1)
assert len(arr["t"]) == 3, arr["t"]
assert arr["x"] == [12.0, 13.0, 14.0]

print("v0.4 dark averaging, ROI analysis, and trend buffer tests: PASS")
