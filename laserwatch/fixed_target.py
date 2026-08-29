from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from .analysis import (
    detect_principal_spot,
    full_scale_for_frame,
    select_analysis_plane,
    validate_frame,
)


@dataclass
class FixedTargetSelection:
    requested_x: float
    requested_y: float
    target_x: float
    target_y: float
    snapped: bool
    roi: tuple[int, int, int, int]


def centered_roi(cx, cy, frame_shape, width, height):
    fh, fw = int(frame_shape[0]), int(frame_shape[1])
    rw = min(max(16, int(width)), fw)
    rh = min(max(16, int(height)), fh)
    x = int(round(float(cx) - rw / 2.0))
    y = int(round(float(cy) - rh / 2.0))
    x = max(0, min(fw - rw, x))
    y = max(0, min(fh - rh, y))
    return x, y, rw, rh


def _corrected_analysis_plane(frame, dark_frame, settings):
    source = validate_frame(frame)
    plane, _ = select_analysis_plane(source, settings.analysis_channel)
    img = plane.astype(np.float32, copy=True)

    if dark_frame is not None:
        dark = validate_frame(dark_frame)
        if dark.shape == source.shape:
            dark_plane, _ = select_analysis_plane(
                dark,
                settings.analysis_channel,
            )
            img -= dark_plane.astype(np.float32, copy=False)
    elif settings.background_level:
        img -= float(settings.background_level)

    np.nan_to_num(img, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    np.maximum(img, 0.0, out=img)
    return source, img


def select_fixed_target(
    frame,
    dark_frame,
    settings,
    click_xy,
    roi_width,
    roi_height,
    snap_radius_px=80,
):
    """
    Pick a fixed target from a user click.

    Only a local square around the click is searched, so a much brighter remote
    reflection cannot steal the selection. Within that local area, the connected
    component nearest the click is selected. If no spot is found, the click itself
    remains the fixed anchor and the measurement waits there.
    """
    source, img = _corrected_analysis_plane(frame, dark_frame, settings)
    full_scale, _, _ = full_scale_for_frame(
        source,
        settings.bit_depth_override,
    )

    cx, cy = map(float, click_xy)
    h, w = img.shape[:2]
    cx = max(0.0, min(float(w - 1), cx))
    cy = max(0.0, min(float(h - 1), cy))

    radius = max(4, int(snap_radius_px))
    x0 = max(0, int(math.floor(cx)) - radius)
    y0 = max(0, int(math.floor(cy)) - radius)
    x1 = min(w, int(math.floor(cx)) + radius + 1)
    y1 = min(h, int(math.floor(cy)) + radius + 1)

    patch = img[y0:y1, x0:x1]
    preferred = (cx - x0, cy - y0)
    detected = detect_principal_spot(
        patch,
        settings,
        full_scale,
        preferred_xy=preferred,
    )

    snapped = detected is not None
    if detected is not None:
        local_x, local_y = detected["centroid"]
        target_x = float(x0) + float(local_x)
        target_y = float(y0) + float(local_y)
    else:
        target_x = cx
        target_y = cy

    roi = centered_roi(
        target_x,
        target_y,
        source.shape[:2],
        roi_width,
        roi_height,
    )

    return FixedTargetSelection(
        requested_x=cx,
        requested_y=cy,
        target_x=target_x,
        target_y=target_y,
        snapped=snapped,
        roi=roi,
    )
