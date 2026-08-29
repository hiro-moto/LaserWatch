from __future__ import annotations

import logging
import math
import threading
from typing import Optional

import cv2
import numpy as np

from .models import AnalysisSettings, BeamResult, CameraSettings

log = logging.getLogger(__name__)

VALID_ANALYSIS_CHANNELS = ("AUTO", "GRAY", "R", "G", "B")
VALID_BIT_DEPTHS = (0, 8, 10, 12, 16)


def validate_frame(frame: np.ndarray) -> np.ndarray:
    if frame is None:
        raise ValueError("Frame is None")
    if not isinstance(frame, np.ndarray):
        raise TypeError(f"Expected numpy.ndarray, got {type(frame)!r}")
    if frame.size == 0:
        raise ValueError("Empty frame")
    if frame.ndim == 2:
        return frame
    if frame.ndim == 3 and frame.shape[2] in (1, 3, 4):
        return frame
    raise ValueError(f"Unsupported frame shape: {frame.shape}")


def is_color_frame(frame: np.ndarray) -> bool:
    frame = validate_frame(frame)
    return bool(frame.ndim == 3 and frame.shape[2] >= 3)


def as_gray(frame: np.ndarray) -> np.ndarray:
    frame = validate_frame(frame)
    if frame.ndim == 2:
        return frame
    if frame.shape[2] == 1:
        return frame[..., 0]
    return cv2.cvtColor(frame[..., :3], cv2.COLOR_BGR2GRAY)


def container_bit_depth(dtype) -> int:
    dt = np.dtype(dtype)
    if np.issubdtype(dt, np.integer):
        return int(np.iinfo(dt).bits)
    if np.issubdtype(dt, np.floating):
        return 0
    raise TypeError(f"Unsupported image dtype: {dt}")


def resolve_effective_bits(dtype, override: int) -> int:
    override = int(override or 0)
    if override not in VALID_BIT_DEPTHS:
        raise ValueError(f"Unsupported bit-depth override: {override}")
    container = container_bit_depth(dtype)
    if override == 0:
        return container
    if container > 0:
        return min(override, container)
    return override


def full_scale_for_frame(frame: np.ndarray, bit_depth_override: int = 0):
    frame = validate_frame(frame)
    dt = frame.dtype
    container = container_bit_depth(dt)
    effective = resolve_effective_bits(dt, bit_depth_override)

    if np.issubdtype(dt, np.integer):
        if effective <= 0:
            effective = container
        return float((1 << effective) - 1), container, effective

    if np.issubdtype(dt, np.floating):
        if effective > 0:
            return float((1 << effective) - 1), container, effective
        max_val = float(np.nanmax(frame))
        full = 1.0 if max_val <= 1.0 else max(255.0, max_val)
        return full, container, 0

    raise TypeError(f"Unsupported image dtype: {dt}")


def select_analysis_plane(frame: np.ndarray, mode: str):
    frame = validate_frame(frame)
    requested = str(mode or "AUTO").upper()
    if requested not in VALID_ANALYSIS_CHANNELS:
        raise ValueError(f"Unsupported analysis channel: {requested}")

    if not is_color_frame(frame):
        return (frame[..., 0] if frame.ndim == 3 else frame), "MONO"

    bgr = frame[..., :3]
    if requested in ("AUTO", "GRAY"):
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), "GRAY"
    if requested == "R":
        return bgr[..., 2], "R"
    if requested == "G":
        return bgr[..., 1], "G"
    if requested == "B":
        return bgr[..., 0], "B"
    raise ValueError(f"Unsupported analysis channel: {requested}")


