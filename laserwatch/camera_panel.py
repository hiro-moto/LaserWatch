from __future__ import annotations

import logging
import math
import time
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QDoubleSpinBox, QGroupBox, QFileDialog, QMessageBox, QCheckBox,
    QSpinBox, QSplitter, QComboBox, QScrollArea, QSizePolicy
)

from .analysis import BeamAnalyzer
from .auto_roi_tracker import AutoRoiTracker
from .beam_profile_widget import BeamProfileWidget
from .camera import CameraThread
from .dark_capture import DarkFrameAccumulator
from .diagnostics import RateMeter, build_diagnostic_payload, diagnostics_to_text, disk_free_bytes, human_bytes, process_rss_bytes
from .diagnostics_dialog import DiagnosticsDialog
from .exposure_optimizer import ExposureOptimizer
from .fixed_target import centered_roi, select_fixed_target
from .image_view import ImageView
from .models import CameraSettings, AnalysisSettings
from .profile import CameraProfileStore
from .profile_exchange import build_profile_payload, identity_match, read_profile_json, write_profile_json
from .raw_recorder import HDF5FrameRecorder
from .report_export import write_html_report
from .session_stats import StreamingBeamStats
from .summary_export import build_measurement_summary, write_summary_json, write_summary_csv
from .session import SessionLogger
from .timeseries import TimeSeriesBuffer
from .trend_widget import TrendWidget
from .workers import AnalysisThread
from .windows_uvc import ExposureRange, prepare_manual_exposure, query_exposure_range

log = logging.getLogger(__name__)


