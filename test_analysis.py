import time
import numpy as np

from laserwatch.analysis import BeamAnalyzer
from laserwatch.models import CameraSettings


def make_gaussian(h=1080, w=1920, cx=930.2, cy=510.7, sx=65.0, sy=42.0):
    x = np.arange(w, dtype=np.float32)
    y = np.arange(h, dtype=np.float32)
    xx = (x - cx) / sx
    yy = (y - cy) / sy
    img = 10 + 220 * np.exp(-0.5 * (yy[:, None] ** 2 + xx[None, :] ** 2))
    return np.clip(img, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    img = make_gaussian()
    cam = CameraSettings(pixel_size_um_x=3.45, pixel_size_um_y=3.45)
    analyzer = BeamAnalyzer(cam)
    analyzer.settings.background_level = 10.0
    analyzer.settings.threshold_fraction = 0.0

    for _ in range(3):
        analyzer.analyze(img, time.perf_counter_ns(), 0)

    times = []
    result = None
    for i in range(30):
        t0 = time.perf_counter()
        result = analyzer.analyze(img, time.perf_counter_ns(), i)
        times.append((time.perf_counter() - t0) * 1000)

    print("Centroid px:", result.cx_px, result.cy_px)
    print("D4sigma um:", result.d4sigma_x_um, result.d4sigma_y_um)
    print("Median analysis time [ms]:", float(np.median(times)))
