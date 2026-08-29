from __future__ import annotations

import hashlib


def profile_key(persistent_id: str) -> str:
    text = (persistent_id or "unknown-camera").encode("utf-8", errors="replace")
    return hashlib.sha1(text).hexdigest()[:20]
