# -*- coding: utf-8 -*-

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
import globals

import pyqtgraph as pg
from pyqtgraph.Qt import QtGui, QtCore
import numpy as np

class Plot(QWidget):
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

    def on_l_margin_moved(self):
        self.margin_value = abs(self.v_line.value() - self.l_line.value())
        self.r_line.setValue(self.v_line.value() + self.margin_value)

    def on_r_margin_moved(self):
        self.margin_value = abs(self.v_line.value() - self.r_line.value())
        self.l_line.setValue(self.v_line.value() - self.margin_value)




