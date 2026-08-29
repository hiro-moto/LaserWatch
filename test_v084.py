import math
import time
from pathlib import Path

import numpy as np

from laserwatch.analysis import BeamAnalyzer
from laserwatch.fixed_target import centered_roi, select_fixed_target
from laserwatch.models import AnalysisSettings, CameraSettings


# ----------------------------------------------------------------------
# Click near the weaker of two spots: local snap must select the clicked spot,
# not a brighter remote reflection.
# ----------------------------------------------------------------------
h, w = 240, 360
yy, xx = np.indices((h, w), dtype=np.float32)

left = 170.0 * np.exp(
    -0.5 * (((xx - 90.0) / 7.0) ** 2 + ((yy - 120.0) / 7.0) ** 2)
)
right = 250.0 * np.exp(
    -0.5 * (((xx - 290.0) / 8.0) ** 2 + ((yy - 120.0) / 8.0) ** 2)
)
img = np.clip(left + right, 0, 255).astype(np.uint8)

settings = AnalysisSettings(
    threshold_fraction=0.01,
    low_signal_fraction=0.0,
    spot_detection_enabled=True,
    spot_threshold_fraction=0.15,
    spot_min_area_px=1,
)
sel = select_fixed_target(
    img,
    None,
    settings,
    click_xy=(96.0, 123.0),
    roi_width=140,
    roi_height=120,
    snap_radius_px=50,
)

assert sel.snapped
assert abs(sel.target_x - 90.0) < 3.0, sel
assert abs(sel.target_y - 120.0) < 3.0, sel
x, y, rw, rh = sel.roi
assert x <= sel.target_x <= x + rw
assert y <= sel.target_y <= y + rh


# ----------------------------------------------------------------------
# No local spot -> keep exact click anchor, rather than jumping remotely.
# ----------------------------------------------------------------------
sel2 = select_fixed_target(
    img,
    None,
    settings,
    click_xy=(180.0, 30.0),
    roi_width=100,
    roi_height=100,
    snap_radius_px=20,
)
assert not sel2.snapped
assert abs(sel2.target_x - 180.0) < 1e-12
assert abs(sel2.target_y - 30.0) < 1e-12


# ----------------------------------------------------------------------
# Analyzer preferred target chooses nearer component inside a broad fixed ROI,
# even when the remote component is optically brighter.
# ----------------------------------------------------------------------
settings.roi = (40, 60, 280, 120)
settings.preferred_target_px = (90.0, 120.0)
an = BeamAnalyzer(
    CameraSettings(pixel_size_um_x=1.0, pixel_size_um_y=1.0),
    settings,
)
r = an.analyze(img, time.perf_counter_ns(), 1)
assert r.detection_state == "DETECTED"
assert abs(r.cx_px - 90.0) < 6.0, r.cx_px


# ----------------------------------------------------------------------
# Centered ROI remains centered on fixed target when resized.
# ----------------------------------------------------------------------
roi1 = centered_roi(90, 120, img.shape[:2], 100, 80)
roi2 = centered_roi(90, 120, img.shape[:2], 160, 140)
for roi in (roi1, roi2):
    x, y, rw, rh = roi
    assert x <= 90 <= x + rw
    assert y <= 120 <= y + rh


# ----------------------------------------------------------------------
# Static GUI checks: blue target, one-click mode, and mutual exclusion.
# ----------------------------------------------------------------------
root = Path(__file__).resolve().parent
iv = (root / "laserwatch" / "image_view.py").read_text(encoding="utf-8")
cp = (root / "laserwatch" / "camera_panel.py").read_text(encoding="utf-8")

assert "target_selected = Signal(tuple)" in iv
assert "Qt.blue" in iv
assert "Pick fixed target" in cp
assert "Click snap radius" in cp
assert "select_fixed_target(" in cp
assert "preferred_target_px" in cp
assert "FIXED TARGET" in cp
assert "blue + = fixed target anchor" in cp

print("v0.8.4 fixed-target click/snap/lock tests: PASS")
