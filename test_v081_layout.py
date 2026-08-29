from pathlib import Path

root = Path(__file__).resolve().parent

camera_panel = (root / "laserwatch" / "camera_panel.py").read_text(encoding="utf-8")
trend = (root / "laserwatch" / "trend_widget.py").read_text(encoding="utf-8")
image = (root / "laserwatch" / "image_view.py").read_text(encoding="utf-8")
main = (root / "laserwatch" / "main_window.py").read_text(encoding="utf-8")

assert "QScrollArea" in camera_panel
assert "right_scroll.setWidgetResizable(True)" in camera_panel
assert "setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)" in camera_panel
assert "QTabWidget" in trend
assert 'addTab(self.position_plot, "Pointing")' in trend
assert 'addTab(self.psd_plot, "PSD")' in trend
assert "self.setMinimumSize(320, 240)" in image
assert "self.setMinimumSize(760, 520)" in main
assert "self.setWindowTitle(" in main

print("v0.8.1 compact/resizable layout regression checks: PASS")
