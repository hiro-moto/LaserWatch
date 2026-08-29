from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import numpy as np


@dataclass
class TimeSeriesPoint:
    t_s: float
    x_um: float
    y_um: float
    d4x_um: float
    d4y_um: float
    intensity: float
    peak: float
    segment: int


class TimeSeriesBuffer:
    def __init__(self, max_points: int = 20000):
        self.max_points = int(max_points)
        self._points = deque(maxlen=self.max_points)
        self._t0_ns = None
        self._segment = 0

    def clear(self):
        self._points.clear()
        self._t0_ns = None
        self._segment = 0

    def break_series(self):
        self._segment += 1

    def append_result(self, result):
        timestamp_ns = int(result.timestamp_ns)
        if self._t0_ns is None:
            self._t0_ns = timestamp_ns

        t_s = (timestamp_ns - self._t0_ns) / 1e9
        self._points.append(
            TimeSeriesPoint(
                t_s=t_s,
                x_um=float(result.cx_um),
                y_um=float(result.cy_um),
                d4x_um=float(result.d4sigma_x_um),
                d4y_um=float(result.d4sigma_y_um),
                intensity=float(result.integrated),
                peak=float(result.peak),
                segment=self._segment,
            )
        )

    def _window_points(self, window_s=None):
        points = list(self._points)
        if not points:
            return []
        if window_s is not None and math.isfinite(window_s) and window_s > 0:
            cutoff = points[-1].t_s - float(window_s)
            points = [p for p in points if p.t_s >= cutoff]
        return points

    def arrays(self, window_s=None):
        points = self._window_points(window_s)
        if not points:
            return {
                "t": [],
                "x": [],
                "y": [],
                "d4x": [],
                "d4y": [],
                "intensity": [],
                "peak": [],
                "segment": [],
            }
        return {
            "t": [p.t_s for p in points],
            "x": [p.x_um for p in points],
            "y": [p.y_um for p in points],
            "d4x": [p.d4x_um for p in points],
            "d4y": [p.d4y_um for p in points],
            "intensity": [p.intensity for p in points],
            "peak": [p.peak for p in points],
            "segment": [p.segment for p in points],
        }

    def statistics(self, window_s=None):
        points = self._window_points(window_s)
        if not points:
            return {"count": 0}

        xp = np.asarray([p.x_um for p in points], dtype=np.float64)
        yp = np.asarray([p.y_um for p in points], dtype=np.float64)
        pair_mask = np.isfinite(xp) & np.isfinite(yp)
        xp, yp = xp[pair_mask], yp[pair_mask]
        if len(xp) == 0:
            return {"count": 0}

        def finite(values):
            a = np.asarray(values, dtype=np.float64)
            return a[np.isfinite(a)]

        def stat(a):
            if len(a) == 0:
                return float("nan"), float("nan"), float("nan")
            return (
                float(np.mean(a)),
                float(np.std(a, ddof=0)),
                float(np.max(a) - np.min(a)),
            )

        d4x = finite([p.d4x_um for p in points])
        d4y = finite([p.d4y_um for p in points])
        intensity = finite([p.intensity for p in points])

        d4x_mean, d4x_sigma, d4x_ptp = stat(d4x)
        d4y_mean, d4y_sigma, d4y_ptp = stat(d4y)
        int_mean, int_sigma, int_ptp = stat(intensity)

        mean_x, mean_y = float(np.mean(xp)), float(np.mean(yp))
        dx, dy = xp - mean_x, yp - mean_y
        radial_rms = float(np.sqrt(np.mean(dx * dx + dy * dy)))
        intensity_cv = (
            100.0 * int_sigma / abs(int_mean)
            if math.isfinite(int_mean) and abs(int_mean) > 1e-15
            else float("nan")
        )

        return {
            "count": int(len(xp)),
            "mean_x": mean_x,
            "mean_y": mean_y,
            "sigma_x": float(np.std(xp, ddof=0)),
            "sigma_y": float(np.std(yp, ddof=0)),
            "ptp_x": float(np.max(xp) - np.min(xp)),
            "ptp_y": float(np.max(yp) - np.min(yp)),
            "radial_rms": radial_rms,
            "d4x_mean": d4x_mean,
            "d4x_sigma": d4x_sigma,
            "d4x_ptp": d4x_ptp,
            "d4y_mean": d4y_mean,
            "d4y_sigma": d4y_sigma,
            "d4y_ptp": d4y_ptp,
            "intensity_mean": int_mean,
            "intensity_sigma": int_sigma,
            "intensity_ptp": int_ptp,
            "intensity_cv_percent": intensity_cv,
        }

    @staticmethod
    def _empty_spectrum(kind="fft", sample_rate_hz=float("nan")):
        if kind == "psd":
            return {
                "f": [],
                "psd_x": [],
                "psd_y": [],
                "sample_rate_hz": sample_rate_hz,
            }
        return {
            "f": [],
            "amp_x": [],
            "amp_y": [],
            "sample_rate_hz": sample_rate_hz,
        }

    def _uniform_latest_pointing(self, window_s=None, max_points=8192):
        points = self._window_points(window_s)
        if len(points) < 16:
            return None

        # Never interpolate across a beam-loss/reacquisition discontinuity.
        latest_segment = points[-1].segment
        points = [p for p in points if p.segment == latest_segment]
        if len(points) < 16:
            return None

        t = np.asarray([p.t_s for p in points], dtype=np.float64)
        x = np.asarray([p.x_um for p in points], dtype=np.float64)
        y = np.asarray([p.y_um for p in points], dtype=np.float64)
        mask = np.isfinite(t) & np.isfinite(x) & np.isfinite(y)
        t, x, y = t[mask], x[mask], y[mask]
        if len(t) < 16:
            return None

        order = np.argsort(t)
        t, x, y = t[order], x[order], y[order]
        dt = np.diff(t)
        dt = dt[np.isfinite(dt) & (dt > 0)]
        if len(dt) < 8:
            return None

        median_dt = float(np.median(dt))
        if not math.isfinite(median_dt) or median_dt <= 0:
            return None

        n = int(round((t[-1] - t[0]) / median_dt)) + 1
        n = max(16, min(n, int(max_points)))
        tu = np.linspace(t[0], t[-1], n)
        xu = np.interp(tu, t, x)
        yu = np.interp(tu, t, y)
        actual_dt = float((tu[-1] - tu[0]) / max(n - 1, 1))
        if actual_dt <= 0:
            return None

        return tu, xu, yu, 1.0 / actual_dt

    def pointing_fft(self, window_s=None, max_points=8192):
        """One-sided pointing amplitude spectrum in micrometres.

        Irregular host timestamps are resampled at the median interval using only
        the latest contiguous valid segment. The mean is removed, a Hann window
        is applied, amplitudes are corrected for the window's coherent gain, and
        the DC bin (0 Hz) is omitted from the returned arrays.
        """
        uniform = self._uniform_latest_pointing(window_s, max_points=max_points)
        if uniform is None:
            return self._empty_spectrum("fft")

        _, xu, yu, fs = uniform
        n = len(xu)
        xu = xu - np.mean(xu)
        yu = yu - np.mean(yu)
        window = np.hanning(n)
        coherent_gain = float(np.sum(window))
        if coherent_gain <= 0:
            return self._empty_spectrum("fft", fs)

        fx = np.fft.rfft(xu * window)
        fy = np.fft.rfft(yu * window)
        freq = np.fft.rfftfreq(n, d=1.0 / fs)
        amp_x = np.abs(fx) / coherent_gain
        amp_y = np.abs(fy) / coherent_gain

        # Convert to a one-sided peak-amplitude spectrum. Nyquist (for even n)
        # is not doubled; all other positive-frequency bins are.
        if n > 2:
            if n % 2 == 0:
                amp_x[1:-1] *= 2.0
                amp_y[1:-1] *= 2.0
            else:
                amp_x[1:] *= 2.0
                amp_y[1:] *= 2.0

        positive = freq > 0.0
        return {
            "f": freq[positive].tolist(),
            "amp_x": amp_x[positive].tolist(),
            "amp_y": amp_y[positive].tolist(),
            "sample_rate_hz": fs,
        }

    def pointing_psd(self, window_s=None, max_points=8192):
        """Legacy one-sided PSD calculation retained for API compatibility."""
        uniform = self._uniform_latest_pointing(window_s, max_points=max_points)
        if uniform is None:
            return self._empty_spectrum("psd")

        _, xu, yu, fs = uniform
        n = len(xu)
        xu -= np.mean(xu)
        yu -= np.mean(yu)
        window = np.hanning(n)
        window_power = float(np.sum(window * window))
        if window_power <= 0:
            return self._empty_spectrum("psd", fs)

        fx = np.fft.rfft(xu * window)
        fy = np.fft.rfft(yu * window)
        freq = np.fft.rfftfreq(n, d=1.0 / fs)
        psd_x = (np.abs(fx) ** 2) / (fs * window_power)
        psd_y = (np.abs(fy) ** 2) / (fs * window_power)

        if n > 2:
            if n % 2 == 0:
                psd_x[1:-1] *= 2.0
                psd_y[1:-1] *= 2.0
            else:
                psd_x[1:] *= 2.0
                psd_y[1:] *= 2.0

        return {
            "f": freq.tolist(),
            "psd_x": psd_x.tolist(),
            "psd_y": psd_y.tolist(),
            "sample_rate_hz": fs,
        }
