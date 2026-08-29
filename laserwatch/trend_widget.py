from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QTabWidget,
    QSizePolicy,
)

log = logging.getLogger(__name__)


class TrendWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        try:
            import pyqtgraph as pg
        except Exception as exc:
            raise RuntimeError(
                f"pyqtgraph is required for trend plots: {exc}"
            ) from exc

        self.pg = pg
        self._origin_um = None
        self._window_s = 60.0
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Window"))

        self.window_combo = QComboBox()
        self.window_combo.addItem("10 s", 10.0)
        self.window_combo.addItem("60 s", 60.0)
        self.window_combo.addItem("10 min", 600.0)
        self.window_combo.addItem("All", None)
        self.window_combo.setCurrentIndex(1)
        self.window_combo.currentIndexChanged.connect(self._on_window_changed)
        controls.addWidget(self.window_combo)

        self.clear_button = QPushButton("Clear trends")
        controls.addWidget(self.clear_button)

        self.time_mode_label = QLabel("Rolling: -60 … 0 s")
        controls.addWidget(self.time_mode_label)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMinimumHeight(140)
        layout.addWidget(self.tabs, 1)

        self.position_plot = self._new_plot("Position", "µm")
        self.x_curve = self.position_plot.plot(name="X")
        self.y_curve = self.position_plot.plot(name="Y")
        self.tabs.addTab(self.position_plot, "Pointing")

        self.size_plot = self._new_plot("Diameter", "µm")
        self.d4x_curve = self.size_plot.plot(name="D4σ X")
        self.d4y_curve = self.size_plot.plot(name="D4σ Y")
        self.tabs.addTab(self.size_plot, "Beam size")

        self.intensity_plot = self._new_plot("Integrated counts", "")
        self.int_curve = self.intensity_plot.plot(name="Integrated")
        self.tabs.addTab(self.intensity_plot, "Intensity")

        self.fft_plot = pg.PlotWidget()
        self.fft_plot.setLabel("bottom", "Frequency", units="Hz")
        self.fft_plot.setLabel("left", "FFT amplitude", units="µm")
        self.fft_plot.showGrid(x=True, y=True, alpha=0.25)
        self.fft_plot.addLegend()
        # Frequency is logarithmic for readability over a broad band. Amplitude
        # stays linear so peaks retain the intuitive displacement unit [µm].
        self.fft_plot.setLogMode(x=True, y=False)
        self.fft_x_curve = self.fft_plot.plot(name="X FFT")
        self.fft_y_curve = self.fft_plot.plot(name="Y FFT")
        self.tabs.addTab(self.fft_plot, "FFT")

    def _new_plot(self, ylabel, units):
        plot = self.pg.PlotWidget()
        plot.setLabel("bottom", "Time relative to now", units="s")
        plot.setLabel("left", ylabel, units=units)
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.addLegend()
        return plot

    @property
    def window_s(self):
        return self._window_s

    def set_origin(self, origin_um):
        self._origin_um = origin_um

    def _on_window_changed(self):
        self._window_s = self.window_combo.currentData()
        if self._window_s is None:
            self.time_mode_label.setText("Elapsed time")
        else:
            self.time_mode_label.setText(
                f"Rolling: -{self._window_s:g} … 0 s"
            )

    @staticmethod
    def _with_segment_breaks(t, y, segments):
        if not t:
            return [], []
        out_t = [float(t[0])]
        out_y = [float(y[0])]
        for i in range(1, len(t)):
            if segments[i] != segments[i - 1]:
                out_t.append(float("nan"))
                out_y.append(float("nan"))
            out_t.append(float(t[i]))
            out_y.append(float(y[i]))
        return out_t, out_y

    def _set_time_axis(self, plots, t):
        if not t:
            return []
        if self._window_s is None:
            for plot in plots:
                plot.enableAutoRange(axis="x", enable=True)
            return list(t)

        latest = float(t[-1])
        shifted = [float(v) - latest for v in t]
        for plot in plots:
            plot.setXRange(-float(self._window_s), 0.0, padding=0.0)
        return shifted

    def update_from_buffer(self, buffer):
        try:
            arrays = buffer.arrays(self._window_s)
            t = arrays["t"]
            if not t:
                for curve in (
                    self.x_curve,
                    self.y_curve,
                    self.d4x_curve,
                    self.d4y_curve,
                    self.int_curve,
                    self.fft_x_curve,
                    self.fft_y_curve,
                ):
                    curve.setData([], [])
                return

            tplot = self._set_time_axis(
                (self.position_plot, self.size_plot, self.intensity_plot),
                t,
            )
            x = list(arrays["x"])
            y = list(arrays["y"])
            if self._origin_um is not None:
                ox, oy = self._origin_um
                x = [v - ox for v in x]
                y = [v - oy for v in y]

            segments = arrays["segment"]
            tx, xx = self._with_segment_breaks(tplot, x, segments)
            ty, yy = self._with_segment_breaks(tplot, y, segments)
            td4x, d4x = self._with_segment_breaks(
                tplot,
                arrays["d4x"],
                segments,
            )
            td4y, d4y = self._with_segment_breaks(
                tplot,
                arrays["d4y"],
                segments,
            )
            ti, intensity = self._with_segment_breaks(
                tplot,
                arrays["intensity"],
                segments,
            )

            self.x_curve.setData(tx, xx, connect="finite")
            self.y_curve.setData(ty, yy, connect="finite")
            self.d4x_curve.setData(td4x, d4x, connect="finite")
            self.d4y_curve.setData(td4y, d4y, connect="finite")
            self.int_curve.setData(ti, intensity, connect="finite")

            fft = buffer.pointing_fft(self._window_s)
            f = fft["f"]
            if f:
                # pointing_fft() deliberately omits f=0, so the FFT tab never
                # shows the DC component even if a large pointing offset exists.
                self.fft_x_curve.setData(f, fft["amp_x"])
                self.fft_y_curve.setData(f, fft["amp_y"])
            else:
                self.fft_x_curve.setData([], [])
                self.fft_y_curve.setData([], [])

        except Exception:
            log.exception("Trend/FFT plot update failed")
