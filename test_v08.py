import time
import numpy as np

from laserwatch.analysis import BeamAnalyzer, full_scale_for_frame
from laserwatch.dark_capture import DarkFrameAccumulator
from laserwatch.models import AnalysisSettings, CameraSettings


def analyzer(channel="AUTO", bits=0):
    settings = AnalysisSettings(
        analysis_channel=channel,
        bit_depth_override=bits,
        threshold_fraction=0.0,
        low_signal_fraction=0.0,
    )
    return BeamAnalyzer(
        CameraSettings(pixel_size_um_x=1.0, pixel_size_um_y=1.0),
        settings,
    )


# ----------------------------------------------------------------------
# Mono is supported and color-only selections safely fall back to MONO.
# ----------------------------------------------------------------------
mono = np.zeros((40, 60), dtype=np.uint8)
mono[20, 30] = 200

r = analyzer("AUTO").analyze(mono, time.perf_counter_ns(), 1)
assert r.source_mode == "MONO"
assert r.analysis_channel == "MONO"
assert r.raw_peak == 200
assert r.full_scale == 255

r = analyzer("R").analyze(mono, time.perf_counter_ns(), 2)
assert r.analysis_channel == "MONO"


# ----------------------------------------------------------------------
# Color saturation cannot be hidden by grayscale conversion.
# One pure-red saturated pixel has Gray ~76 in OpenCV, but exposure metric
# must still see R=255 and report saturation.
# ----------------------------------------------------------------------
color = np.zeros((30, 40, 3), dtype=np.uint8)
color[15, 20, 2] = 255  # BGR -> red

r = analyzer("GRAY").analyze(color, time.perf_counter_ns(), 3)
assert r.source_mode == "COLOR"
assert r.analysis_channel == "GRAY"
assert r.raw_peak_r == 255
assert r.raw_peak_g == 0
assert r.raw_peak_b == 0
assert r.raw_peak == 255
assert r.raw_peak_fraction == 1.0
assert r.saturation_fraction > 0
assert r.saturation_fraction_r > 0
assert r.saturation_fraction_g == 0
assert r.saturation_fraction_b == 0
assert r.quality == "SATURATED"
assert r.analysis_raw_peak < 255  # grayscale did not preserve raw R saturation


# ----------------------------------------------------------------------
# R/G analysis channels produce different centroids when beams differ.
# ----------------------------------------------------------------------
color = np.zeros((50, 80, 3), dtype=np.uint8)
color[25, 15, 2] = 180  # red spot left
color[25, 65, 1] = 180  # green spot right

rr = analyzer("R").analyze(color, time.perf_counter_ns(), 4)
rg = analyzer("G").analyze(color, time.perf_counter_ns(), 5)
assert abs(rr.cx_px - 15) < 0.1
assert abs(rg.cx_px - 65) < 0.1
assert rr.analysis_channel == "R"
assert rg.analysis_channel == "G"


# ----------------------------------------------------------------------
# uint16 with explicit 12-bit effective depth uses full scale = 4095.
# Auto remains conservative at 65535.
# ----------------------------------------------------------------------
u16 = np.zeros((10, 10), dtype=np.uint16)
u16[5, 5] = 4095

full_auto, container_auto, effective_auto = full_scale_for_frame(u16, 0)
assert full_auto == 65535
assert container_auto == 16
assert effective_auto == 16

r12 = analyzer("AUTO", 12).analyze(u16, time.perf_counter_ns(), 6)
assert r12.full_scale == 4095
assert r12.container_bits == 16
assert r12.effective_bits == 12
assert r12.raw_peak_fraction == 1.0
assert r12.quality == "SATURATED"


# ----------------------------------------------------------------------
# Color dark averaging preserves BGR layout and can then be analyzed in R.
# ----------------------------------------------------------------------
acc = DarkFrameAccumulator()
acc.start(2)
dark1 = np.zeros((10, 12, 3), dtype=np.uint8)
dark2 = np.zeros((10, 12, 3), dtype=np.uint8)
dark1[..., 2] = 10
dark2[..., 2] = 14

assert acc.add_frame(dark1) is None
dark = acc.add_frame(dark2)
assert dark is not None
assert dark.shape == (10, 12, 3)
assert np.allclose(dark[..., 2], 12.0)

frame = np.zeros((10, 12, 3), dtype=np.uint8)
frame[..., 2] = 12
frame[5, 6, 2] = 112

a = analyzer("R")
a.set_dark(dark)
rd = a.analyze(frame, time.perf_counter_ns(), 7)
assert abs(rd.cx_px - 6.0) < 0.1
assert abs(rd.cy_px - 5.0) < 0.1

print("v0.8 mono/color, RGB saturation, channel selection, bit depth, and color dark tests: PASS")
