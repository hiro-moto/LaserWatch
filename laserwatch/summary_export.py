from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


def _finite_or_none(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def build_measurement_summary(camera, statistics: dict, baseline=None, extra=None) -> dict:
    summary = {
        "format": "LaserWatch measurement summary",
        "version": "0.8.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "camera": asdict(camera),
        "statistics": {},
        "reference": baseline or None,
    }
    for key, value in (statistics or {}).items():
        if isinstance(value, (int, str, bool)):
            summary["statistics"][key] = value
        else:
            summary["statistics"][key] = _finite_or_none(value)
    if extra:
        summary["recording"] = dict(extra)
    return summary


def write_summary_json(path: Path, summary: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_summary_csv(path: Path, summary: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for section in ("statistics", "reference", "recording"):
        values = summary.get(section) or {}
        if isinstance(values, dict):
            for key, value in values.items():
                rows.append((section, key, value))
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["section", "metric", "value"])
        writer.writerows(rows)
    return path
