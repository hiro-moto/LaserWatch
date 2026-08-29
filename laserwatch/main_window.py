from __future__ import annotations
import logging

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLabel, QComboBox
)

from .camera_panel import CameraPanel
from .models import CameraDevice, CameraSettings
from .windows_uvc import enumerate_video_devices, UVCUnavailable
from .sync_monitor import compute_sync_status
from .resources import app_icon_path

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LaserWatch 0.8.5")
        try:
            self.setWindowIcon(QIcon(str(app_icon_path())))
        except Exception:
            log.exception("Failed to set main-window icon")
        # Default is comfortable on Full-HD, but the window can now shrink
        # substantially on laptops because controls/plots no longer impose
        # a tall minimum size.
        self.resize(1280, 800)
        self.setMinimumSize(760, 520)
        self.settings_store = QSettings("LaserWatch", "LaserWatch")
        self.devices: list[CameraDevice] = []

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("UVC camera"))

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(360)
        bar.addWidget(self.device_combo)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_devices)
        add_btn = QPushButton("Add camera")
        add_btn.clicked.connect(self.add_camera)
        remove_btn = QPushButton("Remove current")
        remove_btn.clicked.connect(self.remove_current)

        bar.addWidget(refresh_btn)
        bar.addWidget(add_btn)
        bar.addWidget(remove_btn)
        bar.addStretch(1)
        self.sync_label = QLabel("Sync: add 2+ cameras")
        self.sync_label.setToolTip(
            "Latest-frame timestamp spread. UVC cameras are not hardware synchronized; "
            "this is a software timing monitor using time.perf_counter_ns()."
        )
        bar.addWidget(self.sync_label)
        layout.addLayout(bar)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.statusBar().showMessage(
            "LaserWatch 0.8.5: click-to-select fixed beam target."
        )
        self.refresh_devices()

        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(250)
        self.sync_timer.timeout.connect(self.update_sync_status)
        self.sync_timer.start()

    def update_sync_status(self):
        try:
            entries = []
            for i in range(self.tabs.count()):
                panel = self.tabs.widget(i)
                camera_thread = getattr(panel, "camera_thread", None)
                if camera_thread is None or not camera_thread.isRunning():
                    continue
                entries.append((
                    panel.settings.name,
                    getattr(panel, "last_capture_timestamp_ns", None),
                ))
            status = compute_sync_status(entries)
            if status is None:
                self.sync_label.setText("Sync: add/run 2+ cameras")
                return
            self.sync_label.setText(
                f"Latest-frame skew: {status.skew_ms:.2f} ms "
                f"({status.camera_count} cameras)"
            )
        except Exception:
            log.exception("Synchronization monitor update failed")
            self.sync_label.setText("Sync: error")

    def refresh_devices(self):
        previous_path = self.settings_store.value("last_device_path", "", type=str)
        self.device_combo.clear()
        self.devices = []

        try:
            self.devices = enumerate_video_devices()
            for dev in self.devices:
                label = dev.display_name
                if dev.instance_id:
                    label += f"  ({dev.instance_id})"
                self.device_combo.addItem(label, dev.persistent_id)

            if not self.devices:
                self.statusBar().showMessage("No DirectShow UVC cameras found.", 5000)
                return

            selected = 0
            if previous_path:
                for i, dev in enumerate(self.devices):
                    if dev.persistent_id == previous_path:
                        selected = i
                        break
            self.device_combo.setCurrentIndex(selected)
            self.statusBar().showMessage(f"Found {len(self.devices)} camera(s).", 3000)
        except Exception as exc:
            log.exception("Camera enumeration failed")
            self.statusBar().showMessage(
                f"DirectShow enumeration unavailable: {exc}", 8000
            )

    def add_camera(self):
        try:
            i = self.device_combo.currentIndex()
            if i < 0 or i >= len(self.devices):
                self.statusBar().showMessage("Select a camera first.", 4000)
                return

            dev = self.devices[i]
            self.settings_store.setValue("last_device_path", dev.persistent_id)

            # Do not allow the same physical DirectShow device twice.
            for tab_i in range(self.tabs.count()):
                panel = self.tabs.widget(tab_i)
                if panel.settings.device_path and panel.settings.device_path == dev.device_path:
                    self.tabs.setCurrentIndex(tab_i)
                    return
                if not dev.device_path and panel.settings.camera_index == dev.dshow_index:
                    self.tabs.setCurrentIndex(tab_i)
                    return

            settings = CameraSettings.from_device(dev)
            panel = CameraPanel(settings)
            self.tabs.addTab(panel, dev.friendly_name)
            self.tabs.setCurrentWidget(panel)
        except Exception as exc:
            log.exception("Failed to add camera")
            self.statusBar().showMessage(f"Add camera failed: {exc}", 5000)

    def remove_current(self):
        i = self.tabs.currentIndex()
        if i < 0:
            return
        panel = self.tabs.widget(i)
        try:
            panel.shutdown()
        except Exception:
            log.exception("Panel shutdown failed during remove")
        finally:
            self.tabs.removeTab(i)
            panel.deleteLater()

    def closeEvent(self, event):
        try:
            self.sync_timer.stop()
        except Exception:
            pass
        for i in range(self.tabs.count()):
            panel = self.tabs.widget(i)
            try:
                panel.shutdown()
            except Exception:
                log.exception("Panel shutdown failed during application close")
        event.accept()
