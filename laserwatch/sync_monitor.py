from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SyncStatus:
    camera_count: int
    skew_ms: float
    oldest_name: str
    newest_name: str


def compute_sync_status(entries) -> SyncStatus | None:
    """Compute latest-frame timestamp spread for entries of (name, timestamp_ns)."""
    valid = [(str(name), int(ts)) for name, ts in entries if ts is not None and int(ts) > 0]
    if len(valid) < 2:
        return None
    oldest = min(valid, key=lambda item: item[1])
    newest = max(valid, key=lambda item: item[1])
    return SyncStatus(
        camera_count=len(valid),
        skew_ms=(newest[1] - oldest[1]) / 1e6,
        oldest_name=oldest[0],
        newest_name=newest[0],
    )
