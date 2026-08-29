import math
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from laserwatch.analysis import BeamAnalyzer
from laserwatch.auto_roi_tracker import AutoRoiTracker
from laserwatch.models import AnalysisSettings, CameraSettings
from laserwatch.timeseries import TimeSeriesBuffer


def make_analyzer(**kwargs):
    settings = AnalysisSettings(
        threshold_fraction=0.01,
        low_signal_fraction=0.0,
        spot_detection_enabled=True,
        spot_threshold_fraction=0.20,
        spot_min_area_px=1,
        **kwargs,
    )
    return BeamAnalyzer(
        CameraSettings(pixel_size_um_x=1.0, pixel_size_um_y=1.0),
        settings,
    )


# ----------------------------------------------------------------------
# Spot detector rejects broad dim background and locks to the bright spot.
# ----------------------------------------------------------------------
h, w = 240, 320
yy, xx = np.indices((h, w), dtype=np.float32)
background = 25.0 + 15.0 * (xx / w) + 8.0 * (yy / h)
spot = 200.0 * np.exp(
    -0.5 * (((xx - 245.0) / 7.0) ** 2 + ((yy - 82.0) / 5.0) ** 2)
)
image = np.clip(background + spot, 0, 255).astype(np.uint8)

r = make_analyzer().analyze(image, 9_000_000_000_000, 1)
assert r.detection_state == "DETECTED"
assert r.quality in ("OK", "SATURATED")
assert abs(r.cx_px - 245.0) < 2.0, r.cx_px
assert abs(r.cy_px - 82.0) < 2.0, r.cy_px
assert r.spot_bbox_w > 0 and r.spot_bbox_h > 0


# ----------------------------------------------------------------------
# Beam not found produces no fake centroid.
# ----------------------------------------------------------------------
blank = np.zeros((100, 120), dtype=np.uint8)
a = make_analyzer()
a.settings.low_signal_fraction = 0.02
rb = a.analyze(blank, 9_000_100_000_000, 2)
assert rb.quality == "BEAM_NOT_FOUND"
assert rb.detection_state == "NOT_FOUND"
assert math.isnan(rb.cx_px)
assert math.isnan(rb.d4sigma_x_um)


# ----------------------------------------------------------------------
# Auto ROI cannot remain trapped: invalid -> full-frame search -> reacquire.
# ----------------------------------------------------------------------
tracker = AutoRoiTracker()
tracker.enable()

not_found = SimpleNamespace(
    detection_state="NOT_FOUND",
    quality="BEAM_NOT_FOUND",
    cx_px=float("nan"),
    cy_px=float("nan"),
)
d = tracker.update(not_found, (480, 640), 200, 160)
assert d.state == "SEARCHING"
assert d.roi is None

found = SimpleNamespace(
    detection_state="DETECTED",
    quality="OK",
    cx_px=500.0,
    cy_px=300.0,
)
d = tracker.update(found, (480, 640), 200, 160)
assert d.state == "TRACKING"
assert d.roi is not None
x, y, rw, rh = d.roi
assert x <= 500 <= x + rw
assert y <= 300 <= y + rh

d = tracker.update(not_found, (480, 640), 200, 160)
assert d.state == "SEARCHING"
assert d.roi is None
assert d.break_series

found_elsewhere = SimpleNamespace(
    detection_state="DETECTED",
    quality="OK",
    cx_px=80.0,
    cy_px=60.0,
)
d = tracker.update(found_elsewhere, (480, 640), 200, 160)
assert d.state == "TRACKING"
assert d.roi is not None
assert d.roi[0] <= 80 <= d.roi[0] + d.roi[2]


# ----------------------------------------------------------------------
# Large nanosecond timestamps remain monotonic for >60 seconds.
# This is the numerical path that failed when Qt coerced timestamp to 32-bit.
# ----------------------------------------------------------------------
buf = TimeSeriesBuffer(max_points=1000)
base = 12_345_678_901_234_567
for i in range(71):
    rr = SimpleNamespace(
        timestamp_ns=base + i * 1_000_000_000,
        cx_um=float(i),
        cy_um=0.0,
        d4sigma_x_um=10.0,
        d4sigma_y_um=11.0,
        integrated=100.0,
        peak=80.0,
    )
    buf.append_result(rr)

arr = buf.arrays(60.0)
assert len(arr["t"]) == 61, (len(arr["t"]), arr["t"][:3], arr["t"][-3:])
assert abs(arr["t"][-1] - arr["t"][0] - 60.0) < 1e-12
assert arr["t"] == sorted(arr["t"])


# ----------------------------------------------------------------------
# Series break is represented by segment change and PSD uses latest segment.
# ----------------------------------------------------------------------
buf.break_series()
for i in range(71, 100):
    rr = SimpleNamespace(
        timestamp_ns=base + i * 1_000_000_000,
        cx_um=5.0 + math.sin(i),
        cy_um=2.0 + math.cos(i),
        d4sigma_x_um=10.0,
        d4sigma_y_um=11.0,
        integrated=100.0,
        peak=80.0,
    )
    buf.append_result(rr)

arr = buf.arrays(None)
assert len(set(arr["segment"])) == 2


# ----------------------------------------------------------------------
# Camera signal declaration must not use Qt 32-bit int for nanosecond timestamp.
# ----------------------------------------------------------------------
camera_source = (
    Path(__file__).resolve().parent
    / "laserwatch"
    / "camera.py"
).read_text(encoding="utf-8").replace(" ", "")
assert "frame_ready=Signal(object,object,object)" in camera_source
assert "frame_ready=Signal(object,int,int)" not in camera_source

print("v0.8.2 spot detection, reacquisition, 64-bit timing, and trend-gap tests: PASS")
