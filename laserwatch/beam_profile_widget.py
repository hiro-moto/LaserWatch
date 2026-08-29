from __future__ import annotations

import logging
import math

import numpy as np
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QSizePolicy,
)

log = logging.getLogger(__name__)


class BeamProfileWidget(QWidget):
    """
    Live X/Y beam cross sections through the measured intensity centroid.

    Measured data are a short-strip average around the centroid.  The optional
    comparison curve is a Gaussian-equivalent profile constructed from the
    measured FWHM; it is not a nonlinear Gaussian fit.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        try:
            import pyqtgraph as pg
        except Exception as exc:
            raise RuntimeError(
                f"pyqtgraph is required for beam profiles: {exc}"
            ) from exc

        self.pg = pg
        self._data = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        controls = QHBoxLayout()
        self.info_label = QLabel("No detected beam profile")
        controls.addWidget(self.info_label)

        controls.addStretch(1)

        self.normalize = QCheckBox("Normalize to peak")
        self.normalize.setChecked(False)
        self.normalize.toggled.connect(self._refresh)
        controls.addWidget(self.normalize)

        self.show_gaussian = QCheckBox("Gaussian-equivalent")
        self.show_gaussian.setChecked(True)
        self.show_gaussian.toggled.connect(self._refresh)
        controls.addWidget(self.show_gaussian)

        root.addLayout(controls)

        plots = QHBoxLayout()
        plots.setSpacing(6)

        self.x_plot = pg.PlotWidget()
        self.x_plot.setLabel("bottom", "X from centroid", units="µm")
        self.x_plot.setLabel("left", "Intensity", units="counts")
        self.x_plot.showGrid(x=True, y=True, alpha=0.25)
        self.x_plot.addLegend()
        self.x_measured = self.x_plot.plot(name="Measured")
        self.x_gaussian = self.x_plot.plot(name="Gaussian eq.")

        self.y_plot = pg.PlotWidget()
        self.y_plot.setLabel("bottom", "Y from centroid", units="µm")
        self.y_plot.setLabel("left", "Intensity", units="counts")
        self.y_plot.showGrid(x=True, y=True, alpha=0.25)
        self.y_plot.addLegend()
        self.y_measured = self.y_plot.plot(name="Measured")
        self.y_gaussian = self.y_plot.plot(name="Gaussian eq.")

        plots.addWidget(self.x_plot, 1)
        plots.addWidget(self.y_plot, 1)
        root.addLayout(plots, 1)

    def clear(self):
        self._data = None
        self.info_label.setText("No detected beam profile")
        for curve in (
            self.x_measured,
            self.x_gaussian,
            self.y_measured,
            self.y_gaussian,
        ):
            curve.setData([], [])

    def set_profile(self, data):
        try:
            if not data:
                self.clear()
                return
            self._data = data
            self._refresh()
        except Exception:
            log.exception("Failed to update beam-profile widget")
            self.clear()

    @staticmethod
    def _normalized(measured, gaussian):
        measured = np.asarray(measured, dtype=np.float64)
        gaussian = np.asarray(gaussian, dtype=np.float64)
        peak = float(np.nanmax(measured)) if measured.size else 0.0
        if not math.isfinite(peak) or peak <= 0:
            return measured, gaussian
        return measured / peak, gaussian / peak

    def _refresh(self):
        try:
            d = self._data
            if not d:
                self.clear()
                return

            x = np.asarray(d["x_um"], dtype=np.float64)
            xi = np.asarray(d["x_intensity"], dtype=np.float64)
            xg = np.asarray(d["x_gaussian"], dtype=np.float64)
            y = np.asarray(d["y_um"], dtype=np.float64)
            yi = np.asarray(d["y_intensity"], dtype=np.float64)
            yg = np.asarray(d["y_gaussian"], dtype=np.float64)

            if self.normalize.isChecked():
                xi, xg = self._normalized(xi, xg)
                yi, yg = self._normalized(yi, yg)
                self.x_plot.setLabel("left", "Normalized intensity")
                self.y_plot.setLabel("left", "Normalized intensity")
            else:
                self.x_plot.setLabel("left", "Intensity", units="counts")
                self.y_plot.setLabel("left", "Intensity", units="counts")

            self.x_measured.setData(x, xi)
            self.y_measured.setData(y, yi)

            if self.show_gaussian.isChecked():
                self.x_gaussian.setData(x, xg, connect="finite")
                self.y_gaussian.setData(y, yg, connect="finite")
            else:
                self.x_gaussian.setData([], [])
                self.y_gaussian.setData([], [])

            self.info_label.setText(
                f"Centroid cross-section | channel={d['analysis_channel']} | "
                f"strip={d['strip_width_px']} px | "
                f"FWHM X={d['fwhm_x_um']:.3f} µm, "
                f"Y={d['fwhm_y_um']:.3f} µm"
            )
        except Exception:
            log.exception("Beam-profile redraw failed")
