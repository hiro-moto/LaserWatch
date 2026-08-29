import math
import time
from pathlib import Path

import numpy as np

from laserwatch.analysis import BeamAnalyzer
from laserwatch.models import AnalysisSettings, CameraSettings


# Synthetic Gaussian beam: verify centroid cross sections correspond to same frame.
h, w = 180, 240
cx, cy = 150.3, 81.7
sx, sy = 9.0, 14.0
yy, xx = np.indices((h, w), dtype=np.float32)
img = 4.0 + 200.0 * np.exp(
    -0.5 * (((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2)
)
img = np.clip(img, 0, 255).astype(np.uint8)

settings = AnalysisSettings(
    threshold_fraction=0.01,
    low_signal_fraction=0.0,
    spot_detection_enabled=True,
    spot_threshold_fraction=0.15,
)
cam = CameraSettings(pixel_size_um_x=2.0, pixel_size_um_y=3.0)
an = BeamAnalyzer(cam, settings)

frame_id = 123
r = an.analyze(img, time.perf_counter_ns(), frame_id)
p = an.get_last_profile(frame_id)

assert p is not None
assert p["frame_id"] == frame_id
assert len(p["x_um"]) == w
assert len(p["y_um"]) == h
assert len(p["x_intensity"]) == w
assert len(p["y_intensity"]) == h
assert p["strip_width_px"] == 5

# Axes are centered on measured centroid.
ix = int(np.argmin(np.abs(p["x_um"])))
iy = int(np.argmin(np.abs(p["y_um"])))
assert abs(p["x_um"][ix]) <= cam.pixel_size_um_x
assert abs(p["y_um"][iy]) <= cam.pixel_size_um_y

# Profile maxima should be close to centroid.
mx = int(np.argmax(p["x_intensity"]))
my = int(np.argmax(p["y_intensity"]))
assert abs(mx - cx) < 2.0, (mx, cx)
assert abs(my - cy) < 2.0, (my, cy)

# Gaussian-equivalent curves are finite around zero and peak there.
assert np.isfinite(p["x_gaussian"][ix])
assert np.isfinite(p["y_gaussian"][iy])

# Stale frame IDs must not return a profile.
assert an.get_last_profile(frame_id + 1) is None

# Static UI checks.
root = Path(__file__).resolve().parent
cp = (root / "laserwatch" / "camera_panel.py").read_text(encoding="utf-8")
bp = (root / "laserwatch" / "beam_profile_widget.py").read_text(encoding="utf-8")
assert "green box = detected spot candidate (not beam diameter)" in cp
assert 'insertTab(3, self.beam_profile, "Profile")' in cp
assert "Gaussian-equivalent" in bp
assert cp.count("self._load_uvc_controls()") == 1

print("v0.8.3 overlay legend and centroid cross-section profile tests: PASS")