def color_raw_metrics(frame_roi, full_scale, saturation_fraction):
    threshold = float(saturation_fraction) * float(full_scale)

    if not is_color_frame(frame_roi):
        mono = frame_roi[..., 0] if frame_roi.ndim == 3 else frame_roi
        peak = float(np.nanmax(mono)) if mono.size else 0.0
        sat = mono >= threshold
        return {
            "source_mode": "MONO",
            "source_channels": 1,
            "raw_peak": peak,
            "sat_any": float(np.count_nonzero(sat) / max(mono.size, 1)),
            "raw_peak_r": float("nan"),
            "raw_peak_g": float("nan"),
            "raw_peak_b": float("nan"),
            "sat_r": float("nan"),
            "sat_g": float("nan"),
            "sat_b": float("nan"),
        }

    b, g, r = frame_roi[..., 0], frame_roi[..., 1], frame_roi[..., 2]
    peak_b = float(np.nanmax(b)) if b.size else 0.0
    peak_g = float(np.nanmax(g)) if g.size else 0.0
    peak_r = float(np.nanmax(r)) if r.size else 0.0
    sb, sg, sr = b >= threshold, g >= threshold, r >= threshold
    sany = sb | sg | sr
    npix = max(b.shape[0] * b.shape[1], 1)

    return {
        "source_mode": "COLOR",
        "source_channels": int(frame_roi.shape[2]),
        "raw_peak": max(peak_b, peak_g, peak_r),
        "sat_any": float(np.count_nonzero(sany) / npix),
        "raw_peak_r": peak_r,
        "raw_peak_g": peak_g,
        "raw_peak_b": peak_b,
        "sat_r": float(np.count_nonzero(sr) / npix),
        "sat_g": float(np.count_nonzero(sg) / npix),
        "sat_b": float(np.count_nonzero(sb) / npix),
    }


