# -*- coding: utf-8 -*-

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
import globals

import pyqtgraph as pg
from pyqtgraph.Qt import QtGui, QtCore
import numpy as np
import time
from collections import deque

class Plot(QWidget):
    # Emitted with the wavelength (nm) whenever the main yellow cursor moves.
    cursor_moved = pyqtSignal(float)

    def __init__(self, parent=None):
        super(Plot, self).__init__(parent)
        self.plot_widget = pg.PlotWidget()

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: transparent;
                border: none;
            }
            QProgressBar::chunk {
                background-color: #24ad11;
            }
        """)
        # Create a layout and add the plot_widget and progress_bar
        mainLayout = QVBoxLayout()
        mainLayout.setSpacing(0)  # Remove space between widgets
        mainLayout.setContentsMargins(0, 0, 0, 0)  # Remove margins around the layout
        
        # Create a layout and add the plot_widget to it
        mainLayout = QVBoxLayout()
        mainLayout.addWidget(self.plot_widget)
        mainLayout.addWidget(self.progress_bar)
        self.setLayout(mainLayout)

        # Set axis labels
        self.plot_widget.setLabel('bottom', 'Wavelength', units='nm')
        self.plot_widget.setLabel('left', 'Counts')

        # Set axis ranges
        self.plot_widget.setXRange(180, 1170)
        self.plot_widget.setYRange(-500, 72000)

        # Show grid lines on both axes
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)



        # Add a plot curve
        self.curve = self.plot_widget.plot(pen='y')

        self.count_limit_label = QLabel('65000', self.plot_widget)
        self.count_limit_label.setStyleSheet("color: red; background: transparent; font-size: 8pt;")
        self.count_limit_label.adjustSize()
        self.count_limit_label.raise_()
        self.plot_widget.getPlotItem().vb.sigRangeChanged.connect(self.update_count_limit_label)
        QTimer.singleShot(0, self.update_count_limit_label)

        # Enable panning and zooming
        self.plot_widget.setMouseEnabled(x=True, y=True)
        self.plot_widget.addLegend()

        # Add main Vertical line
        self.threshold_value = 532
        self.v_line = pg.InfiniteLine(pos=self.threshold_value, angle=90, movable=True)
        self.plot_widget.addItem(self.v_line)

        # Add additional Vertical margins
        self.margin_value = 50
        self.l_line = pg.InfiniteLine(pos=self.threshold_value - self.margin_value, angle=90, movable=True, pen=pg.mkPen(style=QtCore.Qt.DashLine))
        self.plot_widget.addItem(self.l_line)
        self.r_line = pg.InfiniteLine(pos=self.threshold_value + self.margin_value, angle=90, movable=True, pen=pg.mkPen(style=QtCore.Qt.DashLine))
        self.plot_widget.addItem(self.r_line)

        # Connect signals when lines move
        self.v_line.sigPositionChanged.connect(self.on_vline_moved)
        self.l_line.sigPositionChanged.connect(self.on_l_margin_moved)
        self.r_line.sigPositionChanged.connect(self.on_r_margin_moved)

        # --- Inset: power vs time over the last 5 minutes (top-right corner) ---
        self.power_history_seconds = 300  # 5 minutes
        self._power_times = deque()
        self._power_values = deque()

        # A small PlotWidget floating over the main spectrum. Child of the main
        # plot_widget so it moves/clips with it; repositioned in resizeEvent.
        self.power_inset = pg.PlotWidget(parent=self.plot_widget)
        self.power_inset.setBackground((25, 25, 25, 220))
        self.power_inset.setMenuEnabled(False)
        self.power_inset.setMouseEnabled(x=False, y=False)
        self.power_inset.hideButtons()
        self.power_inset.setTitle("Power (last 5 min)", size="8pt")
        self.power_inset.setLabel('bottom', 'Time', units='s')
        self.power_inset.setLabel('left', 'Power', units='W')
        tick_font = QtGui.QFont()
        tick_font.setPointSize(7)
        self.power_inset.getAxis('bottom').setStyle(tickFont=tick_font)
        self.power_inset.getAxis('left').setStyle(tickFont=tick_font)
        self.power_inset.setXRange(-self.power_history_seconds, 0, padding=0)
        self.power_inset.showGrid(x=True, y=True, alpha=0.3)
        self.power_curve = self.power_inset.plot(pen=pg.mkPen('c', width=1))
        self.power_inset.raise_()
        self._position_power_inset()

    def _position_power_inset(self):
        if not hasattr(self, "power_inset"):
            return
        margin = 10
        w = max(180, int(self.plot_widget.width() * 0.28))
        h = max(120, int(self.plot_widget.height() * 0.28))
        x = self.plot_widget.width() - w - margin
        self.power_inset.setGeometry(x, margin, w, h)

    def add_power_sample(self, watts):
        """Append a power reading (watts) and refresh the rolling 5-minute trace."""
        now = time.monotonic()
        self._power_times.append(now)
        self._power_values.append(watts)
        cutoff = now - self.power_history_seconds
        while self._power_times and self._power_times[0] < cutoff:
            self._power_times.popleft()
            self._power_values.popleft()
        t = np.fromiter(self._power_times, dtype=float) - now  # newest = 0, older negative
        y = np.fromiter(self._power_values, dtype=float)
        self.power_curve.setData(t, y)

    def update_plot(self):
        # Generate some dummy data
        x_data = np.array(globals.wavelength[:globals.pixels])
        y_data = np.array(globals.spectraldata[:globals.pixels])

        # Update the curve with new data
        self.curve.setData(x_data, y_data)
        self.update_count_limit_label()
        self.show()
        
    def get_threshold_value(self):
        return self.threshold_value

    def get_margin_value(self):
        return self.margin_value

    def resizeEvent(self, event):
        super(Plot, self).resizeEvent(event)
        self.update_count_limit_label()
        self._position_power_inset()

    def update_count_limit_label(self):
        if not hasattr(self, "count_limit_label"):
            return

        view_box = self.plot_widget.getPlotItem().vb
        scene_pos = view_box.mapViewToScene(QPointF(view_box.viewRange()[0][0], 65000))
        widget_pos = self.plot_widget.mapFromScene(scene_pos)
        axis_width = int(self.plot_widget.getPlotItem().getAxis('left').width())
        x = max(0, axis_width - self.count_limit_label.width() - 4)
        y = int(widget_pos.y() - self.count_limit_label.height() / 2)
        self.count_limit_label.move(x, y)

    def on_vline_moved(self):
        self.threshold_value = self.v_line.value()
        self.r_line.setValue(self.v_line.value() + self.margin_value)
        self.l_line.setValue(self.v_line.value() - self.margin_value)
        self.cursor_moved.emit(self.threshold_value)

    def on_l_margin_moved(self):
        self.margin_value = abs(self.v_line.value() - self.l_line.value())
        self.r_line.setValue(self.v_line.value() + self.margin_value)

    def on_r_margin_moved(self):
        self.margin_value = abs(self.v_line.value() - self.r_line.value())
        self.l_line.setValue(self.v_line.value() - self.margin_value)




