from __future__ import annotations

import logging
import math
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QBrush
from PySide6.QtWidgets import QWidget

from .models import BeamResult

log = logging.getLogger(__name__)


class ImageView(QWidget):
    roi_selected = Signal(tuple)
    target_selected = Signal(tuple)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: Optional[QImage] = None
        self._shape = None
        self._result: Optional[BeamResult] = None
        self._roi = None
        self._tracking_state = "OFF"
        self._fixed_target = None
        self._target_select_enabled = False
        self._display_mode = "RAW"
        self._roi_select_enabled = True
        self._drag_start_widget = None
        self._drag_current_widget = None
        self._display_rect = None
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)

    def set_frame(self, frame: np.ndarray):
        try:
            if frame is None or frame.size == 0:
                return

            if frame.ndim == 2:
                if frame.dtype != np.uint8:
                    disp = cv2.normalize(
                        frame,
                        None,
                        0,
                        255,
                        cv2.NORM_MINMAX,
                    ).astype(np.uint8)
                else:
                    disp = frame
                h, w = disp.shape
                q = QImage(
                    disp.data,
                    w,
                    h,
                    disp.strides[0],
                    QImage.Format_Grayscale8,
                )
            else:
                rgb = cv2.cvtColor(frame[..., :3], cv2.COLOR_BGR2RGB)
                if rgb.dtype != np.uint8:
                    rgb = cv2.normalize(
                        rgb,
                        None,
                        0,
                        255,
                        cv2.NORM_MINMAX,
                    ).astype(np.uint8)
                h, w, _ = rgb.shape
                q = QImage(
                    rgb.data,
                    w,
                    h,
                    rgb.strides[0],
                    QImage.Format_RGB888,
                )

            self._image = q.copy()
            self._shape = (h, w)
            self.update()
        except Exception:
            log.exception("Failed to prepare frame for display")

    def set_result(self, result):
        self._result = result
        self.update()

    def set_tracking_state(self, state):
        self._tracking_state = str(state)
        self.update()

    def set_fixed_target(self, point):
        self._fixed_target = (
            None
            if point is None
            else (float(point[0]), float(point[1]))
        )
        self.update()

    def clear_fixed_target(self):
        self._fixed_target = None
        self.update()

    def set_target_selection_enabled(self, enabled):
        self._target_select_enabled = bool(enabled)
        if enabled:
            self.setCursor(Qt.CrossCursor)
        else:
            self.unsetCursor()
        self.update()

    def set_display_mode(self, mode):
        self._display_mode = str(mode)
        self.update()

    def set_roi(self, roi):
        self._roi = roi
        self.update()

    def clear_roi(self):
        self._roi = None
        self.update()

    def set_roi_selection_enabled(self, enabled):
        self._roi_select_enabled = bool(enabled)

    def _calculate_display_rect(self):
        if self._shape is None:
            return None
        h, w = self._shape
        s = min(self.width() / w, self.height() / h)
        dw, dh = w * s, h * s
        x0 = (self.width() - dw) / 2
        y0 = (self.height() - dh) / 2
        return QRectF(x0, y0, dw, dh), s

    def _widget_to_image(self, point):
        mapping = self._calculate_display_rect()
        if mapping is None:
            return None
        rect, s = mapping
        if not rect.contains(point):
            return None
        x = (point.x() - rect.left()) / s
        y = (point.y() - rect.top()) / s
        h, w = self._shape
        return (
            max(0.0, min(float(w - 1), x)),
            max(0.0, min(float(h - 1), y)),
        )

    def mousePressEvent(self, event):
        if (
            self._target_select_enabled
            and event.button() == Qt.LeftButton
            and self._image is not None
        ):
            point = self._widget_to_image(event.position())
            if point is not None:
                self._target_select_enabled = False
                self.unsetCursor()
                self.target_selected.emit((float(point[0]), float(point[1])))
                self.update()
                event.accept()
                return

        if (
            self._roi_select_enabled
            and event.button() == Qt.LeftButton
            and self._image is not None
            and self._widget_to_image(event.position()) is not None
        ):
            self._drag_start_widget = event.position()
            self._drag_current_widget = event.position()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_widget is not None:
            self._drag_current_widget = event.position()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_start_widget is None:
            super().mouseReleaseEvent(event)
            return

        try:
            start = self._widget_to_image(self._drag_start_widget)
            end = self._widget_to_image(event.position())
            if start is None:
                return

            if end is None:
                mapping = self._calculate_display_rect()
                if mapping is None:
                    return
                rect, _ = mapping
                clamped = QPointF(
                    max(rect.left(), min(rect.right(), event.position().x())),
                    max(rect.top(), min(rect.bottom(), event.position().y())),
                )
                end = self._widget_to_image(clamped)
            if end is None:
                return

            x0, y0 = start
            x1, y1 = end
            x = int(round(min(x0, x1)))
            y = int(round(min(y0, y1)))
            w = int(round(abs(x1 - x0))) + 1
            h = int(round(abs(y1 - y0))) + 1
            if w >= 4 and h >= 4:
                roi = (x, y, w, h)
                self._roi = roi
                self.roi_selected.emit(roi)
        except Exception:
            log.exception("ROI selection failed")
        finally:
            self._drag_start_widget = None
            self._drag_current_widget = None
            self.update()
            event.accept()

    def paintEvent(self, event):
        try:
            p = QPainter(self)
            p.fillRect(self.rect(), Qt.black)
            if self._image is None or self._shape is None:
                return

            mapping = self._calculate_display_rect()
            if mapping is None:
                return
            target, s = mapping
            p.drawImage(target, self._image)

            if self._roi is not None:
                x, y, w, h = self._roi
                p.setPen(QPen(Qt.yellow, 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(
                    QRectF(
                        target.left() + x * s,
                        target.top() + y * s,
                        w * s,
                        h * s,
                    )
                )

            if (
                self._drag_start_widget is not None
                and self._drag_current_widget is not None
            ):
                p.setPen(QPen(Qt.cyan, 1))
                p.setBrush(Qt.NoBrush)
                p.drawRect(
                    QRectF(
                        self._drag_start_widget,
                        self._drag_current_widget,
                    ).normalized()
                )

            r = self._result
            if (
                r is not None
                and getattr(r, "detection_state", "") == "DETECTED"
                and r.spot_bbox_w > 0
                and r.spot_bbox_h > 0
            ):
                p.setPen(QPen(Qt.green, 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(
                    QRectF(
                        target.left() + r.spot_bbox_x * s,
                        target.top() + r.spot_bbox_y * s,
                        r.spot_bbox_w * s,
                        r.spot_bbox_h * s,
                    )
                )

            if self._fixed_target is not None:
                tx = target.left() + self._fixed_target[0] * s
                ty = target.top() + self._fixed_target[1] * s
                p.setPen(QPen(Qt.blue, 2))
                p.drawLine(QPointF(tx - 10, ty), QPointF(tx + 10, ty))
                p.drawLine(QPointF(tx, ty - 10), QPointF(tx, ty + 10))

            if r is not None and math.isfinite(r.cx_px):
                cx = target.left() + r.cx_px * s
                cy = target.top() + r.cy_px * s
                p.setPen(QPen(Qt.green, 1))
                p.drawLine(QPointF(cx - 12, cy), QPointF(cx + 12, cy))
                p.drawLine(QPointF(cx, cy - 12), QPointF(cx, cy + 12))

            p.setPen(QPen(Qt.white, 1))
            p.drawText(
                10,
                20,
                f"{self._display_mode} | ROI mode: {self._tracking_state}",
            )

            if r is not None and r.quality == "SATURATED":
                p.setPen(QPen(Qt.red, 2))
                p.drawText(10, 42, "SATURATED")
            elif r is not None and r.quality == "BEAM_NOT_FOUND":
                p.setPen(QPen(Qt.yellow, 2))
                p.drawText(10, 42, "BEAM NOT FOUND / SEARCHING")

        except Exception:
            log.exception("ImageView paint failed")
