from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass
class TrackingDecision:
    state: str
    roi: tuple[int, int, int, int] | None
    break_series: bool = False


class AutoRoiTracker:
    """
    SEARCHING -> TRACKING -> SEARCHING state machine.

    SEARCHING always requests full-frame analysis (roi=None). Once a valid detected
    spot is found, the tracker returns a centered ROI. If the spot disappears, the
    next analysis is full-frame again, so the tracker cannot remain trapped in a
    wrong dark ROI indefinitely.
    """

    def __init__(self):
        self.state = "OFF"

    def enable(self):
        self.state = "SEARCHING"

    def disable(self):
        self.state = "OFF"

    @staticmethod
    def _valid(result) -> bool:
        return bool(
            getattr(result, "detection_state", "") == "DETECTED"
            and getattr(result, "quality", "") != "BEAM_NOT_FOUND"
            and math.isfinite(float(getattr(result, "cx_px", float("nan"))))
            and math.isfinite(float(getattr(result, "cy_px", float("nan"))))
        )

    @staticmethod
    def centered_roi(cx, cy, frame_shape, width, height):
        fh, fw = int(frame_shape[0]), int(frame_shape[1])
        rw = min(max(16, int(width)), fw)
        rh = min(max(16, int(height)), fh)
        x = int(round(float(cx) - rw / 2.0))
        y = int(round(float(cy) - rh / 2.0))
        x = max(0, min(fw - rw, x))
        y = max(0, min(fh - rh, y))
        return x, y, rw, rh

    def update(self, result, frame_shape, width, height) -> TrackingDecision:
        if self.state == "OFF":
            return TrackingDecision("OFF", None, False)

        if not self._valid(result):
            was_tracking = self.state == "TRACKING"
            self.state = "SEARCHING"
            return TrackingDecision(
                state="SEARCHING",
                roi=None,
                break_series=was_tracking,
            )

        roi = self.centered_roi(
            result.cx_px,
            result.cy_px,
            frame_shape,
            width,
            height,
        )
        was_searching = self.state == "SEARCHING"
        self.state = "TRACKING"
        return TrackingDecision(
            state="TRACKING",
            roi=roi,
            break_series=was_searching,
        )
