import json
import math
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np

from laserwatch.raw_recorder import HDF5FrameRecorder
from laserwatch.summary_export import build_measurement_summary, write_summary_json, write_summary_csv
from laserwatch.sync_monitor import compute_sync_status
from laserwatch.timeseries import TimeSeriesBuffer
from laserwatch.models import CameraSettings

# ----------------------------------------------------------------------
# HDF5 async raw recording
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    path = Path(td) / "raw.h5"
    rec = HDF5FrameRecorder(queue_size=8)
    rec.start(path)
    for i in range(5):
        frame = np.full((12, 16), i, dtype=np.uint16)
        assert rec.submit(frame, 1_000_000 + i * 1000, i)
    rec.stop(timeout_s=5.0)
    assert not rec.last_error, rec.last_error
    assert path.exists()
    with h5py.File(path, "r") as h5:
        assert h5["frames"].shape == (5, 12, 16)
        assert h5["frames"].dtype == np.uint16
        assert h5["timestamp_ns"].shape == (5,)
        assert h5["frame_id"][:].tolist() == [0, 1, 2, 3, 4]
        assert int(h5.attrs["frames_written"]) == 5

# ----------------------------------------------------------------------
# PSD: inject 5 Hz X pointing sine and verify dominant frequency vicinity
# ----------------------------------------------------------------------
buf = TimeSeriesBuffer(max_points=10000)
fs = 50.0
freq0 = 5.0
base_ns = 2_000_000_000
for i in range(500):
    t = i / fs
    rr = SimpleNamespace(
        timestamp_ns=base_ns + int(t * 1e9),
        cx_um=2.0 * math.sin(2 * math.pi * freq0 * t),
        cy_um=0.2 * math.sin(2 * math.pi * 2.0 * t),
        d4sigma_x_um=100.0,
        d4sigma_y_um=110.0,
        integrated=1000.0,
        peak=200.0,
    )
    buf.append_result(rr)
psd = buf.pointing_psd(None)
f = np.asarray(psd["f"])
px = np.asarray(psd["psd_x"])
assert len(f) > 10
peak_f = float(f[1:][np.argmax(px[1:])])
assert abs(peak_f - freq0) < 0.25, peak_f

# ----------------------------------------------------------------------
# Multi-camera timestamp spread
# ----------------------------------------------------------------------
sync = compute_sync_status([
    ("CamA", 10_000_000_000),
    ("CamB", 10_004_500_000),
    ("CamC", 10_002_000_000),
])
assert sync is not None
assert sync.camera_count == 3
assert abs(sync.skew_ms - 4.5) < 1e-12
assert sync.oldest_name == "CamA"
assert sync.newest_name == "CamB"

# ----------------------------------------------------------------------
# Summary JSON / CSV
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    camera = CameraSettings(name="TestCam")
    stats = buf.statistics(None)
    summary = build_measurement_summary(
        camera,
        stats,
        baseline={"x_um": 0.0, "y_um": 0.0},
        extra={"raw_frames_written": 5, "raw_frames_dropped": 0},
    )
    jp = write_summary_json(td / "summary.json", summary)
    cp = write_summary_csv(td / "summary.csv", summary)
    loaded = json.loads(jp.read_text(encoding="utf-8"))
    assert loaded["camera"]["name"] == "TestCam"
    assert loaded["recording"]["raw_frames_written"] == 5
    assert cp.exists() and cp.stat().st_size > 0

print("v0.6 HDF5, PSD, sync monitor, and summary export tests: PASS")
