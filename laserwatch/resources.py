from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def app_icon_path() -> Path:
    ico = resource_path("assets", "icon.ico")
    if ico.exists():
        return ico
    return resource_path("assets", "icon.png")
