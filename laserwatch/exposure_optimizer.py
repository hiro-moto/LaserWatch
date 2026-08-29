from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass
class ExposureDecision:
    done: bool
    new_raw: int | None = None
    message: str = ""
    peak_fraction: float = 0.0
    success: bool = False


class ExposureOptimizer:
    """
    Non-blocking beam-profiler exposure optimizer.

    The controller operates on the camera's discrete DirectShow raw exposure values
    (log2 seconds). After issuing a new exposure it intentionally ignores a configurable
    number of analyzed frames so the camera/driver can settle.

    It uses RAW peak/full-scale, not dark-subtracted peak, because the purpose here is
    ADC utilization and saturation avoidance.
    """

    def __init__(
        self,
        valid_raw_values: list[int],
        target_fraction: float = 0.80,
        tolerance_low: float = 0.08,
        tolerance_high: float = 0.05,
        settle_frames: int = 3,
        required_good_frames: int = 2,
        max_iterations: int = 16,
    ):
        values = sorted(set(int(v) for v in valid_raw_values))
        if not values:
            raise ValueError("No valid exposure values")
        if not (0.05 <= target_fraction <= 0.95):
            raise ValueError("target_fraction out of range")

        self.values = values
        self.target = float(target_fraction)
        self.low = max(0.01, self.target - float(tolerance_low))
        self.high = min(0.98, self.target + float(tolerance_high))
        self.settle_frames = max(0, int(settle_frames))
        self.required_good_frames = max(1, int(required_good_frames))
        self.max_iterations = max(1, int(max_iterations))

        self.active = False
        self.current_raw = values[0]
        self._wait = 0
        self._iterations = 0
        self._good_frames = 0

    def start(self, current_raw: int) -> None:
        self.current_raw = self._nearest_valid(int(current_raw))
        self._wait = 0
        self._iterations = 0
        self._good_frames = 0
        self.active = True

    def cancel(self) -> None:
        self.active = False

    def _nearest_valid(self, raw: int) -> int:
        return min(self.values, key=lambda x: abs(x - raw))

    def _index(self, raw: int) -> int:
        value = self._nearest_valid(raw)
        return self.values.index(value)

    def feed(self, result) -> ExposureDecision:
        if not self.active:
            return ExposureDecision(done=True, message="Inactive")

        if self._wait > 0:
            self._wait -= 1
            return ExposureDecision(
                done=False,
                message=f"Settling ({self._wait} frames)",
                peak_fraction=float(getattr(result, "raw_peak_fraction", 0.0)),
            )

        full_scale = float(getattr(result, "full_scale", 0.0))
        raw_peak = float(getattr(result, "raw_peak", 0.0))
        sat_frac = float(getattr(result, "saturation_fraction", 0.0))

        if not math.isfinite(full_scale) or full_scale <= 0:
            return self._finish(False, "Invalid camera full scale")

        ratio = raw_peak / full_scale if math.isfinite(raw_peak) else 0.0
        ratio = max(0.0, ratio)

        if self.low <= ratio <= self.high and sat_frac <= 0:
            self._good_frames += 1
            if self._good_frames >= self.required_good_frames:
                return self._finish(
                    True,
                    f"Locked at {100.0 * ratio:.1f}% full scale",
                    ratio,
                )
            return ExposureDecision(
                done=False,
                message=f"Confirming target ({self._good_frames}/{self.required_good_frames})",
                peak_fraction=ratio,
            )

        self._good_frames = 0

        if self._iterations >= self.max_iterations:
            return self._finish(False, "Maximum optimization iterations reached", ratio)

        current_index = self._index(self.current_raw)

        if sat_frac > 0 or ratio > self.high:
            # Saturation hides the true overexposure amount; always move shorter.
            if sat_frac > 0:
                step_count = 1
            else:
                factor = self.target / max(ratio, 1e-6)
                step_count = max(1, int(round(abs(math.log2(max(factor, 1e-6))))))
            new_index = max(0, current_index - min(step_count, 3))
        else:
            # Too dim. Since raw exposure is log2(seconds), log2(target/current)
            # directly estimates the number of exposure stops to add.
            if ratio <= 1e-6:
                step_count = 3
            else:
                step_count = max(
                    1,
                    int(round(math.log2(self.target / max(ratio, 1e-6)))),
                )
            new_index = min(len(self.values) - 1, current_index + min(step_count, 3))

        new_raw = self.values[new_index]

        if new_raw == self.current_raw:
            state = "minimum" if new_index == 0 else "maximum"
            return self._finish(
                False,
                f"Reached camera {state} exposure before target",
                ratio,
            )

        self.current_raw = new_raw
        self._iterations += 1
        self._wait = self.settle_frames

        return ExposureDecision(
            done=False,
            new_raw=new_raw,
            message=f"Adjusting exposure ({self._iterations}/{self.max_iterations})",
            peak_fraction=ratio,
        )

    def _finish(self, success: bool, message: str, ratio: float = 0.0) -> ExposureDecision:
        self.active = False
        return ExposureDecision(
            done=True,
            message=message,
            peak_fraction=ratio,
            success=success,
        )