class CameraPanel(QWidget):
    def __init__(self, settings: CameraSettings, parent=None):
        super().__init__(parent)

        self.settings = settings
        self.analysis_settings = AnalysisSettings()

        self.profile = CameraProfileStore(self.settings.persistent_id)
        self.profile.apply_camera_settings(self.settings)
        self.profile.apply_analysis_settings(self.analysis_settings)

        self.analyzer = BeamAnalyzer(self.settings, self.analysis_settings)

        self.camera_thread = None
        self.analysis_thread = AnalysisThread(self.analyzer, self)
        self.analysis_thread.result_ready.connect(self.on_result)
        self.analysis_thread.analysis_error.connect(self.on_error)
        self.analysis_thread.start()

        self.logger = SessionLogger()
        self.raw_recorder = HDF5FrameRecorder(queue_size=16)
        self.dark_accumulator = DarkFrameAccumulator()
        self.trend_buffer = TimeSeriesBuffer(max_points=20000)
        self.session_buffer = TimeSeriesBuffer(max_points=50000)
        self.session_stats = StreamingBeamStats()

        self.last_frame = None
        self.last_result = None
        self.last_capture_timestamp_ns = None
        self.last_capture_frame_id = 0
        self.origin_um = None
        self.baseline = None
        self._last_error_text = None
        self.exposure_info: ExposureRange | None = None
        self.exposure_optimizer: ExposureOptimizer | None = None
        self.auto_roi_tracker = AutoRoiTracker()
        self._last_valid_for_plot = False
        self.fixed_target_px = None
        self._target_pick_armed = False
        self.raw_segments: list[str] = []
        self._raw_counted_paths: set[str] = set()
        self.raw_completed_written = 0
        self.raw_completed_dropped = 0
        self.last_capture_info = {}
        self.acq_rate_meter = RateMeter()
        self.analysis_rate_meter = RateMeter()
        self.current_acq_fps = 0.0
        self.current_analysis_fps = 0.0
        self.min_free_disk_bytes = 2 * 1024**3
        self._last_disk_guard_s = 0.0

        self._build_ui()
        self._restore_ui_profile()
        self._load_uvc_controls()

        self.trend_timer = QTimer(self)
        self.trend_timer.setInterval(200)
        self.trend_timer.timeout.connect(self.refresh_trends)
        self.trend_timer.start()

        self.profile_timer = QTimer(self)
        self.profile_timer.setSingleShot(True)
        self.profile_timer.setInterval(500)
        self.profile_timer.timeout.connect(self.save_profile)

        self.health_timer = QTimer(self)
        self.health_timer.setInterval(1000)
        self.health_timer.timeout.connect(self.refresh_health)
        self.health_timer.start()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        top = QHBoxLayout()
        self.start_btn = QPushButton("Start camera")
        self.start_btn.clicked.connect(self.toggle_camera)
        self.log_btn = QPushButton("Start logging")
        self.log_btn.clicked.connect(self.toggle_logging)
        self.origin_btn = QPushButton("Set reference")
        self.origin_btn.clicked.connect(self.set_origin)
        self.snap_btn = QPushButton("Snapshot")
        self.snap_btn.clicked.connect(self.snapshot)
        self.diag_btn = QPushButton("Diagnostics")
        self.diag_btn.clicked.connect(self.show_diagnostics)

        for w in (self.start_btn, self.log_btn, self.origin_btn, self.snap_btn, self.diag_btn):
            top.addWidget(w)
        top.addStretch(1)
        root.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(True)

        image_side = QWidget()
        image_layout = QVBoxLayout(image_side)
        image_layout.setContentsMargins(2, 2, 2, 2)
        image_layout.setSpacing(3)
        image_side.setMinimumSize(340, 260)
        self.view = ImageView()
        self.view.roi_selected.connect(self.on_manual_roi_selected)
        self.view.target_selected.connect(self.on_fixed_target_selected)
        image_layout.addWidget(self.view, 1)
        overlay_legend = QLabel(
            "Overlay: green box = detected spot candidate (not beam diameter)  |  "
            "green + = measured intensity centroid  |  yellow box = analysis ROI  |  "
            "blue + = fixed target anchor"
        )
        overlay_legend.setWordWrap(True)
        image_layout.addWidget(overlay_legend)

        roi_help = QLabel("Drag on the image to define the analysis ROI.")
        image_layout.addWidget(roi_help)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setFrameShape(QScrollArea.NoFrame)
        right_scroll.setMinimumWidth(280)
        right_scroll.setMaximumWidth(520)
        right_scroll.setSizePolicy(
            QSizePolicy.Preferred,
            QSizePolicy.Expanding,
        )

        right_side = QWidget()
        right_side.setMinimumWidth(260)
        side = QVBoxLayout(right_side)
        side.setContentsMargins(4, 2, 6, 2)
        side.setSpacing(5)
        right_scroll.setWidget(right_side)

        # Camera ---------------------------------------------------------
        camera_box = QGroupBox("Camera")
        form = QFormLayout(camera_box)

        self.status_label = QLabel("STOPPED")
        self.identity_label = QLabel(self.settings.friendly_name or self.settings.name)
        self.identity_label.setWordWrap(True)

        self.exposure_ms = QDoubleSpinBox()
        self.exposure_ms.setRange(0.001, 10_000_000.0)
        self.exposure_ms.setDecimals(3)
        self.exposure_ms.setSuffix(" ms")
        self.exposure_ms.setValue(self.settings.exposure_us / 1000.0)
        self.exposure_ms.valueChanged.connect(self.change_exposure_ms)

        self.actual_exposure = QLabel("-")
        self.exposure_raw_label = QLabel(str(self.settings.exposure_raw))

        self.gain = QDoubleSpinBox()
        self.gain.setRange(0, 10_000)
        self.gain.setDecimals(2)
        self.gain.setValue(self.settings.gain)
        self.gain.valueChanged.connect(self.change_gain)

        self.pixel_x = QDoubleSpinBox()
        self.pixel_x.setRange(0.001, 10_000)
        self.pixel_x.setDecimals(6)
        self.pixel_x.setValue(self.settings.pixel_size_um_x)
        self.pixel_x.valueChanged.connect(self.change_pixel_x)

        self.pixel_y = QDoubleSpinBox()
        self.pixel_y.setRange(0.001, 10_000)
        self.pixel_y.setDecimals(6)
        self.pixel_y.setValue(self.settings.pixel_size_um_y)
        self.pixel_y.valueChanged.connect(self.change_pixel_y)

        self.mag = QDoubleSpinBox()
        self.mag.setRange(0.000001, 10_000)
        self.mag.setDecimals(6)
        self.mag.setValue(self.settings.magnification)
        self.mag.valueChanged.connect(self.change_mag)

        form.addRow("Status", self.status_label)
        form.addRow("Device", self.identity_label)
        form.addRow("Exposure", self.exposure_ms)
        form.addRow("Actual exposure", self.actual_exposure)
        form.addRow("Exposure raw", self.exposure_raw_label)
        form.addRow("Gain", self.gain)
        form.addRow("Pixel X [µm]", self.pixel_x)
        form.addRow("Pixel Y [µm]", self.pixel_y)
        form.addRow("Magnification", self.mag)
        side.addWidget(camera_box)

        # Image format / analysis channel -------------------------------
        image_box = QGroupBox("Image / channel")
        imf = QFormLayout(image_box)

        self.source_info = QLabel("Waiting for frame")
        self.source_info.setWordWrap(True)

        self.channel_combo = QComboBox()
        for label, value in [
            ("Auto (Mono / Gray)", "AUTO"),
            ("Gray", "GRAY"),
            ("Red", "R"),
            ("Green", "G"),
            ("Blue", "B"),
        ]:
            self.channel_combo.addItem(label, value)
        initial_channel = str(self.analysis_settings.analysis_channel).upper()
        idx = self.channel_combo.findData(initial_channel)
        self.channel_combo.setCurrentIndex(max(0, idx))
        self.channel_combo.currentIndexChanged.connect(
            self.on_analysis_channel_changed
        )

        self.bit_depth_combo = QComboBox()
        for label, value in [
            ("Auto (container)", 0),
            ("8 bit", 8),
            ("10 bit", 10),
            ("12 bit", 12),
            ("16 bit", 16),
        ]:
            self.bit_depth_combo.addItem(label, value)
        idx = self.bit_depth_combo.findData(
            int(self.analysis_settings.bit_depth_override)
        )
        self.bit_depth_combo.setCurrentIndex(max(0, idx))
        self.bit_depth_combo.currentIndexChanged.connect(
            self.on_bit_depth_changed
        )

        self.exposure_metric_info = QLabel(
            "Mono: raw peak. Color: max(R,G,B)."
        )
        self.exposure_metric_info.setWordWrap(True)

        imf.addRow("Detected source", self.source_info)
        imf.addRow("Analysis channel", self.channel_combo)
        imf.addRow("Effective bit depth", self.bit_depth_combo)
        imf.addRow("Exposure metric", self.exposure_metric_info)
        side.addWidget(image_box)

        # Auto exposure --------------------------------------------------
        ae_box = QGroupBox("Beam exposure optimizer")
        aef = QFormLayout(ae_box)

        self.ae_target = QDoubleSpinBox()
        self.ae_target.setRange(20.0, 95.0)
        self.ae_target.setDecimals(1)
        self.ae_target.setSuffix(" %")
        self.ae_target.setValue(80.0)
        self.ae_target.valueChanged.connect(self.schedule_profile_save)

        self.ae_btn = QPushButton("Auto Optimize Exposure")
        self.ae_btn.clicked.connect(self.toggle_auto_exposure)
        self.ae_status = QLabel("Idle")
        self.ae_status.setWordWrap(True)

        aef.addRow("Target raw peak", self.ae_target)
        aef.addRow(self.ae_btn)
        aef.addRow("Status", self.ae_status)
        side.addWidget(ae_box)

        # Spot detection -------------------------------------------------
        spot_box = QGroupBox("Spot detection")
        spf = QFormLayout(spot_box)

        self.spot_enabled = QCheckBox("Isolate principal bright spot")
        self.spot_enabled.setChecked(self.analysis_settings.spot_detection_enabled)
        self.spot_enabled.toggled.connect(self.on_spot_detection_changed)

        self.spot_threshold = QDoubleSpinBox()
        self.spot_threshold.setRange(1.0, 90.0)
        self.spot_threshold.setDecimals(1)
        self.spot_threshold.setSuffix(" % of peak")
        self.spot_threshold.setValue(
            100.0 * self.analysis_settings.spot_threshold_fraction
        )
        self.spot_threshold.valueChanged.connect(self.on_spot_threshold_changed)

        self.spot_min_area = QSpinBox()
        self.spot_min_area.setRange(1, 100000)
        self.spot_min_area.setValue(self.analysis_settings.spot_min_area_px)
        self.spot_min_area.valueChanged.connect(self.on_spot_min_area_changed)

        self.tracking_status = QLabel("OFF")
        self.tracking_status.setWordWrap(True)

        spf.addRow(self.spot_enabled)
        spf.addRow("Detection threshold", self.spot_threshold)
        spf.addRow("Minimum area [px]", self.spot_min_area)
        spf.addRow("ROI mode", self.tracking_status)
        side.addWidget(spot_box)

        # ROI ------------------------------------------------------------
        roi_box = QGroupBox("ROI")
        rf = QFormLayout(roi_box)

        self.roi_enabled = QCheckBox("Enable ROI")
        self.roi_enabled.toggled.connect(self.on_roi_enabled_changed)

        self.auto_roi = QCheckBox("Auto follow centroid")
        self.auto_roi.toggled.connect(self.on_auto_roi_changed)

        self.pick_target_btn = QPushButton("Pick fixed target")
        self.pick_target_btn.clicked.connect(self.toggle_fixed_target_pick)

        self.clear_target_btn = QPushButton("Clear fixed target")
        self.clear_target_btn.clicked.connect(self.clear_fixed_target)
        self.clear_target_btn.setEnabled(False)

        self.fixed_target_text = QLabel("None")
        self.fixed_target_text.setWordWrap(True)

        self.snap_radius = QSpinBox()
        self.snap_radius.setRange(4, 1000)
        self.snap_radius.setValue(80)
        self.snap_radius.setSuffix(" px")
        self.snap_radius.valueChanged.connect(self.schedule_profile_save)

        self.roi_width = QSpinBox()
        self.roi_width.setRange(16, 16384)
        self.roi_width.setValue(400)
        self.roi_width.valueChanged.connect(self.on_auto_roi_size_changed)

        self.roi_height = QSpinBox()
        self.roi_height.setRange(16, 16384)
        self.roi_height.setValue(400)
        self.roi_height.valueChanged.connect(self.on_auto_roi_size_changed)

        clear_roi_btn = QPushButton("Full frame")
        clear_roi_btn.clicked.connect(self.clear_roi)

        self.roi_text = QLabel("Full frame")
        self.roi_text.setWordWrap(True)

        rf.addRow(self.roi_enabled)
        rf.addRow(self.auto_roi)
        rf.addRow(self.pick_target_btn)
        rf.addRow(self.clear_target_btn)
        rf.addRow("Fixed target", self.fixed_target_text)
        rf.addRow("Click snap radius", self.snap_radius)
        rf.addRow("ROI width [px]", self.roi_width)
        rf.addRow("ROI height [px]", self.roi_height)
        rf.addRow("Current ROI", self.roi_text)
        rf.addRow(clear_roi_btn)
        side.addWidget(roi_box)

        # Dark -----------------------------------------------------------
        dark_box = QGroupBox("Dark frame")
        df = QFormLayout(dark_box)

        self.dark_frames = QSpinBox()
        self.dark_frames.setRange(1, 1000)
        self.dark_frames.setValue(32)
        self.dark_frames.valueChanged.connect(self.schedule_profile_save)

        self.dark_btn = QPushButton("Capture averaged dark")
        self.dark_btn.clicked.connect(self.capture_dark)

        self.clear_dark_btn = QPushButton("Clear dark")
        self.clear_dark_btn.clicked.connect(self.clear_dark)

        self.dark_status = QLabel("None")
        self.dark_status.setWordWrap(True)

        self.display_mode = QComboBox()
        self.display_mode.addItem("Raw", "RAW")
        self.display_mode.addItem("Dark corrected (auto scale)", "DARK_CORRECTED")
        self.display_mode.currentIndexChanged.connect(self.on_display_mode_changed)

        df.addRow("Display", self.display_mode)
        df.addRow("Frames", self.dark_frames)
        df.addRow(self.dark_btn)
        df.addRow(self.clear_dark_btn)
        df.addRow("Status", self.dark_status)
        side.addWidget(dark_box)

        # Recording ------------------------------------------------------
        recording_box = QGroupBox("Recording / export")
        recf = QFormLayout(recording_box)

        self.raw_enabled = QCheckBox("Record raw frames to HDF5")
        self.raw_enabled.toggled.connect(self.on_raw_enabled_changed)

        self.raw_every = QSpinBox()
        self.raw_every.setRange(1, 100000)
        self.raw_every.setValue(10)
        self.raw_every.setSuffix(" frame(s)")
        self.raw_every.valueChanged.connect(self.schedule_profile_save)

        self.raw_segment_mb = QSpinBox()
        self.raw_segment_mb.setRange(64, 65536)
        self.raw_segment_mb.setValue(2048)
        self.raw_segment_mb.setSuffix(" MiB")
        self.raw_segment_mb.valueChanged.connect(self.schedule_profile_save)

        self.raw_status = QLabel("Off")
        self.raw_status.setWordWrap(True)

        self.summary_btn = QPushButton("Export measurement summary")
        self.summary_btn.clicked.connect(self.export_summary)
        self.report_btn = QPushButton("Export HTML report")
        self.report_btn.clicked.connect(self.export_html_report)
        self.profile_export_btn = QPushButton("Export camera profile")
        self.profile_export_btn.clicked.connect(self.export_profile)
        self.profile_import_btn = QPushButton("Import camera profile")
        self.profile_import_btn.clicked.connect(self.import_profile)

        recf.addRow(self.raw_enabled)
        recf.addRow("Save every", self.raw_every)
        recf.addRow("Raw segment limit", self.raw_segment_mb)
        recf.addRow("Raw status", self.raw_status)
        recf.addRow(self.summary_btn)
        recf.addRow(self.report_btn)
        recf.addRow(self.profile_export_btn)
        recf.addRow(self.profile_import_btn)
        side.addWidget(recording_box)

        # Live metrics ---------------------------------------------------
        metrics = QGroupBox("Beam metrics")
        mf = QFormLayout(metrics)
        self.labels = {}

        for key, title in [
            ("quality", "Quality"),
            ("detection", "Spot detection"),
            ("spots", "Spot candidates"),
            ("x", "X [µm]"),
            ("y", "Y [µm]"),
            ("dx", "ΔX [µm]"),
            ("dy", "ΔY [µm]"),
            ("d4x", "D4σ X [µm]"),
            ("d4y", "D4σ Y [µm]"),
            ("fwhmx", "FWHM X [µm]"),
            ("fwhmy", "FWHM Y [µm]"),
            ("channel", "Analysis channel"),
            ("format", "Source format"),
            ("peak", "Analysis peak"),
            ("rawpeak", "Exposure peak [%FS]"),
            ("rpeak", "R peak [%FS]"),
            ("gpeak", "G peak [%FS]"),
            ("bpeak", "B peak [%FS]"),
            ("sat", "Any saturation [%px]"),
            ("rsat", "R saturation [%px]"),
            ("gsat", "G saturation [%px]"),
            ("bsat", "B saturation [%px]"),
        ]:
            lab = QLabel("-")
            self.labels[key] = lab
            mf.addRow(title, lab)

        side.addWidget(metrics)

        # Stability ------------------------------------------------------
        stability_box = QGroupBox("Stability")
        sf = QFormLayout(stability_box)
        self.stat_labels = {}

        for key, title in [
            ("n", "Samples"),
            ("sigma_x", "σ X [µm]"),
            ("sigma_y", "σ Y [µm]"),
            ("ptp_x", "P-P X [µm]"),
            ("ptp_y", "P-P Y [µm]"),
            ("radial_rms", "Radial RMS [µm]"),
            ("size_sigma", "D4σ fluct. [%]"),
            ("int_cv", "Intensity CV [%]"),
        ]:
            lab = QLabel("-")
            self.stat_labels[key] = lab
            sf.addRow(title, lab)

        side.addWidget(stability_box)

        # Alarm ----------------------------------------------------------
        alarm_box = QGroupBox("Alarm")
        alf = QFormLayout(alarm_box)

        self.alarm_enabled = QCheckBox("Enable reference alarms")
        self.alarm_enabled.toggled.connect(self.schedule_profile_save)

        self.pointing_limit = QDoubleSpinBox()
        self.pointing_limit.setRange(0.0, 1_000_000.0)
        self.pointing_limit.setDecimals(3)
        self.pointing_limit.setSuffix(" µm")
        self.pointing_limit.setValue(10.0)
        self.pointing_limit.valueChanged.connect(self.schedule_profile_save)

        self.size_limit = QDoubleSpinBox()
        self.size_limit.setRange(0.0, 1000.0)
        self.size_limit.setDecimals(2)
        self.size_limit.setSuffix(" %")
        self.size_limit.setValue(10.0)
        self.size_limit.valueChanged.connect(self.schedule_profile_save)

        self.intensity_limit = QDoubleSpinBox()
        self.intensity_limit.setRange(0.0, 1000.0)
        self.intensity_limit.setDecimals(2)
        self.intensity_limit.setSuffix(" %")
        self.intensity_limit.setValue(20.0)
        self.intensity_limit.valueChanged.connect(self.schedule_profile_save)

        self.alarm_status = QLabel("Reference not set")
        self.alarm_status.setWordWrap(True)

        alf.addRow(self.alarm_enabled)
        alf.addRow("Pointing radius", self.pointing_limit)
        alf.addRow("Beam-size change", self.size_limit)
        alf.addRow("Intensity change", self.intensity_limit)
        alf.addRow("State", self.alarm_status)
        side.addWidget(alarm_box)

        health_box = QGroupBox("Runtime health")
        hf = QFormLayout(health_box)
        self.health_labels = {}
        for key, title in [("acq_fps", "Acquisition FPS"), ("analysis_fps", "Analysis FPS"), ("analysis_drop", "Analysis drops"), ("raw_queue", "Raw queue"), ("memory", "Process memory"), ("disk", "Free disk")]:
            lab = QLabel("-")
            self.health_labels[key] = lab
            hf.addRow(title, lab)
        side.addWidget(health_box)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        side.addWidget(self.error_label)
        side.addStretch(1)

        splitter.addWidget(image_side)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([900, 360])

        self.trends = TrendWidget()
        self.trends.clear_button.clicked.connect(self.clear_trends)

        self.beam_profile = BeamProfileWidget()
        # Insert before PSD so beam-profile inspection sits next to the ordinary
        # beam-size/intensity views.
        self.trends.tabs.insertTab(3, self.beam_profile, "Profile")

        vertical_splitter = QSplitter(Qt.Vertical)
        vertical_splitter.setChildrenCollapsible(True)
        vertical_splitter.addWidget(splitter)
        vertical_splitter.addWidget(self.trends)
        vertical_splitter.setStretchFactor(0, 4)
        vertical_splitter.setStretchFactor(1, 2)
        vertical_splitter.setSizes([570, 250])

        root.addWidget(vertical_splitter, 1)

        note = QLabel(
            "v0.8.4: green box = detected spot; green + = measured centroid; yellow box "
            "= analysis ROI; blue + = fixed target anchor. Pick fixed target snaps a click "
            "to the nearest local spot and never reacquires outside the fixed ROI."
        )
        note.setWordWrap(True)
        note.setMaximumHeight(42)
        root.addWidget(note)

    # ------------------------------------------------------------------
    # Profile persistence
    # ------------------------------------------------------------------

    def _restore_ui_profile(self):
        try:
            self.ae_target.setValue(self.profile.get("ae_target_percent", 80.0, float))
            self.roi_enabled.setChecked(self.profile.get("roi_enabled", False, bool))
            self.auto_roi.setChecked(self.profile.get("auto_roi", False, bool))
            self.roi_width.setValue(self.profile.get("roi_width", 400, int))
            self.roi_height.setValue(self.profile.get("roi_height", 400, int))
            self.snap_radius.setValue(self.profile.get("snap_radius", 80, int))
            self.dark_frames.setValue(self.profile.get("dark_frames", 32, int))
            display_mode = self.profile.get("display_mode", "RAW", str)
            didx = self.display_mode.findData(display_mode)
            self.display_mode.setCurrentIndex(max(0, didx))
            self.raw_enabled.setChecked(self.profile.get("raw_enabled", False, bool))
            self.raw_every.setValue(self.profile.get("raw_every", 10, int))
            self.raw_segment_mb.setValue(self.profile.get("raw_segment_mb", 2048, int))
            self.alarm_enabled.setChecked(self.profile.get("alarm_enabled", False, bool))
            self.pointing_limit.setValue(
                self.profile.get("pointing_limit_um", 10.0, float)
            )
            self.size_limit.setValue(
                self.profile.get("size_limit_percent", 10.0, float)
            )
            self.intensity_limit.setValue(
                self.profile.get("intensity_limit_percent", 20.0, float)
            )

            if self.analysis_settings.roi is not None and self.roi_enabled.isChecked():
                self.view.set_roi(self.analysis_settings.roi)
                self._update_roi_text()
            elif not self.roi_enabled.isChecked():
                self.analysis_settings.roi = None

            self.view.set_roi_selection_enabled(not self.auto_roi.isChecked())
        except Exception:
            log.exception("Failed to restore camera UI profile")

    def schedule_profile_save(self, *_args):
        try:
            if hasattr(self, "profile_timer"):
                self.profile_timer.start()
        except Exception:
            log.exception("Could not schedule profile save")

    def save_profile(self):
        try:
            self.profile.save_core(self.settings, self.analysis_settings)
            self.profile.set("ae_target_percent", self.ae_target.value())
            self.profile.set("roi_enabled", self.roi_enabled.isChecked())
            self.profile.set("auto_roi", self.auto_roi.isChecked())
            self.profile.set("roi_width", self.roi_width.value())
            self.profile.set("roi_height", self.roi_height.value())
            self.profile.set("snap_radius", self.snap_radius.value())
            self.profile.set("dark_frames", self.dark_frames.value())
            self.profile.set("display_mode", self.display_mode.currentData())
            self.profile.set("raw_enabled", self.raw_enabled.isChecked())
            self.profile.set("raw_every", self.raw_every.value())
            self.profile.set("raw_segment_mb", self.raw_segment_mb.value())
            self.profile.set("alarm_enabled", self.alarm_enabled.isChecked())
            self.profile.set("pointing_limit_um", self.pointing_limit.value())
            self.profile.set("size_limit_percent", self.size_limit.value())
            self.profile.set(
                "intensity_limit_percent", self.intensity_limit.value()
            )
        except Exception:
            log.exception("Failed to save camera profile")

    # ------------------------------------------------------------------
    # UVC + camera lifecycle
    # ------------------------------------------------------------------

    def _load_uvc_controls(self):
        try:
            self.exposure_info = query_exposure_range(self.settings.camera_index)
            info = self.exposure_info

            requested_raw, requested_us = info.quantize_us(self.settings.exposure_us)

            self.exposure_ms.blockSignals(True)
            self.exposure_ms.setRange(
                max(info.min_us / 1000.0, 0.000001),
                info.max_us / 1000.0,
            )
            self.exposure_ms.setValue(requested_us / 1000.0)
            self.exposure_ms.blockSignals(False)

            self.settings.exposure_raw = requested_raw
            self.settings.exposure_us = requested_us
            self.actual_exposure.setText(
                f"Target {self._format_exposure(requested_us)}"
            )
            self.exposure_raw_label.setText(str(requested_raw))

            if not info.supports_manual:
                self.exposure_ms.setEnabled(False)
                self.ae_btn.setEnabled(False)
                self.on_error("Camera does not report manual exposure control.")
        except Exception as exc:
            self.exposure_info = None
            self.actual_exposure.setText("UVC range unavailable")
            self.ae_btn.setEnabled(False)
            log.warning("Physical exposure control unavailable: %s", exc)
            self.on_error(
                "Physical UVC exposure range unavailable; capture may still work."
            )

    @staticmethod
    def _format_exposure(us: float) -> str:
        if us < 1000.0:
            return f"{us:.1f} µs"
        if us < 1_000_000.0:
            return f"{us / 1000.0:.3f} ms"
        return f"{us / 1_000_000.0:.3f} s"

    def toggle_camera(self):
        try:
            if self.camera_thread is not None and self.camera_thread.isRunning():
                self._stop_camera()
                return

            if self.exposure_info is not None:
                try:
                    raw, actual_us, refreshed = prepare_manual_exposure(
                        self.settings.camera_index,
                        self.exposure_ms.value() * 1000.0,
                    )
                    self.settings.exposure_raw = raw
                    self.settings.exposure_us = actual_us
                    self.exposure_info = refreshed
                    self._show_actual_exposure(raw, actual_us)
                except Exception as exc:
                    self.on_error(f"Could not prepare manual exposure: {exc}")

            self.camera_thread = CameraThread(self.settings, self)
            self.camera_thread.frame_ready.connect(self.on_frame)
            self.camera_thread.camera_error.connect(self.on_error)
            self.camera_thread.status.connect(self.on_camera_status)
            self.camera_thread.actual_exposure_raw.connect(
                self.on_actual_exposure_raw
            )
            self.camera_thread.capture_info.connect(self.on_capture_info)
            self.camera_thread.finished.connect(self.camera_finished)
            self.camera_thread.start()
            self.start_btn.setText("Stop camera")
        except Exception as exc:
            log.exception("Failed to toggle camera")
            self.on_error(f"Camera start/stop failed: {exc}")

    def _stop_camera(self):
        if self.camera_thread is None:
            return

        try:
            self.cancel_auto_exposure("Cancelled: camera stopped")
            self.dark_accumulator.cancel()
            self.dark_status.setText("Cancelled")
            self.camera_thread.stop()

            if not self.camera_thread.wait(2000):
                log.warning("Camera thread did not stop within timeout")
                self.camera_thread.terminate()
                self.camera_thread.wait(500)
        except Exception:
            log.exception("Failed while stopping camera")
        finally:
            self.camera_thread = None
            self.start_btn.setText("Start camera")
            self.status_label.setText("STOPPED")

    def camera_finished(self):
        self.start_btn.setText("Start camera")

    def on_camera_status(self, status):
        self.status_label.setText(status)

    # ------------------------------------------------------------------
    # Frame/result path
    # ------------------------------------------------------------------

    def on_frame(self, frame, ts, frame_id):
        try:
            self.last_frame = frame
            self.last_capture_timestamp_ns = int(ts)
            self.last_capture_frame_id = int(frame_id)
            self._update_live_display(frame)

            if self.raw_recorder.active and frame_id % max(1, self.raw_every.value()) == 0:
                self.raw_recorder.submit(frame, ts, frame_id)
                self._check_raw_rotation_and_disk()

            if self.dark_accumulator.active:
                try:
                    dark = self.dark_accumulator.add_frame(frame)
                    count, target = self.dark_accumulator.progress

                    if dark is None:
                        self.dark_status.setText(f"Capturing {count}/{target}")
                    else:
                        self.analyzer.set_dark(dark)
                        self.dark_status.setText(
                            f"Active: averaged {self.dark_frames.value()} frames"
                        )
                        self.dark_btn.setEnabled(True)
                        if str(self.display_mode.currentData()) == "DARK_CORRECTED":
                            self._update_live_display(frame)
                except Exception as exc:
                    self.dark_accumulator.cancel()
                    self.dark_btn.setEnabled(True)
                    self.dark_status.setText("Failed")
                    self.on_error(f"Dark capture failed: {exc}")

            self.analysis_thread.submit(frame, ts, frame_id)
        except Exception as exc:
            log.exception("Frame handling failed")
            self.on_error(f"Frame handling failed: {exc}")

    def on_result(self, r):
        try:
            self.last_result = r
            self.view.set_result(r)

            self._update_auto_exposure(r)
            self._update_auto_roi_state(r)

            self.labels["quality"].setText(r.quality)
            self.labels["detection"].setText(r.detection_state)
            self.labels["spots"].setText(str(r.spot_count))

            valid_position = math.isfinite(r.cx_um) and math.isfinite(r.cy_um)
            self.labels["x"].setText(f"{r.cx_um:.3f}" if valid_position else "-")
            self.labels["y"].setText(f"{r.cy_um:.3f}" if valid_position else "-")

            if self.origin_um is None or not valid_position:
                self.labels["dx"].setText("-")
                self.labels["dy"].setText("-")
            else:
                self.labels["dx"].setText(
                    f"{r.cx_um - self.origin_um[0]:+.3f}"
                )
                self.labels["dy"].setText(
                    f"{r.cy_um - self.origin_um[1]:+.3f}"
                )

            self.labels["d4x"].setText(
                f"{r.d4sigma_x_um:.3f}" if math.isfinite(r.d4sigma_x_um) else "-"
            )
            self.labels["d4y"].setText(
                f"{r.d4sigma_y_um:.3f}" if math.isfinite(r.d4sigma_y_um) else "-"
            )
            self.labels["fwhmx"].setText(
                f"{r.fwhm_x_um:.3f}" if math.isfinite(r.fwhm_x_um) else "-"
            )
            self.labels["fwhmy"].setText(
                f"{r.fwhm_y_um:.3f}" if math.isfinite(r.fwhm_y_um) else "-"
            )
            self.labels["channel"].setText(r.analysis_channel)
            self.labels["format"].setText(
                f"{r.source_mode} {r.source_dtype}; "
                f"container={r.container_bits or '-'} bit, "
                f"effective={r.effective_bits or 'auto'} bit"
            )
            self.source_info.setText(
                f"{r.source_mode}, {r.source_channels} ch, {r.source_dtype}; "
                f"container {r.container_bits or '-'} bit / "
                f"effective {r.effective_bits or 'auto'} bit"
            )

            self.labels["peak"].setText(f"{r.peak:.1f}")
            self.labels["rawpeak"].setText(
                f"{100.0 * r.raw_peak_fraction:.1f}"
            )

            if r.source_mode == "COLOR":
                self.labels["rpeak"].setText(
                    f"{100.0 * r.raw_peak_r_fraction:.1f}"
                )
                self.labels["gpeak"].setText(
                    f"{100.0 * r.raw_peak_g_fraction:.1f}"
                )
                self.labels["bpeak"].setText(
                    f"{100.0 * r.raw_peak_b_fraction:.1f}"
                )
                self.labels["rsat"].setText(
                    f"{100.0 * r.saturation_fraction_r:.4f}"
                )
                self.labels["gsat"].setText(
                    f"{100.0 * r.saturation_fraction_g:.4f}"
                )
                self.labels["bsat"].setText(
                    f"{100.0 * r.saturation_fraction_b:.4f}"
                )
            else:
                for key in ("rpeak", "gpeak", "bpeak", "rsat", "gsat", "bsat"):
                    self.labels[key].setText("-")

            self.labels["sat"].setText(
                f"{100.0 * r.saturation_fraction:.4f}"
            )

            profile_data = self.analyzer.get_last_profile(r.frame_id)
            if profile_data is not None:
                self.beam_profile.set_profile(profile_data)
            else:
                self.beam_profile.clear()

            valid_for_plot = (
                r.detection_state in ("DETECTED", "DISABLED")
                and r.quality in ("OK", "SATURATED")
                and math.isfinite(r.cx_um)
                and math.isfinite(r.cy_um)
            )

            if valid_for_plot:
                self.trend_buffer.append_result(r)
                self._last_valid_for_plot = True
            elif self._last_valid_for_plot:
                self.trend_buffer.break_series()
                self._last_valid_for_plot = False

            self._evaluate_alarm(r)

            if self.logger.active:
                try:
                    if valid_for_plot:
                        self.session_buffer.append_result(r)
                        self.session_stats.add_result(r)
                    else:
                        self.session_buffer.break_series()
                    self.logger.write(r)
                except Exception as exc:
                    try:
                        self._finish_logging_session()
                    except Exception:
                        log.exception("Failed to finalize session after CSV write failure")
                    self.log_btn.setText("Start logging")
                    self.on_error(
                        f"Logging stopped after write failure: {exc}"
                    )
        except Exception as exc:
            log.exception("Result display failed")
            self.on_error(f"Result display failed: {exc}")

    # ------------------------------------------------------------------
    # Image / color channel
    # ------------------------------------------------------------------

    def on_analysis_channel_changed(self, _index):
        try:
            value = str(self.channel_combo.currentData() or "AUTO").upper()
            if value not in ("AUTO", "GRAY", "R", "G", "B"):
                value = "AUTO"
            self.analysis_settings.analysis_channel = value
            self.schedule_profile_save()
        except Exception as exc:
            log.exception("Analysis channel change failed")
            self.on_error(f"Analysis channel change failed: {exc}")

    def on_bit_depth_changed(self, _index):
        try:
            value = int(self.bit_depth_combo.currentData() or 0)
            if value not in (0, 8, 10, 12, 16):
                value = 0
            self.analysis_settings.bit_depth_override = value
            self.schedule_profile_save()
        except Exception as exc:
            log.exception("Bit-depth change failed")
            self.on_error(f"Bit-depth change failed: {exc}")

    # ------------------------------------------------------------------
    # Spot detection / display
    # ------------------------------------------------------------------

    def on_spot_detection_changed(self, enabled):
        try:
            self.analysis_settings.spot_detection_enabled = bool(enabled)
            if not enabled and self.auto_roi.isChecked():
                self.auto_roi.setChecked(False)
            self.schedule_profile_save()
        except Exception as exc:
            log.exception("Spot detection toggle failed")
            self.on_error(f"Spot detection toggle failed: {exc}")

    def on_spot_threshold_changed(self, value):
        self.analysis_settings.spot_threshold_fraction = float(value) / 100.0
        self.schedule_profile_save()

    def on_spot_min_area_changed(self, value):
        self.analysis_settings.spot_min_area_px = int(value)
        self.schedule_profile_save()

    def on_display_mode_changed(self, _index):
        try:
            if self.last_frame is not None:
                self._update_live_display(self.last_frame)
            self.schedule_profile_save()
        except Exception:
            log.exception("Display-mode update failed")

    def _update_live_display(self, frame):
        mode = str(self.display_mode.currentData() or "RAW")
        self.view.set_display_mode(
            "DARK CORRECTED" if mode == "DARK_CORRECTED" else "RAW"
        )
        if mode == "DARK_CORRECTED":
            self.view.set_frame(self.analyzer.dark_corrected_source(frame))
        else:
            self.view.set_frame(frame)

    # ------------------------------------------------------------------
    # Auto exposure
    # ------------------------------------------------------------------

    def toggle_auto_exposure(self):
        if self.exposure_optimizer is not None and self.exposure_optimizer.active:
            self.cancel_auto_exposure("Cancelled by user")
            return
        self.start_auto_exposure()

    def start_auto_exposure(self):
        try:
            if (
                self.camera_thread is None
                or not self.camera_thread.isRunning()
            ):
                self.on_error("Start the camera before Auto Optimize Exposure.")
                return

            if self.exposure_info is None:
                self.on_error("Physical exposure range is unavailable.")
                return

            optimizer = ExposureOptimizer(
                self.exposure_info.valid_raw_values(),
                target_fraction=self.ae_target.value() / 100.0,
                settle_frames=3,
                required_good_frames=2,
                max_iterations=16,
            )
            optimizer.start(self.settings.exposure_raw)
            self.exposure_optimizer = optimizer
            self.ae_btn.setText("Cancel Auto Exposure")
            self.ae_status.setText("Optimizing...")
        except Exception as exc:
            log.exception("Auto exposure start failed")
            self.on_error(f"Auto exposure start failed: {exc}")

    def cancel_auto_exposure(self, message="Cancelled"):
        if self.exposure_optimizer is not None:
            self.exposure_optimizer.cancel()
        self.exposure_optimizer = None
        if hasattr(self, "ae_btn"):
            self.ae_btn.setText("Auto Optimize Exposure")
        if hasattr(self, "ae_status"):
            self.ae_status.setText(message)

    def _update_auto_exposure(self, result):
        optimizer = self.exposure_optimizer
        if optimizer is None or not optimizer.active:
            return

        try:
            if result.quality == "BEAM_NOT_FOUND":
                self.ae_status.setText("Waiting for detected beam")
                return
            decision = optimizer.feed(result)

            self.ae_status.setText(
                f"{decision.message}; peak={100.0 * decision.peak_fraction:.1f}%"
            )

            if decision.new_raw is not None:
                raw = int(decision.new_raw)
                actual_us = ExposureRange.raw_to_us(raw)

                self.settings.exposure_raw = raw
                self.settings.exposure_us = actual_us
                self._show_actual_exposure(raw, actual_us)

                self.exposure_ms.blockSignals(True)
                self.exposure_ms.setValue(actual_us / 1000.0)
                self.exposure_ms.blockSignals(False)

                if self.camera_thread is not None:
                    self.camera_thread.set_exposure_raw(raw)

            if decision.done:
                status = decision.message
                if decision.success:
                    status = f"Complete: {status}"
                else:
                    status = f"Stopped: {status}"
                self.cancel_auto_exposure(status)
                self.schedule_profile_save()
        except Exception as exc:
            log.exception("Auto exposure update failed")
            self.cancel_auto_exposure("Failed")
            self.on_error(f"Auto exposure failed: {exc}")

    # ------------------------------------------------------------------
    # Fixed target / click-to-select
    # ------------------------------------------------------------------

    def toggle_fixed_target_pick(self):
        try:
            if self._target_pick_armed:
                self._cancel_target_pick()
                return

            if self.last_frame is None:
                self.on_error("Start the camera before selecting a fixed target.")
                return

            if not self.spot_enabled.isChecked():
                self.spot_enabled.setChecked(True)

            # Fixed target and Auto ROI are mutually exclusive.
            if self.auto_roi.isChecked():
                self.auto_roi.setChecked(False)

            self._target_pick_armed = True
            self.pick_target_btn.setText("Cancel target selection")
            self.fixed_target_text.setText(
                "Click near the desired spot in the live image..."
            )
            self.view.set_target_selection_enabled(True)
        except Exception as exc:
            log.exception("Could not arm fixed-target selection")
            self.on_error(f"Fixed-target selection failed: {exc}")

    def _cancel_target_pick(self):
        self._target_pick_armed = False
        if hasattr(self, "pick_target_btn"):
            self.pick_target_btn.setText("Pick fixed target")
        if hasattr(self, "view"):
            self.view.set_target_selection_enabled(False)

    def on_fixed_target_selected(self, point):
        try:
            self._cancel_target_pick()
            if self.last_frame is None:
                self.on_error("No camera frame available for target selection.")
                return

            selection = select_fixed_target(
                self.last_frame,
                self.analyzer.get_dark_copy(),
                self.analysis_settings,
                point,
                self.roi_width.value(),
                self.roi_height.value(),
                snap_radius_px=self.snap_radius.value(),
            )

            self.auto_roi_tracker.disable()
            if self.auto_roi.isChecked():
                self.auto_roi.blockSignals(True)
                self.auto_roi.setChecked(False)
                self.auto_roi.blockSignals(False)

            self.roi_enabled.blockSignals(True)
            self.roi_enabled.setChecked(True)
            self.roi_enabled.blockSignals(False)

            self.fixed_target_px = (
                float(selection.target_x),
                float(selection.target_y),
            )
            self.analysis_settings.preferred_target_px = self.fixed_target_px
            self.analysis_settings.roi = tuple(selection.roi)

            self.view.set_fixed_target(self.fixed_target_px)
            self.view.set_roi(self.analysis_settings.roi)
            self.view.set_tracking_state("FIXED")
            self.tracking_status.setText("FIXED TARGET")
            self.clear_target_btn.setEnabled(True)

            snap_text = "snapped to nearest spot" if selection.snapped else "no spot found; using click position"
            self.fixed_target_text.setText(
                f"x={selection.target_x:.1f}, y={selection.target_y:.1f} px "
                f"({snap_text})"
            )
            self._update_roi_text()

            # Starting a new fixed target is a discontinuity in pointing history.
            self.trend_buffer.break_series()
            if self.logger.active:
                self.session_buffer.break_series()

            self.schedule_profile_save()
        except Exception as exc:
            log.exception("Fixed target selection failed")
            self.on_error(f"Fixed target selection failed: {exc}")

    def _clear_fixed_target_marker_only(self):
        self.fixed_target_px = None
        if hasattr(self, "view"):
            self.view.clear_fixed_target()
        if hasattr(self, "clear_target_btn"):
            self.clear_target_btn.setEnabled(False)
        if hasattr(self, "fixed_target_text"):
            self.fixed_target_text.setText("None")

    def clear_fixed_target(self):
        try:
            self._cancel_target_pick()
            self._clear_fixed_target_marker_only()
            self.analysis_settings.preferred_target_px = None

            # Keep the current yellow ROI as an ordinary manual ROI; only the
            # target-lock semantics are removed.
            if self.analysis_settings.roi is not None:
                self.tracking_status.setText("MANUAL ROI")
                self.view.set_tracking_state("MANUAL")
            else:
                self.tracking_status.setText("OFF")
                self.view.set_tracking_state("OFF")

            self.trend_buffer.break_series()
            if self.logger.active:
                self.session_buffer.break_series()
        except Exception:
            log.exception("Clear fixed target failed")

    # ------------------------------------------------------------------
    # ROI
    # ------------------------------------------------------------------

    def on_manual_roi_selected(self, roi):
        try:
            self._cancel_target_pick()
            self._clear_fixed_target_marker_only()
            self.analysis_settings.preferred_target_px = None

            self.auto_roi.blockSignals(True)
            self.auto_roi.setChecked(False)
            self.auto_roi.blockSignals(False)

            self.roi_enabled.blockSignals(True)
            self.roi_enabled.setChecked(True)
            self.roi_enabled.blockSignals(False)

            self.analysis_settings.roi = tuple(map(int, roi))
            self.view.set_roi(self.analysis_settings.roi)
            self.view.set_roi_selection_enabled(True)
            self.tracking_status.setText("MANUAL ROI")
            self.view.set_tracking_state("MANUAL")
            self._update_roi_text()
            self.schedule_profile_save()
        except Exception as exc:
            log.exception("Manual ROI failed")
            self.on_error(f"ROI selection failed: {exc}")

    def on_roi_enabled_changed(self, enabled):
        try:
            if not enabled:
                self.analysis_settings.roi = None
                self.analysis_settings.preferred_target_px = None
                self._cancel_target_pick()
                self._clear_fixed_target_marker_only()
                self.view.clear_roi()
                self.roi_text.setText("Full frame")
                self.tracking_status.setText("OFF")
                self.view.set_tracking_state("OFF")
                self.schedule_profile_save()
                return

            if self.analysis_settings.roi is None:
                if self.auto_roi.isChecked() and self.last_result is not None:
                    self._update_auto_roi_from_result(self.last_result)
                elif self.last_frame is not None:
                    h, w = self.last_frame.shape[:2]
                    rw = min(self.roi_width.value(), w)
                    rh = min(self.roi_height.value(), h)
                    roi = ((w-rw)//2, (h-rh)//2, rw, rh)
                    self.analysis_settings.roi = roi
                    self.view.set_roi(roi)
                    self._update_roi_text()
            self.schedule_profile_save()
        except Exception as exc:
            log.exception("ROI enable failed")
            self.on_error(f"ROI enable failed: {exc}")

    def on_auto_roi_changed(self, enabled):
        try:
            self.view.set_roi_selection_enabled(not enabled)
            if enabled:
                self._cancel_target_pick()
                self._clear_fixed_target_marker_only()
                self.analysis_settings.preferred_target_px = None
                if not self.spot_enabled.isChecked():
                    self.spot_enabled.setChecked(True)
                self.roi_enabled.setChecked(True)
                self.auto_roi_tracker.enable()
                self.analysis_settings.roi = None
                self.view.clear_roi()
                self.tracking_status.setText("SEARCHING (full frame)")
                self.view.set_tracking_state("SEARCHING")
                self.trend_buffer.break_series()
                if self.logger.active:
                    self.session_buffer.break_series()
            else:
                self.auto_roi_tracker.disable()
                if self.fixed_target_px is None:
                    self.tracking_status.setText("OFF")
                    self.view.set_tracking_state("OFF")
            self.schedule_profile_save()
        except Exception as exc:
            log.exception("Auto ROI toggle failed")
            self.on_error(f"Auto ROI toggle failed: {exc}")

    def on_auto_roi_size_changed(self, _value):
        try:
            if (
                self.fixed_target_px is not None
                and not self.auto_roi.isChecked()
                and self.last_frame is not None
            ):
                roi = centered_roi(
                    self.fixed_target_px[0],
                    self.fixed_target_px[1],
                    self.last_frame.shape[:2],
                    self.roi_width.value(),
                    self.roi_height.value(),
                )
                self.analysis_settings.roi = roi
                self.view.set_roi(roi)
                self._update_roi_text()
        except Exception:
            log.exception("Fixed ROI resize failed")
        self.schedule_profile_save()

    def _update_auto_roi_state(self, result):
        if not self.auto_roi.isChecked():
            return
        if self.last_frame is None:
            return

        try:
            decision = self.auto_roi_tracker.update(
                result,
                self.last_frame.shape[:2],
                self.roi_width.value(),
                self.roi_height.value(),
            )
            self.tracking_status.setText(
                "TRACKING"
                if decision.state == "TRACKING"
                else "SEARCHING (full frame)"
            )
            self.view.set_tracking_state(decision.state)

            if decision.break_series:
                self.trend_buffer.break_series()
                if self.logger.active:
                    self.session_buffer.break_series()

            if decision.roi is None:
                self.analysis_settings.roi = None
                self.view.clear_roi()
                self.roi_text.setText("Full frame search")
            else:
                self.analysis_settings.roi = decision.roi
                self.view.set_roi(decision.roi)
                self._update_roi_text()
        except Exception as exc:
            log.exception("Auto ROI tracking update failed")
            self.analysis_settings.roi = None
            self.auto_roi_tracker.enable()
            self.tracking_status.setText("SEARCHING after error")
            self.view.set_tracking_state("SEARCHING")
            self.on_error(f"Auto ROI tracking failed: {exc}")

    def clear_roi(self):
        self._cancel_target_pick()
        self._clear_fixed_target_marker_only()
        self.analysis_settings.preferred_target_px = None
        self.auto_roi_tracker.disable()
        self.auto_roi.setChecked(False)
        self.roi_enabled.setChecked(False)
        self.analysis_settings.roi = None
        self.view.clear_roi()
        self.view.set_roi_selection_enabled(True)
        self.roi_text.setText("Full frame")
        self.schedule_profile_save()

    def _update_roi_text(self):
        roi = self.analysis_settings.roi
        if roi is None:
            self.roi_text.setText("Full frame")
        else:
            x, y, w, h = roi
            self.roi_text.setText(f"x={x}, y={y}, w={w}, h={h}")

    # ------------------------------------------------------------------
    # Dark
    # ------------------------------------------------------------------

    def capture_dark(self):
        try:
            if self.last_frame is None:
                self.on_error("Cannot capture dark: no frame available.")
                return

            n = self.dark_frames.value()
            self.dark_accumulator.start(n)
            self.dark_btn.setEnabled(False)
            self.dark_status.setText(f"Capturing 0/{n}")
        except Exception as exc:
            log.exception("Dark capture start failed")
            self.on_error(f"Dark capture start failed: {exc}")

    def clear_dark(self):
        try:
            self.dark_accumulator.cancel()
            self.analyzer.clear_dark()
            self.dark_btn.setEnabled(True)
            self.dark_status.setText("None")
            if self.last_frame is not None:
                self._update_live_display(self.last_frame)
        except Exception as exc:
            log.exception("Clear dark failed")
            self.on_error(f"Clear dark failed: {exc}")

    # ------------------------------------------------------------------
    # Stability + alarms
    # ------------------------------------------------------------------

    def refresh_trends(self):
        try:
            self.trends.set_origin(self.origin_um)
            self.trends.update_from_buffer(self.trend_buffer)
            stats = self.trend_buffer.statistics(self.trends.window_s)
            self._show_statistics(stats)
            self._refresh_raw_status()
        except Exception:
            log.exception("Trend/statistics refresh failed")

    def _show_statistics(self, stats):
        if not stats or stats.get("count", 0) == 0:
            for label in self.stat_labels.values():
                label.setText("-")
            return

        self.stat_labels["n"].setText(str(stats["count"]))
        self.stat_labels["sigma_x"].setText(f'{stats["sigma_x"]:.3f}')
        self.stat_labels["sigma_y"].setText(f'{stats["sigma_y"]:.3f}')
        self.stat_labels["ptp_x"].setText(f'{stats["ptp_x"]:.3f}')
        self.stat_labels["ptp_y"].setText(f'{stats["ptp_y"]:.3f}')
        self.stat_labels["radial_rms"].setText(
            f'{stats["radial_rms"]:.3f}'
        )

        size_pct = float("nan")
        components = []
        for mean_key, sigma_key in (
            ("d4x_mean", "d4x_sigma"),
            ("d4y_mean", "d4y_sigma"),
        ):
            mean = stats.get(mean_key, float("nan"))
            sigma = stats.get(sigma_key, float("nan"))
            if math.isfinite(mean) and abs(mean) > 1e-15 and math.isfinite(sigma):
                components.append(100.0 * sigma / abs(mean))

        if components:
            size_pct = max(components)

        self.stat_labels["size_sigma"].setText(
            "-" if not math.isfinite(size_pct) else f"{size_pct:.3f}"
        )

        int_cv = stats.get("intensity_cv_percent", float("nan"))
        self.stat_labels["int_cv"].setText(
            "-" if not math.isfinite(int_cv) else f"{int_cv:.3f}"
        )

    def clear_trends(self):
        self.trend_buffer.clear()
        self.refresh_trends()

    def set_origin(self):
        if self.last_result is None:
            self.on_error("Cannot set reference: no valid measurement yet.")
            return

        r = self.last_result
        if r.quality == "BEAM_NOT_FOUND":
            self.on_error("Cannot set reference: beam is not detected.")
            return
        if not math.isfinite(r.cx_um) or not math.isfinite(r.cy_um):
            self.on_error("Cannot set reference from an invalid beam result.")
            return

        self.origin_um = (r.cx_um, r.cy_um)
        self.baseline = {
            "x_um": r.cx_um,
            "y_um": r.cy_um,
            "d4x_um": r.d4sigma_x_um,
            "d4y_um": r.d4sigma_y_um,
            "intensity": r.integrated,
        }
        self.trends.set_origin(self.origin_um)
        self.alarm_status.setText("Reference active")

    def _evaluate_alarm(self, r):
        if r.quality == "BEAM_NOT_FOUND":
            self.alarm_status.setText("WARNING: BEAM NOT FOUND")
            return

        if r.quality == "SATURATED":
            self.alarm_status.setText("WARNING: SATURATED")
            return

        if r.quality == "LOW_SIGNAL":
            self.alarm_status.setText("WARNING: LOW SIGNAL")
            return

        if not self.alarm_enabled.isChecked():
            self.alarm_status.setText(
                "Reference active; alarms disabled"
                if self.baseline is not None
                else "Reference not set"
            )
            return

        if self.baseline is None:
            self.alarm_status.setText("Reference not set")
            return

        warnings = []

        dx = r.cx_um - self.baseline["x_um"]
        dy = r.cy_um - self.baseline["y_um"]
        radial = math.hypot(dx, dy)

        if radial > self.pointing_limit.value():
            warnings.append(
                f"pointing {radial:.2f} µm"
            )

        size_changes = []
        for current, base in (
            (r.d4sigma_x_um, self.baseline["d4x_um"]),
            (r.d4sigma_y_um, self.baseline["d4y_um"]),
        ):
            if (
                math.isfinite(current)
                and math.isfinite(base)
                and abs(base) > 1e-15
            ):
                size_changes.append(100.0 * abs(current - base) / abs(base))

        if size_changes and max(size_changes) > self.size_limit.value():
            warnings.append(
                f"beam size {max(size_changes):.1f}%"
            )

        base_int = self.baseline["intensity"]
        if (
            math.isfinite(r.integrated)
            and math.isfinite(base_int)
            and abs(base_int) > 1e-15
        ):
            int_change = 100.0 * abs(r.integrated - base_int) / abs(base_int)
            if int_change > self.intensity_limit.value():
                warnings.append(f"intensity {int_change:.1f}%")

        if warnings:
            self.alarm_status.setText("WARNING: " + ", ".join(warnings))
        else:
            self.alarm_status.setText("OK")

    # ------------------------------------------------------------------
    # Camera controls / logging / files
    # ------------------------------------------------------------------

    def change_exposure_ms(self, value_ms):
        try:
            target_us = float(value_ms) * 1000.0
            if self.exposure_info is None:
                return

            raw, actual_us = self.exposure_info.quantize_us(target_us)
            self.settings.exposure_raw = raw
            self.settings.exposure_us = actual_us
            self._show_actual_exposure(raw, actual_us)

            if (
                self.camera_thread is not None
                and self.camera_thread.isRunning()
            ):
                self.camera_thread.set_exposure_raw(raw)

            self.schedule_profile_save()
        except Exception as exc:
            log.exception("Exposure change failed")
            self.on_error(f"Exposure change failed: {exc}")

    def on_actual_exposure_raw(self, raw):
        try:
            actual_us = ExposureRange.raw_to_us(int(raw))
            self.settings.exposure_raw = int(raw)
            self.settings.exposure_us = actual_us
            self._show_actual_exposure(int(raw), actual_us)
        except Exception:
            log.exception("Could not display actual exposure")

    def _show_actual_exposure(self, raw, actual_us):
        self.exposure_raw_label.setText(str(int(raw)))
        self.actual_exposure.setText(self._format_exposure(float(actual_us)))

    def toggle_logging(self):
        try:
            if self.logger.active:
                self._finish_logging_session()
                self.log_btn.setText("Start logging")
                return

            base = Path(
                QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
            ) / "LaserWatchData"

            path = self.logger.start(base, self.settings)
            self.session_buffer.clear()
            self.session_stats.reset()
            self.raw_recorder = HDF5FrameRecorder(queue_size=16)
            self.raw_segments = []
            self._raw_counted_paths = set()
            self.raw_completed_written = 0
            self.raw_completed_dropped = 0
            if self.raw_enabled.isChecked():
                self._start_raw_recorder()
            self.log_btn.setText("Stop logging")
            QMessageBox.information(
                self,
                "Logging",
                f"Session created:\n{path}",
            )
        except Exception as exc:
            log.exception("Logging start/stop failed")
            self.log_btn.setText("Start logging")
            self.on_error(f"Logging failed: {exc}")

    def on_raw_enabled_changed(self, enabled):
        try:
            if enabled and self.logger.active:
                self._start_raw_recorder()
            elif not enabled and (self.raw_recorder.active or self.raw_recorder.path is not None):
                self._stop_raw_recorder()
            self._refresh_raw_status()
            self.schedule_profile_save()
        except Exception as exc:
            log.exception("Raw-recording toggle failed")
            self.on_error(f"Raw recording failed: {exc}")

    def _start_raw_recorder(self):
        if self.raw_recorder.active:
            return
        if not self.logger.active or self.logger.dir is None:
            self.raw_status.setText("Waiting for measurement session")
            return

        base = self.logger.dir / "raw_frames.h5"
        path = base
        segment = 2
        while path.exists():
            path = self.logger.dir / f"raw_frames_{segment:03d}.h5"
            segment += 1

        self.raw_recorder.start(path)
        self.raw_status.setText(f"Recording: {path.name}")

    def _stop_raw_recorder(self):
        path = self.raw_recorder.path
        self.raw_recorder.stop()
        if self.raw_recorder.active:
            raise RuntimeError(self.raw_recorder.last_error or "Raw recorder did not stop")
        if path is not None:
            key = str(path)
            if key not in self._raw_counted_paths:
                self.raw_completed_written += int(self.raw_recorder.frames_written)
                self.raw_completed_dropped += int(self.raw_recorder.frames_dropped)
                self.raw_segments.append(key)
                self._raw_counted_paths.add(key)

    def _refresh_raw_status(self):
        if self.raw_recorder.active:
            self.raw_status.setText(
                f"Recording: {self.raw_recorder.frames_written} written, "
                f"{self.raw_recorder.frames_dropped} dropped"
            )
        elif self.raw_recorder.last_error:
            self.raw_status.setText(f"ERROR: {self.raw_recorder.last_error}")
        elif self.raw_enabled.isChecked() and self.logger.active:
            self.raw_status.setText("Enabled, recorder stopped")
        else:
            self.raw_status.setText("Off")

    def _summary_dict(self):
        stats = self.session_stats.statistics() if self.logger.dir is not None else self.trend_buffer.statistics(None)
        active_written = 0 if str(self.raw_recorder.path) in self._raw_counted_paths else int(self.raw_recorder.frames_written)
        active_dropped = 0 if str(self.raw_recorder.path) in self._raw_counted_paths else int(self.raw_recorder.frames_dropped)
        segments = list(self.raw_segments)
        if self.raw_recorder.path is not None and str(self.raw_recorder.path) not in segments:
            segments.append(str(self.raw_recorder.path))
        extra = {
            "raw_frames_written": int(self.raw_completed_written + active_written),
            "raw_frames_dropped": int(self.raw_completed_dropped + active_dropped),
            "raw_hdf5_segments": segments,
            "analysis_samples": int(stats.get("count", 0)),
            "analysis_channel_setting": str(self.analysis_settings.analysis_channel),
            "bit_depth_override": int(self.analysis_settings.bit_depth_override),
            "last_source_mode": (
                self.last_result.source_mode if self.last_result is not None else ""
            ),
            "last_source_dtype": (
                self.last_result.source_dtype if self.last_result is not None else ""
            ),
            "last_effective_bits": (
                self.last_result.effective_bits if self.last_result is not None else 0
            ),
        }
        return build_measurement_summary(
            self.settings,
            stats,
            baseline=self.baseline,
            extra=extra,
        )

    def _write_session_summary(self):
        if self.logger.dir is None:
            return
        summary = self._summary_dict()
        write_summary_json(self.logger.dir / "summary.json", summary)
        write_summary_csv(self.logger.dir / "summary.csv", summary)

    def _finish_logging_session(self):
        # Stop raw first so its final written/dropped counts are included in summary.
        try:
            self._stop_raw_recorder()
        except Exception:
            log.exception("Raw recorder stop failed")
        try:
            self._write_session_summary()
        except Exception:
            log.exception("Automatic summary export failed")
        try:
            self._write_session_report()
        except Exception:
            log.exception("Automatic HTML report export failed")
        self.logger.stop()
        self._refresh_raw_status()

    def export_summary(self):
        try:
            default_dir = (
                self.logger.dir
                if self.logger.dir is not None
                else Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))
            )
            default_path = str(default_dir / "LaserWatch_summary.json")
            filename, selected_filter = QFileDialog.getSaveFileName(
                self,
                "Export measurement summary",
                default_path,
                "JSON (*.json);;CSV (*.csv)",
            )
            if not filename:
                return
            summary = self._summary_dict()
            path = Path(filename)
            if path.suffix.lower() == ".csv" or selected_filter.startswith("CSV"):
                if path.suffix.lower() != ".csv":
                    path = path.with_suffix(".csv")
                write_summary_csv(path, summary)
            else:
                if path.suffix.lower() != ".json":
                    path = path.with_suffix(".json")
                write_summary_json(path, summary)
        except Exception as exc:
            log.exception("Summary export failed")
            self.on_error(f"Summary export failed: {exc}")

    def on_capture_info(self, info):
        try:
            self.last_capture_info = dict(info or {})
        except Exception:
            log.exception("Capture-info update failed")

    def refresh_health(self):
        try:
            cam = self.camera_thread
            captured = int(getattr(cam, "frames_captured", 0)) if cam is not None else 0
            processed = int(getattr(self.analysis_thread, "frames_processed", 0))
            self.current_acq_fps = self.acq_rate_meter.update(captured)
            self.current_analysis_fps = self.analysis_rate_meter.update(processed)
            self.health_labels["acq_fps"].setText(f"{self.current_acq_fps:.2f}")
            self.health_labels["analysis_fps"].setText(f"{self.current_analysis_fps:.2f}")
            self.health_labels["analysis_drop"].setText(str(int(getattr(self.analysis_thread, "frames_dropped", 0))))
            self.health_labels["raw_queue"].setText(f"{self.raw_recorder.queue_depth}/{self.raw_recorder.queue_size}")
            self.health_labels["memory"].setText(human_bytes(process_rss_bytes()))
            base = self.logger.dir if self.logger.dir is not None else Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))
            self.health_labels["disk"].setText(human_bytes(disk_free_bytes(base)))
            self._refresh_raw_status()
        except Exception:
            log.exception("Runtime health refresh failed")

    def diagnostic_payload(self):
        cam = self.camera_thread
        acquisition = {
            "acquisition_fps": self.current_acq_fps,
            "frames_captured": int(getattr(cam, "frames_captured", 0)) if cam else 0,
            "read_failures": int(getattr(cam, "read_failures", 0)) if cam else 0,
            "reconnect_count": int(getattr(cam, "reconnect_count", 0)) if cam else 0,
            "last_capture_frame_id": int(self.last_capture_frame_id),
        }
        analysis = {
            "analysis_fps": self.current_analysis_fps,
            "frames_submitted": int(self.analysis_thread.frames_submitted),
            "frames_processed": int(self.analysis_thread.frames_processed),
            "frames_dropped": int(self.analysis_thread.frames_dropped),
            "failures": int(self.analysis_thread.failures),
            "queue_depth": int(self.analysis_thread.queue_depth),
            "analysis_channel_setting": str(self.analysis_settings.analysis_channel),
            "bit_depth_override": int(self.analysis_settings.bit_depth_override),
            "last_source_mode": (
                self.last_result.source_mode if self.last_result is not None else ""
            ),
            "last_source_dtype": (
                self.last_result.source_dtype if self.last_result is not None else ""
            ),
            "last_container_bits": (
                self.last_result.container_bits if self.last_result is not None else 0
            ),
            "last_effective_bits": (
                self.last_result.effective_bits if self.last_result is not None else 0
            ),
        }
        recording = {
            "csv_active": bool(self.logger.active),
            "csv_rows_written": int(getattr(self.logger, "rows_written", 0)),
            "raw_active": bool(self.raw_recorder.active),
            "raw_queue_depth": int(self.raw_recorder.queue_depth),
            "raw_frames_written_current": int(self.raw_recorder.frames_written),
            "raw_frames_dropped_current": int(self.raw_recorder.frames_dropped),
            "raw_uncompressed_bytes_current": int(self.raw_recorder.bytes_written_uncompressed),
            "raw_last_error": self.raw_recorder.last_error,
        }
        runtime = {"process_rss_bytes": process_rss_bytes()}
        try:
            base = self.logger.dir if self.logger.dir is not None else Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))
            runtime["disk_free_bytes"] = disk_free_bytes(base)
            runtime["disk_path"] = str(base)
        except Exception as exc:
            runtime["disk_error"] = str(exc)
        return build_diagnostic_payload(self.settings, self.last_capture_info, exposure_info=self.exposure_info, acquisition=acquisition, analysis=analysis, recording=recording, runtime=runtime)

    def show_diagnostics(self):
        try:
            dlg = DiagnosticsDialog(diagnostics_to_text(self.diagnostic_payload()), self)
            dlg.exec()
        except Exception as exc:
            log.exception("Diagnostics dialog failed")
            self.on_error(f"Diagnostics failed: {exc}")

    def _profile_ui_dict(self):
        return {
            "ae_target_percent": self.ae_target.value(),
            "roi_enabled": self.roi_enabled.isChecked(),
            "auto_roi": self.auto_roi.isChecked(),
            "roi_width": self.roi_width.value(),
            "roi_height": self.roi_height.value(),
            "snap_radius": self.snap_radius.value(),
            "dark_frames": self.dark_frames.value(),
            "display_mode": str(self.display_mode.currentData()),
            "raw_enabled": self.raw_enabled.isChecked(),
            "raw_every": self.raw_every.value(),
            "raw_segment_mb": self.raw_segment_mb.value(),
            "alarm_enabled": self.alarm_enabled.isChecked(),
            "pointing_limit_um": self.pointing_limit.value(),
            "size_limit_percent": self.size_limit.value(),
            "intensity_limit_percent": self.intensity_limit.value(),
        }

    def export_profile(self):
        try:
            payload = build_profile_payload(self.settings, self.analysis_settings, self._profile_ui_dict())
            filename, _ = QFileDialog.getSaveFileName(self, "Export LaserWatch camera profile", f"{self.settings.name}_LaserWatch_profile.json", "LaserWatch profile (*.json)")
            if not filename:
                return
            path = Path(filename)
            if path.suffix.lower() != ".json":
                path = path.with_suffix(".json")
            write_profile_json(path, payload)
        except Exception as exc:
            log.exception("Profile export failed")
            self.on_error(f"Profile export failed: {exc}")

    def import_profile(self):
        try:
            filename, _ = QFileDialog.getOpenFileName(self, "Import LaserWatch camera profile", "", "LaserWatch profile (*.json)")
            if not filename:
                return
            payload = read_profile_json(Path(filename))
            if not identity_match(self.settings, payload):
                response = QMessageBox.question(self, "Camera profile identity mismatch", "This profile was exported for a different camera identity. Apply only the measurement/calibration settings anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if response != QMessageBox.Yes:
                    return
            cs = payload["camera_settings"]
            ans = payload["analysis_settings"]
            ui = payload["ui_settings"]
            self.settings.exposure_us = float(cs.get("exposure_us", self.settings.exposure_us))
            self.settings.gain = float(cs.get("gain", self.settings.gain))
            self.settings.pixel_size_um_x = float(cs.get("pixel_size_um_x", self.settings.pixel_size_um_x))
            self.settings.pixel_size_um_y = float(cs.get("pixel_size_um_y", self.settings.pixel_size_um_y))
            self.settings.magnification = float(cs.get("magnification", self.settings.magnification))
            roi = ans.get("roi")
            self.analysis_settings.roi = tuple(int(v) for v in roi) if roi is not None else None
            self.analysis_settings.threshold_fraction = float(ans.get("threshold_fraction", self.analysis_settings.threshold_fraction))
            self.analysis_settings.saturation_fraction = float(ans.get("saturation_fraction", self.analysis_settings.saturation_fraction))
            self.analysis_settings.low_signal_fraction = float(ans.get("low_signal_fraction", self.analysis_settings.low_signal_fraction))
            self.analysis_settings.analysis_channel = str(
                ans.get("analysis_channel", self.analysis_settings.analysis_channel)
            ).upper()
            if self.analysis_settings.analysis_channel not in ("AUTO", "GRAY", "R", "G", "B"):
                self.analysis_settings.analysis_channel = "AUTO"
            self.analysis_settings.bit_depth_override = int(
                ans.get("bit_depth_override", self.analysis_settings.bit_depth_override)
            )
            if self.analysis_settings.bit_depth_override not in (0, 8, 10, 12, 16):
                self.analysis_settings.bit_depth_override = 0
            self.analysis_settings.spot_detection_enabled = bool(
                ans.get("spot_detection_enabled", self.analysis_settings.spot_detection_enabled)
            )
            self.analysis_settings.spot_threshold_fraction = float(
                ans.get("spot_threshold_fraction", self.analysis_settings.spot_threshold_fraction)
            )
            self.analysis_settings.spot_min_area_px = int(
                ans.get("spot_min_area_px", self.analysis_settings.spot_min_area_px)
            )
            self.analysis_settings.spot_padding_px = int(
                ans.get("spot_padding_px", self.analysis_settings.spot_padding_px)
            )
            self.analysis_settings.preferred_target_px = None
            self._clear_fixed_target_marker_only()

            self.spot_enabled.setChecked(self.analysis_settings.spot_detection_enabled)
            self.spot_threshold.setValue(100.0 * self.analysis_settings.spot_threshold_fraction)
            self.spot_min_area.setValue(self.analysis_settings.spot_min_area_px)

            cidx = self.channel_combo.findData(self.analysis_settings.analysis_channel)
            self.channel_combo.setCurrentIndex(max(0, cidx))
            bidx = self.bit_depth_combo.findData(self.analysis_settings.bit_depth_override)
            self.bit_depth_combo.setCurrentIndex(max(0, bidx))

            self.exposure_ms.setValue(self.settings.exposure_us / 1000.0)
            self.gain.setValue(self.settings.gain)
            self.pixel_x.setValue(self.settings.pixel_size_um_x)
            self.pixel_y.setValue(self.settings.pixel_size_um_y)
            self.mag.setValue(self.settings.magnification)
            self.ae_target.setValue(float(ui.get("ae_target_percent", 80.0)))
            self.roi_width.setValue(int(ui.get("roi_width", 400)))
            self.roi_height.setValue(int(ui.get("roi_height", 400)))
            self.snap_radius.setValue(int(ui.get("snap_radius", 80)))
            self.dark_frames.setValue(int(ui.get("dark_frames", 32)))
            didx = self.display_mode.findData(str(ui.get("display_mode", "RAW")))
            self.display_mode.setCurrentIndex(max(0, didx))
            self.raw_every.setValue(int(ui.get("raw_every", 10)))
            self.raw_segment_mb.setValue(int(ui.get("raw_segment_mb", 2048)))
            self.pointing_limit.setValue(float(ui.get("pointing_limit_um", 10.0)))
            self.size_limit.setValue(float(ui.get("size_limit_percent", 10.0)))
            self.intensity_limit.setValue(float(ui.get("intensity_limit_percent", 20.0)))
            self.alarm_enabled.setChecked(bool(ui.get("alarm_enabled", False)))
            self.auto_roi.setChecked(bool(ui.get("auto_roi", False)))
            self.roi_enabled.setChecked(bool(ui.get("roi_enabled", False)))
            if self.roi_enabled.isChecked() and self.analysis_settings.roi:
                self.view.set_roi(self.analysis_settings.roi)
                self._update_roi_text()
            elif not self.roi_enabled.isChecked():
                self.analysis_settings.roi = None
                self.view.clear_roi()
            if self.camera_thread is not None and self.camera_thread.isRunning():
                if self.exposure_info is not None:
                    raw, actual_us = self.exposure_info.quantize_us(self.settings.exposure_us)
                    self.settings.exposure_raw = raw
                    self.settings.exposure_us = actual_us
                    self.camera_thread.set_exposure_raw(raw)
                    self._show_actual_exposure(raw, actual_us)
                self.camera_thread.set_gain(self.settings.gain)
            self.save_profile()
        except Exception as exc:
            log.exception("Profile import failed")
            self.on_error(f"Profile import failed: {exc}")

    def _check_raw_rotation_and_disk(self):
        if not self.raw_recorder.active:
            return
        try:
            now = time.monotonic()
            if self.logger.dir is not None and now - self._last_disk_guard_s >= 2.0:
                self._last_disk_guard_s = now
                if disk_free_bytes(self.logger.dir) < self.min_free_disk_bytes:
                    self._stop_raw_recorder()
                    self.raw_enabled.setChecked(False)
                    self.on_error("Raw recording stopped: less than 2 GiB free disk space.")
                    return
            limit = int(self.raw_segment_mb.value()) * 1024**2
            if limit > 0 and self.raw_recorder.bytes_written_uncompressed >= limit:
                self._stop_raw_recorder()
                if self.raw_enabled.isChecked() and self.logger.active:
                    self._start_raw_recorder()
        except Exception as exc:
            log.exception("Raw rotation/disk guard failed")
            self.on_error(f"Raw recording guard failed: {exc}")

    def _write_session_report(self):
        if self.logger.dir is None:
            return
        summary = self._summary_dict()
        arrays = self.session_buffer.arrays(None)
        try:
            psd = self.session_buffer.pointing_psd(None)
        except Exception:
            psd = {}
        write_html_report(self.logger.dir / "report.html", summary, arrays=arrays, psd=psd)

    def export_html_report(self):
        try:
            default_dir = self.logger.dir if self.logger.dir is not None else Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))
            filename, _ = QFileDialog.getSaveFileName(self, "Export LaserWatch HTML report", str(default_dir / "LaserWatch_report.html"), "HTML (*.html)")
            if not filename:
                return
            path = Path(filename)
            if path.suffix.lower() != ".html":
                path = path.with_suffix(".html")
            summary = self._summary_dict()
            buffer = self.session_buffer if self.logger.dir is not None else self.trend_buffer
            arrays = buffer.arrays(None)
            try:
                psd = buffer.pointing_psd(None)
            except Exception:
                psd = {}
            write_html_report(path, summary, arrays=arrays, psd=psd)
        except Exception as exc:
            log.exception("HTML report export failed")
            self.on_error(f"HTML report export failed: {exc}")


    def snapshot(self):
        try:
            if self.last_frame is None:
                self.on_error("Cannot save snapshot: no frame available.")
                return

            import cv2

            filename, _ = QFileDialog.getSaveFileName(
                self,
                "Save snapshot",
                "beam.png",
                "PNG (*.png);;TIFF (*.tif *.tiff)",
            )
            if not filename:
                return

            ok = cv2.imwrite(filename, self.last_frame)
            if not ok:
                raise IOError(f"cv2.imwrite returned False for: {filename}")
        except Exception as exc:
            log.exception("Snapshot save failed")
            self.on_error(f"Snapshot save failed: {exc}")

    def change_gain(self, v):
        self.settings.gain = float(v)
        if self.camera_thread is not None:
            self.camera_thread.set_gain(v)
        self.schedule_profile_save()

    def change_pixel_x(self, v):
        self.settings.pixel_size_um_x = float(v)
        self.schedule_profile_save()

    def change_pixel_y(self, v):
        self.settings.pixel_size_um_y = float(v)
        self.schedule_profile_save()

    def change_mag(self, v):
        self.settings.magnification = float(v)
        self.schedule_profile_save()

    def on_error(self, msg):
        msg = str(msg)
        log.warning("Camera panel error: %s", msg)
        self._last_error_text = msg
        self.error_label.setText(msg)
        self.labels["quality"].setText("ERROR")
        QTimer.singleShot(5000, self._clear_error_if_unchanged)

    def _clear_error_if_unchanged(self):
        if self.error_label.text() == self._last_error_text:
            self.error_label.setText("")

    def shutdown(self):
        try:
            self.save_profile()
        except Exception:
            log.exception("Final profile save failed")

        try:
            self.trend_timer.stop()
            self.profile_timer.stop()
            self.health_timer.stop()
        except Exception:
            pass

        try:
            self._stop_camera()
        except Exception:
            log.exception("Camera shutdown failed")

        try:
            self.analysis_thread.stop()
            if not self.analysis_thread.wait(2000):
                log.warning("Analysis thread did not stop within timeout")
                self.analysis_thread.terminate()
                self.analysis_thread.wait(500)
        except Exception:
            log.exception("Analysis shutdown failed")

        try:
            if self.logger.active:
                self._finish_logging_session()
            else:
                self._stop_raw_recorder()
        except Exception:
            log.exception("Logger/raw shutdown failed")
