from __future__ import annotations

import logging

from PySide6.QtCore import QSettings

from .profile_utils import profile_key

log = logging.getLogger(__name__)



class CameraProfileStore:
    def __init__(self, persistent_id: str):
        self.settings = QSettings("LaserWatch", "LaserWatch")
        self.key = profile_key(persistent_id)
        self.prefix = f"camera_profiles/{self.key}"

    def _name(self, name: str) -> str:
        return f"{self.prefix}/{name}"

    def get(self, name: str, default=None, type_=None):
        try:
            if type_ is None:
                return self.settings.value(self._name(name), default)
            return self.settings.value(self._name(name), default, type=type_)
        except Exception:
            log.exception("Failed to read profile value: %s", name)
            return default

    def set(self, name: str, value) -> None:
        try:
            self.settings.setValue(self._name(name), value)
        except Exception:
            log.exception("Failed to write profile value: %s", name)

    def apply_camera_settings(self, camera) -> None:
        camera.exposure_us = float(self.get("exposure_us", camera.exposure_us, float))
        camera.gain = float(self.get("gain", camera.gain, float))
        camera.pixel_size_um_x = float(
            self.get("pixel_size_um_x", camera.pixel_size_um_x, float)
        )
        camera.pixel_size_um_y = float(
            self.get("pixel_size_um_y", camera.pixel_size_um_y, float)
        )
        camera.magnification = float(
            self.get("magnification", camera.magnification, float)
        )

    def apply_analysis_settings(self, analysis) -> None:
        analysis.threshold_fraction = float(
            self.get("threshold_fraction", analysis.threshold_fraction, float)
        )
        analysis.saturation_fraction = float(
            self.get("saturation_fraction", analysis.saturation_fraction, float)
        )
        analysis.low_signal_fraction = float(
            self.get("low_signal_fraction", analysis.low_signal_fraction, float)
        )
        analysis.analysis_channel = str(
            self.get("analysis_channel", analysis.analysis_channel, str)
        ).upper()
        if analysis.analysis_channel not in ("AUTO", "GRAY", "R", "G", "B"):
            analysis.analysis_channel = "AUTO"

        analysis.bit_depth_override = int(
            self.get("bit_depth_override", analysis.bit_depth_override, int)
        )
        if analysis.bit_depth_override not in (0, 8, 10, 12, 16):
            analysis.bit_depth_override = 0

        analysis.spot_detection_enabled = bool(
            self.get("spot_detection_enabled", analysis.spot_detection_enabled, bool)
        )
        analysis.spot_threshold_fraction = float(
            self.get("spot_threshold_fraction", analysis.spot_threshold_fraction, float)
        )
        analysis.spot_min_area_px = int(
            self.get("spot_min_area_px", analysis.spot_min_area_px, int)
        )
        analysis.spot_padding_px = int(
            self.get("spot_padding_px", analysis.spot_padding_px, int)
        )

        roi_text = self.get("roi", "", str)
        if roi_text:
            try:
                values = [int(v) for v in roi_text.split(",")]
                if len(values) == 4 and values[2] > 0 and values[3] > 0:
                    analysis.roi = tuple(values)
            except Exception:
                log.warning("Ignoring invalid saved ROI: %s", roi_text)

    def save_core(self, camera, analysis) -> None:
        self.set("exposure_us", float(camera.exposure_us))
        self.set("gain", float(camera.gain))
        self.set("pixel_size_um_x", float(camera.pixel_size_um_x))
        self.set("pixel_size_um_y", float(camera.pixel_size_um_y))
        self.set("magnification", float(camera.magnification))
        self.set("threshold_fraction", float(analysis.threshold_fraction))
        self.set("saturation_fraction", float(analysis.saturation_fraction))
        self.set("low_signal_fraction", float(analysis.low_signal_fraction))
        self.set("analysis_channel", str(analysis.analysis_channel))
        self.set("bit_depth_override", int(analysis.bit_depth_override))
        self.set("spot_detection_enabled", bool(analysis.spot_detection_enabled))
        self.set("spot_threshold_fraction", float(analysis.spot_threshold_fraction))
        self.set("spot_min_area_px", int(analysis.spot_min_area_px))
        self.set("spot_padding_px", int(analysis.spot_padding_px))

        if analysis.roi is None:
            self.set("roi", "")
        else:
            self.set("roi", ",".join(str(int(v)) for v in analysis.roi))