def detect_principal_spot(img: np.ndarray, settings: AnalysisSettings, full_scale: float, preferred_xy=None):
    """
    Detect bright connected components at a relatively high threshold.
    Return the strongest component and an expanded measurement window.

    Without a preferred target, component ranking prioritizes peak then
    integrated intensity. With preferred_xy=(x,y), the candidate nearest the
    requested target is selected, with optical strength as the tiebreaker.
    """
    if img.size == 0:
        return None

    peak = float(np.max(img))
    if (
        not math.isfinite(peak)
        or peak <= max(0.0, float(settings.low_signal_fraction)) * full_scale
    ):
        return None

    detect_fraction = min(
        max(float(settings.spot_threshold_fraction), 0.001),
        0.95,
    )
    threshold = peak * detect_fraction
    binary = (img >= threshold).astype(np.uint8)

    nlabels, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary,
        connectivity=8,
    )

    candidates = []
    min_area = max(1, int(settings.spot_min_area_px))
    for label in range(1, nlabels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue

        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        mask = labels[y:y+h, x:x+w] == label
        patch = img[y:y+h, x:x+w]
        values = patch[mask]
        if values.size == 0:
            continue

        component_peak = float(np.max(values))
        component_sum = float(np.sum(values, dtype=np.float64))

        # Intensity-weighted component centroid, in img-local pixels.
        py, px = np.nonzero(mask)
        weights_local = values.astype(np.float64, copy=False)
        wsum = float(np.sum(weights_local))
        if wsum > 0:
            centroid_x = x + float(np.dot(px.astype(np.float64), weights_local) / wsum)
            centroid_y = y + float(np.dot(py.astype(np.float64), weights_local) / wsum)
        else:
            centroid_x = x + 0.5 * (w - 1)
            centroid_y = y + 0.5 * (h - 1)

        candidates.append(
            (
                component_peak,
                component_sum,
                area,
                x,
                y,
                w,
                h,
                centroid_x,
                centroid_y,
            )
        )

    if not candidates:
        return None

    if preferred_xy is None:
        # Strongest optical peak wins; integrated signal is the tiebreaker.
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    else:
        px0, py0 = map(float, preferred_xy)

        def target_key(item):
            cx = item[7]
            cy = item[8]
            distance2 = (cx - px0) ** 2 + (cy - py0) ** 2
            # Nearest optical component first; stronger candidate breaks ties.
            return (distance2, -item[0], -item[1])

        candidates.sort(key=target_key)

    best = candidates[0]
    _, _, area, x, y, w, h, centroid_x, centroid_y = best

    pad = max(
        int(settings.spot_padding_px),
        int(round(1.5 * max(w, h))),
    )
    mx0 = max(0, x - pad)
    my0 = max(0, y - pad)
    mx1 = min(img.shape[1], x + w + pad)
    my1 = min(img.shape[0], y + h + pad)

    return {
        "spot_count": len(candidates),
        "bbox": (x, y, w, h),
        "area": area,
        "centroid": (centroid_x, centroid_y),
        "measurement_window": (mx0, my0, mx1 - mx0, my1 - my0),
    }


class BeamAnalyzer:
    def __init__(
        self,
        camera_settings: CameraSettings,
        settings: Optional[AnalysisSettings] = None,
    ):
        self.camera_settings = camera_settings
        self.settings = settings or AnalysisSettings()
        self.dark_frame: Optional[np.ndarray] = None
        self._dark_lock = threading.RLock()
        self._profile_lock = threading.RLock()
        self._last_profile = None

    def set_dark(self, frame: Optional[np.ndarray]) -> None:
        with self._dark_lock:
            if frame is None:
                self.dark_frame = None
                return
            src = validate_frame(frame)
            self.dark_frame = src.astype(np.float32, copy=True)

    def clear_dark(self) -> None:
        self.set_dark(None)

    def get_dark_copy(self) -> Optional[np.ndarray]:
        with self._dark_lock:
            return None if self.dark_frame is None else self.dark_frame.copy()

    def dark_corrected_source(self, frame: np.ndarray) -> np.ndarray:
        """Return source-layout dark-subtracted image for display."""
        source = validate_frame(frame)
        with self._dark_lock:
            dark = None if self.dark_frame is None else self.dark_frame

        out = source.astype(np.float32, copy=True)
        if dark is not None and dark.shape == source.shape:
            out -= dark
            np.maximum(out, 0.0, out=out)
        return out

    def analyze(self, frame, timestamp_ns, frame_id):
        with self._profile_lock:
            self._last_profile = None
        source = validate_frame(frame)
        full_scale, container_bits, effective_bits = full_scale_for_frame(
            source,
            self.settings.bit_depth_override,
        )
        analysis_plane, actual_channel = select_analysis_plane(
            source,
            self.settings.analysis_channel,
        )

        with self._dark_lock:
            dark_snapshot = None if self.dark_frame is None else self.dark_frame

        dark_plane = None
        if dark_snapshot is not None:
            try:
                if dark_snapshot.shape == source.shape:
                    dark_plane, _ = select_analysis_plane(
                        dark_snapshot,
                        self.settings.analysis_channel,
                    )
                else:
                    log.warning(
                        "Dark frame shape mismatch: dark=%s frame=%s",
                        dark_snapshot.shape,
                        source.shape,
                    )
            except Exception:
                log.exception("Could not select dark analysis channel")

        ox = oy = 0
        roi = self.settings.roi
        if roi is not None:
            if len(roi) != 4:
                raise ValueError("ROI must be (x, y, w, h)")
            x, y, w, h = map(int, roi)
            if w <= 0 or h <= 0:
                raise ValueError(f"Invalid ROI size: {roi}")

            x = max(0, min(x, analysis_plane.shape[1] - 1))
            y = max(0, min(y, analysis_plane.shape[0] - 1))
            w = max(1, min(w, analysis_plane.shape[1] - x))
            h = max(1, min(h, analysis_plane.shape[0] - y))

            plane_roi = analysis_plane[y:y+h, x:x+w]
            raw_roi = source[y:y+h, x:x+w]
            dark_roi = None if dark_plane is None else dark_plane[y:y+h, x:x+w]
            ox, oy = x, y
        else:
            plane_roi = analysis_plane
            raw_roi = source
            dark_roi = dark_plane

        raw_metrics = color_raw_metrics(
            raw_roi,
            full_scale,
            self.settings.saturation_fraction,
        )
        analysis_raw_peak = (
            float(np.nanmax(plane_roi)) if plane_roi.size else 0.0
        )

        img = plane_roi.astype(np.float32, copy=False)
        if dark_roi is not None and dark_roi.shape == img.shape:
            img = img - dark_roi
        elif dark_roi is None and self.settings.background_level:
            img = img - float(self.settings.background_level)

        img = np.nan_to_num(
            img,
            nan=0.0,
            posinf=full_scale,
            neginf=0.0,
        )
        img = np.maximum(img, 0.0)

        common = dict(
            timestamp_ns=int(timestamp_ns),
            frame_id=int(frame_id),
            saturation_fraction=float(raw_metrics["sat_any"]),
            raw_peak=float(raw_metrics["raw_peak"]),
            full_scale=float(full_scale),
            source_mode=str(raw_metrics["source_mode"]),
            source_channels=int(raw_metrics["source_channels"]),
            source_dtype=str(source.dtype),
            container_bits=int(container_bits),
            effective_bits=int(effective_bits),
            analysis_channel=actual_channel,
            analysis_raw_peak=analysis_raw_peak,
            raw_peak_r=float(raw_metrics["raw_peak_r"]),
            raw_peak_g=float(raw_metrics["raw_peak_g"]),
            raw_peak_b=float(raw_metrics["raw_peak_b"]),
            saturation_fraction_r=float(raw_metrics["sat_r"]),
            saturation_fraction_g=float(raw_metrics["sat_g"]),
            saturation_fraction_b=float(raw_metrics["sat_b"]),
        )

        measurement = img
        mx = my = 0
        detection_state = "DISABLED"
        spot_count = 0
        spot_bbox = (-1, -1, 0, 0)
        spot_area = 0

        if self.settings.spot_detection_enabled:
            preferred_local = None
            if self.settings.preferred_target_px is not None:
                try:
                    ptx, pty = self.settings.preferred_target_px
                    preferred_local = (
                        float(ptx) - float(ox),
                        float(pty) - float(oy),
                    )
                except Exception:
                    preferred_local = None

            detected = detect_principal_spot(
                img,
                self.settings,
                full_scale,
                preferred_xy=preferred_local,
            )
            if detected is None:
                nan = float("nan")
                return BeamResult(
                    cx_px=nan,
                    cy_px=nan,
                    cx_um=nan,
                    cy_um=nan,
                    d4sigma_x_um=nan,
                    d4sigma_y_um=nan,
                    d4sigma_major_um=nan,
                    d4sigma_minor_um=nan,
                    fwhm_x_um=nan,
                    fwhm_y_um=nan,
                    angle_deg=nan,
                    peak=float(img.max(initial=0.0)),
                    integrated=0.0,
                    quality="BEAM_NOT_FOUND",
                    detection_state="NOT_FOUND",
                    **common,
                )

            detection_state = "DETECTED"
            spot_count = int(detected["spot_count"])
            bx, by, bw, bh = detected["bbox"]
            spot_bbox = (bx + ox, by + oy, bw, bh)
            spot_area = int(detected["area"])

            mx, my, mw, mh = detected["measurement_window"]
            measurement = img[my:my+mh, mx:mx+mw]

        peak = float(measurement.max(initial=0.0))
        if peak > 0 and self.settings.threshold_fraction > 0:
            thr = peak * float(self.settings.threshold_fraction)
            weights = np.where(measurement >= thr, measurement, 0.0)
        else:
            weights = measurement

        col = weights.sum(axis=0, dtype=np.float64)
        row = weights.sum(axis=1, dtype=np.float64)
        total = float(col.sum())

        if (
            not np.isfinite(total)
            or total <= 0
            or peak <= self.settings.low_signal_fraction * full_scale
        ):
            nan = float("nan")
            return BeamResult(
                cx_px=nan,
                cy_px=nan,
                cx_um=nan,
                cy_um=nan,
                d4sigma_x_um=nan,
                d4sigma_y_um=nan,
                d4sigma_major_um=nan,
                d4sigma_minor_um=nan,
                fwhm_x_um=nan,
                fwhm_y_um=nan,
                angle_deg=nan,
                peak=peak,
                integrated=max(total, 0.0),
                quality=(
                    "BEAM_NOT_FOUND"
                    if self.settings.spot_detection_enabled
                    else "LOW_SIGNAL"
                ),
                detection_state=(
                    "NOT_FOUND"
                    if self.settings.spot_detection_enabled
                    else "DISABLED"
                ),
                spot_count=spot_count,
                spot_bbox_x=spot_bbox[0],
                spot_bbox_y=spot_bbox[1],
                spot_bbox_w=spot_bbox[2],
                spot_bbox_h=spot_bbox[3],
                spot_area_px=spot_area,
                **common,
            )

        xs = np.arange(weights.shape[1], dtype=np.float64)
        ys = np.arange(weights.shape[0], dtype=np.float64)
        cx_local = float(np.dot(col, xs) / total)
        cy_local = float(np.dot(row, ys) / total)

        dx = xs - cx_local
        dy = ys - cy_local
        var_x = max(float(np.dot(col, dx * dx) / total), 0.0)
        var_y = max(float(np.dot(row, dy * dy) / total), 0.0)

        exy = float(np.dot(ys, weights @ xs) / total)
        cov_xy = exy - cx_local * cy_local
        cov = np.array(
            [[var_x, cov_xy], [cov_xy, var_y]],
            dtype=np.float64,
        )
        evals, evecs = np.linalg.eigh(cov)
        evals = np.maximum(evals, 0.0)
        minor_var, major_var = float(evals[0]), float(evals[1])
        major_vec = evecs[:, 1]
        angle_deg = math.degrees(
            math.atan2(major_vec[1], major_vec[0])
        )

        px_x = float(self.camera_settings.effective_pixel_um_x)
        px_y = float(self.camera_settings.effective_pixel_um_y)
        if px_x <= 0 or px_y <= 0:
            raise ValueError("Pixel calibration must be positive")

        d4x = 4.0 * math.sqrt(var_x) * px_x
        d4y = 4.0 * math.sqrt(var_y) * px_y
        f = 2.0 * math.sqrt(2.0 * math.log(2.0))
        fwhm_x = f * math.sqrt(var_x) * px_x
        fwhm_y = f * math.sqrt(var_y) * px_y
        pmean = math.sqrt(px_x * px_y)
        d4major = 4.0 * math.sqrt(major_var) * pmean
        d4minor = 4.0 * math.sqrt(minor_var) * pmean

        cx = cx_local + mx + ox
        cy = cy_local + my + oy
        quality = (
            "SATURATED"
            if float(raw_metrics["sat_any"]) > 0
            else "OK"
        )

        result = BeamResult(
            cx_px=cx,
            cy_px=cy,
            cx_um=cx * px_x,
            cy_um=cy * px_y,
            d4sigma_x_um=d4x,
            d4sigma_y_um=d4y,
            d4sigma_major_um=d4major,
            d4sigma_minor_um=d4minor,
            fwhm_x_um=fwhm_x,
            fwhm_y_um=fwhm_y,
            angle_deg=angle_deg,
            peak=peak,
            integrated=total,
            quality=quality,
            detection_state=detection_state,
            spot_count=spot_count,
            spot_bbox_x=spot_bbox[0],
            spot_bbox_y=spot_bbox[1],
            spot_bbox_w=spot_bbox[2],
            spot_bbox_h=spot_bbox[3],
            spot_area_px=spot_area,
            **common,
        )

        self._cache_cross_sections(
            img,
            ox,
            oy,
            result,
            strip_half_width=2,
        )

        if (
            self.settings.gaussian_fit_enabled
            and frame_id % max(1, self.settings.gaussian_fit_every_n) == 0
        ):
            try:
                self._gaussian_fit(weights, ox + mx, oy + my, result)
            except Exception:
                log.exception("Gaussian fit failed on frame %s", frame_id)

        return result


    def _cache_cross_sections(self, img, ox, oy, result, strip_half_width=2):
        """
        Cache horizontal/vertical intensity profiles through the measured centroid.

        Profiles are taken from the current analysis image (selected Gray/R/G/B channel,
        after dark/background subtraction) across the active analysis ROI/full frame.
        A small strip is averaged rather than using exactly one row/column to reduce noise.
        """
        try:
            if (
                img is None
                or img.size == 0
                or not np.isfinite(result.cx_px)
                or not np.isfinite(result.cy_px)
            ):
                return

            cx_local = float(result.cx_px) - float(ox)
            cy_local = float(result.cy_px) - float(oy)
            h, w = img.shape[:2]

            cx_i = int(round(cx_local))
            cy_i = int(round(cy_local))
            cx_i = max(0, min(w - 1, cx_i))
            cy_i = max(0, min(h - 1, cy_i))

            half = max(0, int(strip_half_width))
            y0, y1 = max(0, cy_i - half), min(h, cy_i + half + 1)
            x0, x1 = max(0, cx_i - half), min(w, cx_i + half + 1)

            x_profile = np.mean(img[y0:y1, :], axis=0, dtype=np.float64)
            y_profile = np.mean(img[:, x0:x1], axis=1, dtype=np.float64)

            px_x = float(self.camera_settings.effective_pixel_um_x)
            px_y = float(self.camera_settings.effective_pixel_um_y)
            x_um = (np.arange(w, dtype=np.float64) - cx_local) * px_x
            y_um = (np.arange(h, dtype=np.float64) - cy_local) * px_y

            def gaussian_equivalent(axis_um, measured, fwhm_um):
                measured = np.asarray(measured, dtype=np.float64)
                n = len(measured)
                edge_n = max(1, min(n // 10, 25))
                baseline = float(
                    np.median(
                        np.concatenate((measured[:edge_n], measured[-edge_n:]))
                    )
                )
                amplitude = max(float(np.max(measured)) - baseline, 0.0)
                if (
                    not math.isfinite(float(fwhm_um))
                    or float(fwhm_um) <= 0
                    or amplitude <= 0
                ):
                    return np.full_like(axis_um, np.nan, dtype=np.float64)

                # Gaussian specified by FWHM:
                # I(x) = b + A exp[-4 ln(2) (x/FWHM)^2].
                return baseline + amplitude * np.exp(
                    -4.0 * math.log(2.0)
                    * (np.asarray(axis_um, dtype=np.float64) / float(fwhm_um)) ** 2
                )

            data = {
                "frame_id": int(result.frame_id),
                "analysis_channel": str(result.analysis_channel),
                "strip_width_px": int(y1 - y0),
                "x_um": x_um,
                "x_intensity": x_profile,
                "x_gaussian": gaussian_equivalent(
                    x_um, x_profile, result.fwhm_x_um
                ),
                "y_um": y_um,
                "y_intensity": y_profile,
                "y_gaussian": gaussian_equivalent(
                    y_um, y_profile, result.fwhm_y_um
                ),
                "fwhm_x_um": float(result.fwhm_x_um),
                "fwhm_y_um": float(result.fwhm_y_um),
            }

            with self._profile_lock:
                self._last_profile = data
        except Exception:
            log.exception("Cross-section profile generation failed")

    def get_last_profile(self, frame_id=None):
        with self._profile_lock:
            data = self._last_profile
            if data is None:
                return None
            if frame_id is not None and int(data["frame_id"]) != int(frame_id):
                return None
            # Arrays are immutable from the GUI side by convention; shallow copy is
            # enough and avoids copying several vectors every frame.
            return dict(data)

    def _gaussian_fit(self, weights, ox, oy, result):
        try:
            from scipy.optimize import least_squares
        except Exception:
            log.warning("SciPy unavailable; Gaussian fitting skipped")
            return

        if not np.isfinite(result.cx_px) or not np.isfinite(result.cy_px):
            return

        cx = result.cx_px - ox
        cy = result.cy_px - oy
        h, w = weights.shape
        half = int(
            max(
                16,
                min(
                    max(
                        result.d4sigma_x_um
                        / max(self.camera_settings.effective_pixel_um_x, 1e-9),
                        result.d4sigma_y_um
                        / max(self.camera_settings.effective_pixel_um_y, 1e-9),
                    ),
                    300,
                ),
            )
        )

        x0, x1 = max(0, int(cx) - half), min(w, int(cx) + half + 1)
        y0, y1 = max(0, int(cy) - half), min(h, int(cy) + half + 1)
        z = weights[y0:y1, x0:x1].astype(np.float64, copy=False)
        if z.size < 25 or z.max(initial=0.0) <= 0:
            return

        step = max(1, int(math.sqrt(z.size / 20000)))
        z = z[::step, ::step]
        yy, xx = np.indices(z.shape, dtype=np.float64)
        xx = xx * step + x0
        yy = yy * step + y0

        amp0 = float(z.max())
        bg0 = float(np.percentile(z, 5))
        sx0 = max(
            2.0,
            result.d4sigma_x_um
            / max(self.camera_settings.effective_pixel_um_x, 1e-9)
            / 4.0,
        )
        sy0 = max(
            2.0,
            result.d4sigma_y_um
            / max(self.camera_settings.effective_pixel_um_y, 1e-9)
            / 4.0,
        )

        def residual(p):
            amp, x_c, y_c, sx, sy, bg = p
            model = bg + amp * np.exp(
                -0.5
                * (
                    ((xx - x_c) / sx) ** 2
                    + ((yy - y_c) / sy) ** 2
                )
            )
            return (model - z).ravel()

        fit = least_squares(
            residual,
            [
                max(amp0 - bg0, 1.0),
                cx,
                cy,
                sx0,
                sy0,
                bg0,
            ],
            bounds=(
                [0.0, 0.0, 0.0, 0.5, 0.5, -np.inf],
                [np.inf, w - 1.0, h - 1.0, w, h, np.inf],
            ),
            max_nfev=60,
        )
        if not fit.success:
            return

        _, gx, gy, gsx, gsy, _ = fit.x
        result.gaussian_fit_ok = True
        result.gaussian_cx_px = gx + ox
        result.gaussian_cy_px = gy + oy
        f = 2.0 * math.sqrt(2.0 * math.log(2.0))
        result.gaussian_fwhm_x_um = (
            f * abs(gsx) * self.camera_settings.effective_pixel_um_x
        )
        result.gaussian_fwhm_y_um = (
            f * abs(gsy) * self.camera_settings.effective_pixel_um_y
        )
