from __future__ import annotations

import logging
import math
import re
import sys
from dataclasses import dataclass
from typing import Optional

from .models import CameraDevice

log = logging.getLogger(__name__)

CAMERA_CONTROL_EXPOSURE = 4
CAMERA_CONTROL_FLAGS_AUTO = 0x0001
CAMERA_CONTROL_FLAGS_MANUAL = 0x0002

_USB_RE = re.compile(r"vid_([0-9a-f]{4}).*?pid_([0-9a-f]{4})", re.IGNORECASE)


class UVCUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ExposureRange:
    min_raw: int
    max_raw: int
    step_raw: int
    default_raw: int
    current_raw: int
    current_flags: int
    capability_flags: int

    @staticmethod
    def raw_to_seconds(raw: int) -> float:
        return 2.0 ** int(raw)

    @staticmethod
    def raw_to_us(raw: int) -> float:
        return ExposureRange.raw_to_seconds(raw) * 1_000_000.0

    @staticmethod
    def us_to_log2_raw_float(exposure_us: float) -> float:
        if not math.isfinite(exposure_us) or exposure_us <= 0:
            raise ValueError("Exposure must be a positive finite value")
        return math.log2(exposure_us / 1_000_000.0)

    def valid_raw_values(self) -> list[int]:
        step = max(abs(int(self.step_raw)), 1)
        lo, hi = sorted((int(self.min_raw), int(self.max_raw)))
        return list(range(lo, hi + 1, step))

    def quantize_us(self, exposure_us: float) -> tuple[int, float]:
        target = self.us_to_log2_raw_float(exposure_us)
        values = self.valid_raw_values()
        if not values:
            raise ValueError("Camera reported an empty exposure range")
        raw = min(values, key=lambda v: abs(v - target))
        return raw, self.raw_to_us(raw)

    @property
    def min_us(self) -> float:
        return min(self.raw_to_us(self.min_raw), self.raw_to_us(self.max_raw))

    @property
    def max_us(self) -> float:
        return max(self.raw_to_us(self.min_raw), self.raw_to_us(self.max_raw))

    @property
    def current_us(self) -> float:
        return self.raw_to_us(self.current_raw)

    @property
    def supports_auto(self) -> bool:
        return bool(self.capability_flags & CAMERA_CONTROL_FLAGS_AUTO)

    @property
    def supports_manual(self) -> bool:
        return bool(self.capability_flags & CAMERA_CONTROL_FLAGS_MANUAL)


def parse_usb_identity(device_path: str) -> tuple[str, str, str]:
    path = device_path or ""
    match = _USB_RE.search(path)
    vid = match.group(1).upper() if match else ""
    pid = match.group(2).upper() if match else ""

    # DirectShow paths commonly look like:
    # \\?\usb#vid_xxxx&pid_yyyy&mi_00#INSTANCE#{...}
    parts = path.replace("/", "\\").split("#")
    instance_id = parts[2] if len(parts) >= 3 else ""
    return vid, pid, instance_id


def _require_windows():
    if sys.platform != "win32":
        raise UVCUnavailable("Windows DirectShow UVC control is available only on Windows")


def enumerate_video_devices() -> list[CameraDevice]:
    """
    Enumerate DirectShow video-input devices in the same ordering used by CAP_DSHOW.

    FriendlyName and DevicePath are read from each DirectShow moniker property bag.
    DevicePath is used as the persistent identity when the driver exposes it.
    """
    _require_windows()

    try:
        from comtypes import GUID
        from comtypes.persist import IPropertyBag
        from pygrabber.dshow_graph import FilterGraph
        from pygrabber.dshow_ids import DeviceCategories
    except Exception as exc:
        raise UVCUnavailable(f"DirectShow enumeration dependencies unavailable: {exc}") from exc

    graph = None
    devices: list[CameraDevice] = []
    try:
        graph = FilterGraph()
        sys_enum = graph.system_device_enum.system_device_enum
        enum = sys_enum.CreateClassEnumerator(GUID(DeviceCategories.VideoInputDevice), dwFlags=0)

        try:
            moniker, count = enum.Next(1)
        except ValueError:
            return []

        index = 0
        while count > 0:
            friendly = f"Camera {index}"
            device_path = ""
            try:
                bag = moniker.BindToStorage(0, 0, IPropertyBag._iid_).QueryInterface(IPropertyBag)
                try:
                    friendly = str(bag.Read("FriendlyName", pErrorLog=None))
                except Exception:
                    log.exception("Could not read FriendlyName for DirectShow camera %s", index)
                try:
                    device_path = str(bag.Read("DevicePath", pErrorLog=None))
                except Exception:
                    # DevicePath is driver-dependent. Missing DevicePath is not fatal.
                    device_path = ""
            except Exception:
                log.exception("Could not read DirectShow property bag for camera %s", index)

            vid, pid, instance_id = parse_usb_identity(device_path)
            devices.append(
                CameraDevice(
                    dshow_index=index,
                    friendly_name=friendly,
                    device_path=device_path,
                    vid=vid,
                    pid=pid,
                    instance_id=instance_id,
                )
            )
            index += 1
            moniker, count = enum.Next(1)

        return devices
    except Exception as exc:
        log.exception("DirectShow camera enumeration failed")
        raise UVCUnavailable(f"DirectShow camera enumeration failed: {exc}") from exc
    finally:
        graph = None


