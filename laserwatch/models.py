from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Tuple

ROI = Tuple[int, int, int, int]


@dataclass
class CameraDevice:
    dshow_index: int
    friendly_name: str
    device_path: str = ""
    vid: str = ""
    pid: str = ""
    instance_id: str = ""

    @property
    def persistent_id(self) -> str:
        return self.device_path or self.instance_id or f"dshow:{self.friendly_name}"

    @property
    def display_name(self) -> str:
        suffix = f"  [{self.vid}:{self.pid}]" if self.vid and self.pid else ""
        return f"{self.friendly_name}{suffix}"


@dataclass
class CameraSettings:
    camera_index: int = 0
    name: str = "Camera"
    friendly_name: str = ""
    device_path: str = ""
    vid: str = ""
    pid: str = ""
    instance_id: str = ""
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    exposure_raw: int = -6
    exposure_us: float = 15625.0
    auto_exposure: bool = False
    gain: float = 0.0
    pixel_size_um_x: float = 3.45
    pixel_size_um_y: float = 3.45
    magnification: float = 1.0

    @classmethod
    def from_device(cls, device: CameraDevice) -> "CameraSettings":
        return cls(
            camera_index=device.dshow_index,
            name=device.friendly_name or f"Camera_{device.dshow_index}",
            friendly_name=device.friendly_name,
            device_path=device.device_path,
            vid=device.vid,
            pid=device.pid,
            instance_id=device.instance_id,
        )

    @property
    def persistent_id(self) -> str:
        return (
            self.device_path
            or self.instance_id
            or f"{self.vid}:{self.pid}:{self.friendly_name}:{self.camera_index}"
        )

    @property
    def effective_pixel_um_x(self) -> float:
        return self.pixel_size_um_x / max(self.magnification, 1e-12)

    @property
    def effective_pixel_um_y(self) -> float:
        return self.pixel_size_um_y / max(self.magnification, 1e-12)


@dataclass
class AnalysisSettings:
    roi: Optional[ROI] = None
    background_level: float = 0.0
    threshold_fraction: float = 0.01
    saturation_fraction: float = 0.98
    low_signal_fraction: float = 0.02
    gaussian_fit_enabled: bool = False
    gaussian_fit_every_n: int = 10
    analysis_channel: str = "AUTO"
    bit_depth_override: int = 0

    # v0.8.2 spot isolation. Detection uses this threshold to locate the
    # principal connected component, then ordinary low-threshold moment
    # analysis is performed in an expanded window around that component.
    spot_detection_enabled: bool = True
    spot_threshold_fraction: float = 0.15
    spot_min_area_px: int = 1
    spot_padding_px: int = 24

    # Session-only preferred optical target. When set, Spot detection chooses
    # the candidate nearest this full-frame pixel coordinate rather than the
    # globally strongest component. It is deliberately not persisted.
    preferred_target_px: Optional[Tuple[float, float]] = None


@dataclass
class BeamResult:
    timestamp_ns: int
    frame_id: int
    cx_px: float
    cy_px: float
    cx_um: float
    cy_um: float
    d4sigma_x_um: float
    d4sigma_y_um: float
    d4sigma_major_um: float
    d4sigma_minor_um: float
    fwhm_x_um: float
    fwhm_y_um: float
    angle_deg: float
    peak: float
    integrated: float
    saturation_fraction: float
    quality: str

    raw_peak: float = 0.0
    full_scale: float = 255.0

    source_mode: str = "MONO"
    source_channels: int = 1
    source_dtype: str = "uint8"
    container_bits: int = 8
    effective_bits: int = 8
    analysis_channel: str = "MONO"
    analysis_raw_peak: float = 0.0

    raw_peak_r: float = float("nan")
    raw_peak_g: float = float("nan")
    raw_peak_b: float = float("nan")
    saturation_fraction_r: float = float("nan")
    saturation_fraction_g: float = float("nan")
    saturation_fraction_b: float = float("nan")

    # Spot detector metadata.
    detection_state: str = "DISABLED"  # DETECTED / NOT_FOUND / DISABLED
    spot_count: int = 0
    spot_bbox_x: int = -1
    spot_bbox_y: int = -1
    spot_bbox_w: int = 0
    spot_bbox_h: int = 0
    spot_area_px: int = 0

    gaussian_fit_ok: bool = False
    gaussian_cx_px: float = float("nan")
    gaussian_cy_px: float = float("nan")
    gaussian_fwhm_x_um: float = float("nan")
    gaussian_fwhm_y_um: float = float("nan")

    @property
    def raw_peak_fraction(self) -> float:
        return self.raw_peak / self.full_scale if self.full_scale > 0 else 0.0

    @property
    def raw_peak_r_fraction(self) -> float:
        return self.raw_peak_r / self.full_scale if self.full_scale > 0 else float("nan")

    @property
    def raw_peak_g_fraction(self) -> float:
        return self.raw_peak_g / self.full_scale if self.full_scale > 0 else float("nan")

    @property
    def raw_peak_b_fraction(self) -> float:
        return self.raw_peak_b / self.full_scale if self.full_scale > 0 else float("nan")

    def asdict(self):
        return asdict(self)
