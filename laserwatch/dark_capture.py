from __future__ import annotations
import numpy as np
from .analysis import validate_frame


class DarkFrameAccumulator:
    def __init__(self):
        self.target_frames = 0
        self.count = 0
        self._sum = None
        self._shape = None

    @property
    def active(self):
        return self.target_frames > 0 and self.count < self.target_frames

    @property
    def progress(self):
        return self.count, self.target_frames

    def start(self, n_frames):
        n = int(n_frames)
        if n <= 0:
            raise ValueError("Dark-frame count must be positive")
        self.target_frames = n
        self.count = 0
        self._sum = None
        self._shape = None

    def cancel(self):
        self.target_frames = 0
        self.count = 0
        self._sum = None
        self._shape = None

    def add_frame(self, frame):
        if not self.active:
            return None
        src = validate_frame(frame)
        if self._sum is None:
            self._shape = src.shape
            self._sum = np.zeros(src.shape, dtype=np.float64)
        elif src.shape != self._shape:
            expected = self._shape
            self.cancel()
            raise ValueError(
                f"Frame shape changed during dark capture: expected {expected}, got {src.shape}"
            )
        self._sum += src.astype(np.float64, copy=False)
        self.count += 1
        if self.count < self.target_frames:
            return None

        result = (self._sum / float(self.target_frames)).astype(np.float32)
        self.target_frames = 0
        self._sum = None
        self._shape = None
        return result