def _make_camera_control_interface():
    from ctypes import POINTER, c_long
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IAMCameraControl(IUnknown):
        _iid_ = GUID("{C6E13370-30AC-11D0-A18C-00A0C9118956}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "GetRange",
                (["in"], c_long, "Property"),
                (["out"], POINTER(c_long), "pMin"),
                (["out"], POINTER(c_long), "pMax"),
                (["out"], POINTER(c_long), "pSteppingDelta"),
                (["out"], POINTER(c_long), "pDefault"),
                (["out"], POINTER(c_long), "pCapsFlags"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "Set",
                (["in"], c_long, "Property"),
                (["in"], c_long, "lValue"),
                (["in"], c_long, "Flags"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "Get",
                (["in"], c_long, "Property"),
                (["out"], POINTER(c_long), "lValue"),
                (["out"], POINTER(c_long), "Flags"),
            ),
        ]

    return IAMCameraControl


class DirectShowCameraControl:
    """
    Short-lived DirectShow IAMCameraControl session.

    Do not keep this object alive while the OpenCV capture device is active on
    cameras/drivers that allow only one client. LaserWatch uses it mainly before
    starting acquisition, then applies the resulting raw exposure value through
    OpenCV's DirectShow backend.
    """

    def __init__(self, dshow_index: int):
        _require_windows()
        self.dshow_index = int(dshow_index)
        self._graph = None
        self._filter = None
        self._control = None
        self._open()

    def _open(self):
        try:
            from pygrabber.dshow_graph import FilterGraph
            IAMCameraControl = _make_camera_control_interface()

            self._graph = FilterGraph()
            self._graph.add_video_input_device(self.dshow_index)
            self._filter = self._graph.get_input_device().instance
            self._control = self._filter.QueryInterface(IAMCameraControl)
        except Exception as exc:
            self.close()
            raise UVCUnavailable(
                f"Camera {self.dshow_index} does not expose IAMCameraControl: {exc}"
            ) from exc

    def close(self):
        self._control = None
        self._filter = None
        self._graph = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def get_exposure_range(self) -> ExposureRange:
        if self._control is None:
            raise UVCUnavailable("Camera control is closed")
        try:
            min_v, max_v, step_v, default_v, caps = self._control.GetRange(
                CAMERA_CONTROL_EXPOSURE
            )
            current_v, current_flags = self._control.Get(CAMERA_CONTROL_EXPOSURE)
            return ExposureRange(
                int(min_v),
                int(max_v),
                max(abs(int(step_v)), 1),
                int(default_v),
                int(current_v),
                int(current_flags),
                int(caps),
            )
        except Exception as exc:
            raise UVCUnavailable(f"Exposure control is not supported: {exc}") from exc

    def set_auto_exposure(self, enabled: bool) -> None:
        if self._control is None:
            raise UVCUnavailable("Camera control is closed")
        try:
            current, _ = self._control.Get(CAMERA_CONTROL_EXPOSURE)
            flags = CAMERA_CONTROL_FLAGS_AUTO if enabled else CAMERA_CONTROL_FLAGS_MANUAL
            self._control.Set(CAMERA_CONTROL_EXPOSURE, int(current), flags)
        except Exception as exc:
            raise UVCUnavailable(f"Could not change auto exposure: {exc}") from exc

    def set_exposure_us(self, exposure_us: float) -> tuple[int, float]:
        info = self.get_exposure_range()
        if not info.supports_manual:
            raise UVCUnavailable("Camera does not report manual exposure support")
        raw, actual_us = info.quantize_us(exposure_us)
        try:
            self._control.Set(
                CAMERA_CONTROL_EXPOSURE,
                int(raw),
                CAMERA_CONTROL_FLAGS_MANUAL,
            )
            confirmed, _ = self._control.Get(CAMERA_CONTROL_EXPOSURE)
            confirmed = int(confirmed)
            return confirmed, ExposureRange.raw_to_us(confirmed)
        except Exception as exc:
            raise UVCUnavailable(f"Could not set exposure: {exc}") from exc


def query_exposure_range(dshow_index: int) -> ExposureRange:
    with DirectShowCameraControl(dshow_index) as ctrl:
        return ctrl.get_exposure_range()


def prepare_manual_exposure(dshow_index: int, exposure_us: float) -> tuple[int, float, ExposureRange]:
    """
    Disable auto exposure and set the nearest supported manual exposure before capture.
    """
    with DirectShowCameraControl(dshow_index) as ctrl:
        info = ctrl.get_exposure_range()
        ctrl.set_auto_exposure(False)
        raw, actual_us = ctrl.set_exposure_us(exposure_us)
        refreshed = ctrl.get_exposure_range()
        return raw, actual_us, refreshed
