from pathlib import Path

root = Path(__file__).resolve().parent
cp = (root / "laserwatch" / "camera_panel.py").read_text(encoding="utf-8")
tw = (root / "laserwatch" / "trend_widget.py").read_text(encoding="utf-8")
iv = (root / "laserwatch" / "image_view.py").read_text(encoding="utf-8")

assert "Dark corrected (auto scale)" in cp
assert "AutoRoiTracker" in cp
assert "SEARCHING (full frame)" in cp
assert "BEAM_NOT_FOUND" in cp
assert "setXRange(-float(self._window_s), 0.0" in tw
assert 'connect="finite"' in tw
assert "spot_bbox_x" in iv
assert "BEAM NOT FOUND / SEARCHING" in iv

print("v0.8.2 GUI/static regression checks: PASS")
