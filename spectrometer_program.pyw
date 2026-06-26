#!/usr/bin/env python3
import os
import sys

# Load the Ophir power-meter COM DLL graph BEFORE importing PyQt5 / pyqtgraph:
# merely importing those loads Qt's own copies of shared DLLs, and if they load
# first the Ophir DLL's init routine fails (WinError -2147023782). This must come
# before every Qt-pulling import below. See power_meter.preload.
import power_meter
power_meter.preload()

import platform
import ctypes
import ctypes.wintypes
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from avaspec import *
import globals
import form1
import numpy as np
import time
from shutter import (
    ShutterController,
    ShutterError,
    RotationController,
    RotationError,
)
from power_meter import PowerMeterController, PowerMeterError
from simulator import SpectrometerSimulator
from datetime import datetime
from pyqtgraph import PlotDataItem

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class MainWindow(QMainWindow, form1.Ui_MainWindow):
    newdata = pyqtSignal(int, int, int)
    def __init__(self, parent=None):
        QMainWindow.__init__(self, parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setupUi(self)
        self.setWindowTitle("Spectra Explorer")
        self.create_title_bar()
        self.shutter = ShutterController()
        self.rotation = RotationController()
        self.power_meter = PowerMeterController()
        self.simulator = SpectrometerSimulator()
        # Real-device calibration backup, saved/restored by on_simulate_toggled.
        self._real_wavelength = None
        self._real_pixels = None
        self.AvgPresetEdit.setValidator(QIntValidator(1, 32767, self))
        self.AvgPresetEdit.textChanged.connect(self.update_avg_preset_button_text)
        # on_Avg200StartBtn_clicked / on_ShutterBtn_clicked are auto-connected by
        # connectSlotsByName() in setupUi() — connecting them again here would fire
        # the slot twice per click (toggle() would open then immediately close).
        self.update_shutter_button_style()

        self.RotationTarget.setValidator(QDoubleValidator(self))
        self.RotationStep.setValidator(QDoubleValidator(self))
        self.RotationLoopFrequency.setValidator(QDoubleValidator(self))
        self.RotationTargetWavelength.setValidator(QDoubleValidator(self))
        self.RotationTargetIntensity.setValidator(QDoubleValidator(self))
        self.RotationLoopDeadband.setValidator(QDoubleValidator(self))
        self.LatencyWavelength.setValidator(QDoubleValidator(self))
        self.LatencyPercentage.setValidator(QDoubleValidator(0.0, 100.0, 1, self))
        self.InitShutter.clicked.connect(self.on_init_shutter)
        self.InitRotation.clicked.connect(self.on_init_rotation)
        self.MoveRotation.clicked.connect(self.on_move_rotation)
        self.MeasureLatencyBtn.clicked.connect(self.on_measure_latency)
        self.InitialisePowerMeter.clicked.connect(self.on_init_power_meter)
        self.set_power_meter_status("Not connected", "gray")
        self.PowerMeterDisplay.setDigitCount(7)
        self.PowerMeterDisplay.display("----")
        self.PowerMeterUnits.setText("W")
        self.SimulateData.setChecked(getattr(globals, "simulate_data", False))
        self.SimulateData.toggled.connect(self.on_simulate_toggled)
        self.showMaximized()

        # Initialize UI fields
        self.LasernmBtn.setText("1064")
        self.IntTimeEdt.setText("20")
        self.NumAvgEdt.setText("1")
        self.NumTrigMeasEdt.setText("1000")
        self.NumTrigSelEdt.setText("10")
        self.RotationStep.setText("0.5")
        self.RotationLoopFrequency.setText("2")
        self.last_save_path = None
        self.last_save_averaging = 1
        self.top_plots = []  # List to hold top 10 plots and their values
        self.avg_curve = PlotDataItem()
        self.acquisition_mode = "idle"
        self.transitioning = False
        self.avs_cb = None
        self.dark_spectra = None
        self.trigger_total = 0
        self.trigger_selected = 0
        self.trigger_top_values = None
        self.trigger_top_spectra = None
        self.trigger_min_idx = 0
        self.trigger_max_idx = 0
        self.measurement_generation = 0
        self.active_generation = 0
        self.shutter_latency_ms = None
        self._last_rotation_correction = 0.0
        self._drag_pos = None
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(20)
        # Separate, slower timer for the power meter: polling the Ophir COM server
        # at the 50 Hz plot rate would be wasteful. Started on successful init.
        self.power_timer = QTimer(self)
        self.power_timer.timeout.connect(self.update_power_meter)
        self.newdata.connect(self.handle_newdata)
        self.set_acquisition_controls()
        self.on_OpenCommBtn_clicked()

    def closeEvent(self, event):
        """Override the closeEvent to stop the timer, release resources, and quit the app."""
        try:
            # Stop and disconnect timer
            if self.timer.isActive():
                self.timer.stop()
            self.timer.timeout.disconnect()
            
            if globals.dev_handle is not None:
                AVS_StopMeasure(globals.dev_handle)
                AVS_Deactivate(globals.dev_handle)

            try:
                self.shutter.disconnect()
            except Exception as shutter_err:
                print(f"Error disconnecting shutter: {shutter_err}")

            try:
                self.rotation.disconnect()
            except Exception as rotation_err:
                print(f"Error disconnecting rotation stage: {rotation_err}")

            try:
                if self.power_timer.isActive():
                    self.power_timer.stop()
                self.power_meter.disconnect()
            except Exception as power_err:
                print(f"Error disconnecting power meter: {power_err}")

            # Release AvaSpec library
            AVS_Done()
            print("Spectrometer resources released.")
            
            # Disconnect signals
            self.newdata.disconnect()

        except Exception as e:
            print(f"Error during closeEvent cleanup: {e}")

        print("Application is closing...")
        qApp.quit()
        sys.exit(0)
        event.accept()  # Ensure close event is accepted


    def show_info_message(self, message):
        info_label = QLabel(message, self)
        info_label.setStyleSheet("""
            background-color: yellow;
            padding: 0px;
            border-radius: 0px;
            color: black;
        """)
        info_label.setAlignment(Qt.AlignCenter)

        # Align with the plot widget's dimensions and position
        plot_geometry = self.plot.geometry()  # Get plot widget's geometry
        plot_x = plot_geometry.x() + 9
        plot_y = plot_geometry.y()
        plot_width = plot_geometry.width() - 21  # Adjust to ensure no overflow on the right

        info_label_height = 30  # Customize the height of the info label
        x_position = plot_x
        y_position = plot_y - info_label_height + 39  # Position above the plot widget with a 5-pixel gap

        # Set geometry dynamically
        info_label.setGeometry(x_position, y_position, plot_width, info_label_height)
        
        # Show the info label
        info_label.show()

        # Set a timer to hide the label after 3 seconds
        QTimer.singleShot(3000, info_label.hide)
        
    def measure_cb(self, pparam1, pparam2, generation):
        param1 = pparam1[0] # dereference the pointers
        param2 = pparam2[0]
        self.newdata.emit(param1, param2, generation) 

    def save_data(self, file_path):
        # Retrieve wavelength and spectral data
        wavelengths = globals.wavelength
        sample_data = getattr(globals, "averagedspectrum", None)

        # Check if there is valid spectrum data
        if sample_data is None or len(sample_data) == 0:
            QMessageBox.warning(self, "Save Error", "No averaged spectrum available to save!")
            return

        # Handle dark data safely
        dark_data = getattr(globals, "darkspectraldata", None)
        if dark_data is None or len(dark_data) != len(sample_data):
            QMessageBox.warning(self, "Warning", "Dark data missing or invalid! Using zeros instead.")
            dark_data = np.zeros_like(sample_data)

        # Create a reference data array (currently zeros)
        reference_data = np.zeros(len(sample_data))

        # Calculate scope-corrected data safely
        scope_corrected_data = sample_data - dark_data

        # Prepare header
        integration_time = float(self.IntTimeEdt.text())
        averaging_number = getattr(self, "last_save_averaging", None)
        if averaging_number is None:
            averaging_number = int(self.NumAvgEdt.text())
        smoothing_pixels = 3
        spectrometer_name = "1212121U1"  # Example placeholder spectrometer name

        header = (
            f"{int(self.LasernmBtn.text())}\n"
            f"Integration time [ms]: {integration_time}\n"
            f"Averaging Nr. [scans]: {averaging_number}\n"
            f"Smoothing Nr. [pixels]: {smoothing_pixels}\n"
            f"Data measured with spectrometer [name]: {spectrometer_name}\n"
            "Wave   ;Sample   ;Dark     ;Reference;Scope Corrected for Dark\n"
            "[nm]   ;[counts] ;[counts] ;[counts]\n"
        )

        # Format spectral data lines
        data_lines = [
            f"{wave:.2f};{sample:.3f};{dark:.3f};{reference:.3f};{corrected:.5f}"
            for wave, sample, dark, reference, corrected in zip(
                wavelengths, sample_data, dark_data, reference_data, scope_corrected_data
            )
        ]

        # If user-selected filename exists, generate a unique one
        file_path = self.get_unique_filename(file_path)

        # Save to file
        try:
            with open(file_path, 'w') as file:
                file.write(header)
                file.write("\n".join(data_lines))
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save data:\n{e}")

    def get_unique_filename(self, file_path):
        """If the file exists, append _1, _2, etc. until it's unique."""
        base, ext = os.path.splitext(file_path)
        counter = 1
        new_path = file_path
        while os.path.exists(new_path):
            new_path = f"{base}_{counter}{ext}"
            counter += 1
        return new_path


    def configure_measurement(self, integration_time=None, averages=None):
        self.avg_curve.clear()
        ret = AVS_UseHighResAdc(globals.dev_handle, True)
        if ret != 0:
            self.show_info_message(f"High resolution ADC failed ({ret})")
            return None

        if integration_time is None:
            integration_time = float(self.IntTimeEdt.text())
        if averages is None:
            averages = int(self.NumAvgEdt.text())

        measconfig = MeasConfigType()
        measconfig.m_StartPixel = 0
        measconfig.m_StopPixel = globals.pixels - 1
        measconfig.m_IntegrationTime = integration_time
        measconfig.m_IntegrationDelay = 0
        measconfig.m_NrAverages = averages
        #measconfig.m_CorDynDark_m_Enable = self.DarkCorrChk.isChecked()
        #measconfig.m_CorDynDark_m_ForgetPercentage = int(self.DarkCorrPercEdt.text())
        measconfig.m_CorDynDark_m_Enable = 0  # nesting of types does NOT work!!
        measconfig.m_CorDynDark_m_ForgetPercentage = 0
        measconfig.m_Smoothing_m_SmoothPix = 0
        measconfig.m_Smoothing_m_SmoothModel = 0
        measconfig.m_SaturationDetection = 0
        measconfig.m_Trigger_m_Mode = 0
        measconfig.m_Trigger_m_Source = 0
        measconfig.m_Trigger_m_SourceType = 0
        measconfig.m_Control_m_StrobeControl = 0
        measconfig.m_Control_m_LaserDelay = 0
        measconfig.m_Control_m_LaserWidth = 0
        measconfig.m_Control_m_LaserWaveLength = 0.0
        measconfig.m_Control_m_StoreToRam = 0
        ret = AVS_PrepareMeasure(globals.dev_handle, measconfig)
        if ret != 0:
            self.show_info_message(f"Prepare measurement failed ({ret})")
            return None
        return measconfig
    
    def create_title_bar(self):
        """Create a custom title bar with close, minimize, and maximize buttons on the right."""
        self.title_bar = QWidget(self)
        self.title_bar.setStyleSheet("background-color: rgb(35,39,42); color: white;")
        self.title_bar.setFixedHeight(24)
        self.title_bar.mousePressEvent = self.title_bar_mouse_press
        self.title_bar.mouseMoveEvent = self.title_bar_mouse_move
        self.title_bar.mouseDoubleClickEvent = self.title_bar_mouse_double_click

        # Create layout for title bar
        self.title_layout = QHBoxLayout(self.title_bar)
        self.title_layout.setContentsMargins(0, 0, 0, 0)

        # Create a label for the title (optional)
        title_label = QLabel("  Spectra Explorer", self)
        title_label.setStyleSheet("color: white; font-size: 12px;")
        title_label.setFixedHeight(24)
        title_label.mousePressEvent = self.title_bar_mouse_press
        title_label.mouseMoveEvent = self.title_bar_mouse_move
        title_label.mouseDoubleClickEvent = self.title_bar_mouse_double_click

        # Add title label to the layout (left side)
        self.title_layout.addWidget(title_label)

        # Spacer to move buttons to the right
        self.title_layout.addStretch()

        # Create minimize, maximize, and close buttons
        minimize_button = QPushButton(self)
        maximize_button = QPushButton(self)
        close_button = QPushButton(self)

        # Set fixed height and width for buttons
        button_size = QSize(24, 22)

        # Load SVG icons into buttons
        minimize_button.setIcon(QIcon(os.path.join(SCRIPT_DIR, "minimize-2.svg")))
        maximize_button.setIcon(QIcon(os.path.join(SCRIPT_DIR, "maximize.svg")))
        close_button.setIcon(QIcon(os.path.join(SCRIPT_DIR, "x.svg")))

        # Set fixed size for buttons to match the icon size
        minimize_button.setFixedSize(button_size)
        maximize_button.setFixedSize(button_size)
        close_button.setFixedSize(button_size)

        # Set styles for buttons (light gray color, no rounded corners on hover)
        button_style = """
            QPushButton {
                background-color: transparent;
                color: #d3d3d3;  /* Light gray for the button text/icon */
                border: none;
                padding: 0;
                text-align: center;
                font-size: 16px;  /* Optional, but you might want to control the icon size in CSS */
            }
            QPushButton:hover {
                background-color: rgb(50, 60, 70);
                border-radius: 0;  /* Ensure no rounded corners on hover */
            }
            QPushButton:pressed {
                background-color: rgb(90, 100, 110);
            }
            QPushButton:focus {
                outline: none;  /* Remove focus outline for a cleaner look */
            }
        """
        
        close_button.setStyleSheet(button_style + "QPushButton:hover { background-color: red; }")
        minimize_button.setStyleSheet(button_style)
        maximize_button.setStyleSheet(button_style)

        # Connect button signals to window actions
        close_button.clicked.connect(self.close)
        minimize_button.clicked.connect(self.showMinimized)
        maximize_button.clicked.connect(self.toggle_maximize_restore)

        # Add buttons to layout (right side)
        self.title_layout.addWidget(minimize_button)
        self.title_layout.addWidget(maximize_button)
        self.title_layout.addWidget(close_button)

        # Set title bar position at the top
        self.title_bar.setGeometry(0, 0, self.width(), 24)

    def update_shutter_button_style(self):
        if self.shutter.is_closed:
            self.ShutterBtn.setText("Shutter Closed")
            self.ShutterBtn.setStyleSheet("""
                QPushButton {
                    background-color: darkgreen;
                    color: white;
                    border: 1px solid #006400;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #228B22;
                }
            """)
        else:
            self.ShutterBtn.setText("Shutter Open")
            self.ShutterBtn.setStyleSheet("")

    @pyqtSlot()
    def on_ShutterBtn_clicked(self):
        try:
            self.shutter.toggle()
        except ShutterError as e:
            self.show_info_message(str(e))
            return
        except Exception as e:
            self.show_info_message(f"Shutter error: {e}")
            return
        self.update_shutter_button_style()

    def on_init_shutter(self):
        try:
            self.shutter.initialize()
        except ShutterError as e:
            self.show_info_message(str(e))
        except Exception as e:
            self.show_info_message(f"Shutter error: {e}")
        self.update_shutter_button_style()

    def on_init_rotation(self):
        try:
            self.rotation.initialize()
        except RotationError as e:
            self.show_info_message(str(e))
        except Exception as e:
            self.show_info_message(f"Rotation error: {e}")

    def on_move_rotation(self):
        text = self.RotationTarget.text().strip()
        try:
            target = float(text)
        except ValueError:
            self.show_info_message("Enter a rotation target in degrees")
            return
        try:
            self.rotation.move_to(target)
        except RotationError as e:
            self.show_info_message(str(e))
        except Exception as e:
            self.show_info_message(f"Rotation error: {e}")

    def set_power_meter_status(self, text, color="black"):
        self.PowerMeterStatus.setText(text)
        self.PowerMeterStatus.setStyleSheet(f"color: {color};")

    def on_init_power_meter(self):
        if self.power_meter.is_connected:
            # Toggle off: disconnect and stop polling.
            self.power_timer.stop()
            self.power_meter.disconnect()
            self.set_power_meter_status("Not connected", "gray")
            self.PowerMeterDisplay.display("----")
            self.InitialisePowerMeter.setText("Initialise")
            return
        try:
            serial = self.power_meter.initialize()
        except PowerMeterError as e:
            self.set_power_meter_status("Error", "red")
            self.show_info_message(str(e))
            return
        except Exception as e:
            self.set_power_meter_status("Error", "red")
            self.show_info_message(f"Power meter error: {e}")
            return
        self.set_power_meter_status(f"Connected ({serial})", "green")
        self.InitialisePowerMeter.setText("Disconnect")
        self.power_timer.start(200)  # 5 Hz display update

    @staticmethod
    def _scale_power(watts):
        """Scale a watt reading to a (value, unit) pair for display."""
        a = abs(watts)
        if a >= 1.0:
            return watts, "W"
        if a >= 1e-3:
            return watts * 1e3, "mW"
        if a >= 1e-6:
            return watts * 1e6, "µW"
        return watts * 1e9, "nW"

    @pyqtSlot()
    def update_power_meter(self):
        if not self.power_meter.is_connected:
            return
        try:
            watts = self.power_meter.read()
        except PowerMeterError:
            return
        except Exception as e:
            self.power_timer.stop()
            self.power_meter.disconnect()
            self.set_power_meter_status("Error", "red")
            self.PowerMeterDisplay.display("----")
            self.InitialisePowerMeter.setText("Initialise")
            self.show_info_message(f"Power meter error: {e}")
            return
        if watts is None:
            return  # no new sample buffered since last poll
        value, unit = self._scale_power(watts)
        self.PowerMeterDisplay.display(f"{value:.3f}")
        self.PowerMeterUnits.setText(unit)

    def _read_peak_at_wavelength(self, target_nm, window_nm=5.0):
        """Peak counts within ±window_nm of target_nm, or full-spectrum max if wavelength data is unavailable."""
        data = getattr(globals, "spectraldata", None)
        wl = getattr(globals, "wavelength", None)
        if data is None or len(data) == 0:
            return 0.0
        if wl is None or len(wl) == 0:
            return float(np.max(data))
        lo = np.searchsorted(wl, target_nm - window_nm, side="left")
        hi = np.searchsorted(wl, target_nm + window_nm, side="right")
        lo = max(0, lo)
        hi = min(len(data), hi)
        if lo >= hi:
            return float(np.max(data))
        return float(np.max(data[lo:hi]))

    def _run_rotation_logic(self):
        """Bang-bang feedback: nudge the rotation stage (ND filter) to hold the intensity
        at RotationTargetWavelength near RotationTargetIntensity. Runs off the plot timer;
        the RotationLoopFrequency field throttles how often a correction actually fires."""
        if not self.EnableRotationLogic.isChecked():
            return
        # No-op until the stage is homed/initialised (also covers simulate/demo mode).
        if not getattr(self.rotation, "_initialized", False):
            return

        try:
            freq = float(self.RotationLoopFrequency.text())
        except ValueError:
            return
        if freq <= 0:
            return
        if time.perf_counter() - self._last_rotation_correction < 1.0 / freq:
            return

        try:
            target_nm = float(self.RotationTargetWavelength.text())
            target_intensity = float(self.RotationTargetIntensity.text())
            step = float(self.RotationStep.text())
            deadband = float(self.RotationLoopDeadband.text())
        except ValueError:
            return

        current = self._read_peak_at_wavelength(target_nm)
        if current <= 0:
            return

        if current < target_intensity - deadband:
            sign = 1   # too dim
        elif current > target_intensity + deadband:
            sign = -1  # too bright
        else:
            return     # within deadband — hold
        if self.ReverseRotation.isChecked():
            sign = -sign

        try:
            new_angle = self.rotation.get_angle() + sign * step
            self.rotation.move_to(new_angle)
        except RotationError as e:
            self.EnableRotationLogic.setChecked(False)
            self.show_info_message(str(e))
            return
        except Exception as e:
            self.EnableRotationLogic.setChecked(False)
            self.show_info_message(f"Rotation error: {e}")
            return

        self._last_rotation_correction = time.perf_counter()

    def on_measure_latency(self):
        try:
            target_nm = float(self.LatencyWavelength.text())
        except ValueError:
            self.show_info_message("Enter a valid wavelength for latency measurement")
            return
        try:
            pct = float(self.LatencyPercentage.text())
            if not (0.0 < pct < 100.0):
                raise ValueError
        except ValueError:
            self.show_info_message("Enter a drop percentage between 0 and 100")
            return

        original = self._read_peak_at_wavelength(target_nm)
        if original <= 0:
            self.shutter_latency_ms = None
            self.LatencyResultLabel.setText("-- ms")
            self.show_info_message("No signal to measure latency against")
            return
        threshold = (pct / 100.0) * original

        try:
            self.shutter.close()
        except ShutterError as e:
            self.shutter_latency_ms = None
            self.LatencyResultLabel.setText("-- ms")
            self.show_info_message(str(e))
            return
        except Exception as e:
            self.shutter_latency_ms = None
            self.LatencyResultLabel.setText("-- ms")
            self.show_info_message(f"Shutter error: {e}")
            return

        start = time.perf_counter()
        self.update_shutter_button_style()

        timeout = 5.0
        elapsed_ms = None
        while time.perf_counter() - start < timeout:
            QApplication.processEvents()
            if self._read_peak_at_wavelength(target_nm) <= threshold:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                break
            time.sleep(0.002)

        if elapsed_ms is None:
            self.shutter_latency_ms = None
            self.LatencyResultLabel.setText("timeout")
            self.show_info_message("Latency measurement timed out")
            return
        self.shutter_latency_ms = elapsed_ms
        self.LatencyResultLabel.setText(f"{elapsed_ms:.1f} ms")

    def on_simulate_toggled(self, checked):
        # The simulator clobbers the shared globals.wavelength / globals.pixels
        # (see update_plot). Back up the real device's calibration on the way in
        # and restore it on the way out, otherwise the real spectrometer data
        # keeps being plotted against the simulator's wavelength axis and appears
        # shifted to lower wavelengths.
        if checked:
            self._real_wavelength = globals.wavelength
            self._real_pixels = globals.pixels
        else:
            if getattr(self, "_real_wavelength", None) is not None:
                globals.wavelength = self._real_wavelength
                globals.pixels = self._real_pixels
        globals.simulate_data = checked

    def update_avg_preset_button_text(self):
        averages = self.get_avg_preset_value(show_error=False)
        if averages is None:
            self.Avg200StartBtn.setText("Avg Start")
        else:
            self.Avg200StartBtn.setText(f"{averages} Avg Start")

    def get_avg_preset_value(self, show_error=True):
        try:
            averages = int(self.AvgPresetEdit.text())
        except ValueError:
            if show_error:
                self.show_info_message("Preset average must be numeric")
            return None

        if averages < 1:
            if show_error:
                self.show_info_message("Preset average must be at least 1")
            return None

        return averages

    def resizeEvent(self, event):
        super(MainWindow, self).resizeEvent(event)
        if hasattr(self, "title_bar"):
            self.title_bar.setGeometry(0, 0, self.width(), 24)

    def toggle_maximize_restore(self):
        """Toggle between maximized and normal window states."""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def mousePressEvent(self, event):
        super(MainWindow, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super(MainWindow, self).mouseMoveEvent(event)

    def title_bar_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def title_bar_mouse_move(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            if self.isMaximized():
                normal_width = max(self.normalGeometry().width(), 800)
                x_ratio = event.x() / max(self.title_bar.width(), 1)
                self.showNormal()
                self._drag_pos = QPoint(int(normal_width * x_ratio), event.y())
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def title_bar_mouse_double_click(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximize_restore()
            event.accept()

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and eventType == "windows_generic_MSG" and not self.isMaximized():
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                border = 6
                pos = self.mapFromGlobal(QCursor.pos())
                x, y = pos.x(), pos.y()
                width, height = self.width(), self.height()

                left = x <= border
                right = x >= width - border
                top = y <= border
                bottom = y >= height - border

                if top and left:
                    return True, 13  # HTTOPLEFT
                if top and right:
                    return True, 14  # HTTOPRIGHT
                if bottom and left:
                    return True, 16  # HTBOTTOMLEFT
                if bottom and right:
                    return True, 17  # HTBOTTOMRIGHT
                if left:
                    return True, 10  # HTLEFT
                if right:
                    return True, 11  # HTRIGHT
                if top:
                    return True, 12  # HTTOP
                if bottom:
                    return True, 15  # HTBOTTOM

        return super(MainWindow, self).nativeEvent(eventType, message)

    def set_acquisition_controls(self):
        finite_running = self.acquisition_mode in ("dark", "trigger")
        self.StartMeasBtn.setEnabled(not finite_running)
        self.TrigSaveBtn.setEnabled(not finite_running)
        self.Avg200StartBtn.setEnabled(not finite_running)
        self.AvgPresetEdit.setEnabled(not finite_running)
        self.SaveDarkBtn.setEnabled(not finite_running)
        self.StopMeasBtn.setEnabled(self.acquisition_mode != "idle")

        for field in (self.IntTimeEdt, self.NumAvgEdt, self.NumTrigMeasEdt, self.NumTrigSelEdt):
            field.setEnabled(not finite_running)

    def validate_measurement_inputs(self, require_trigger=False, require_selected=False):
        try:
            integration_time = float(self.IntTimeEdt.text())
            averages = int(self.NumAvgEdt.text())
        except ValueError:
            self.show_info_message("Integration time and averages must be numeric")
            return None

        if integration_time <= 0:
            self.show_info_message("Integration time must be positive")
            return None
        if averages < 1:
            self.show_info_message("Number of averages must be at least 1")
            return None

        trigger_total = 0
        trigger_selected = 0
        if not require_trigger:
            return integration_time, averages, trigger_total, trigger_selected

        try:
            trigger_total = int(self.NumTrigMeasEdt.text())
        except ValueError:
            self.show_info_message("Trigger parameters must be numeric")
            return None

        if trigger_total < 1:
            self.show_info_message("Trigger samples must be at least 1")
            return None
        if trigger_total > 32767:
            self.show_info_message("Trigger samples must be 32767 or less")
            return None
        if require_selected:
            try:
                trigger_selected = int(self.NumTrigSelEdt.text())
            except ValueError:
                self.show_info_message("Selected samples must be numeric")
                return None
            if trigger_selected < 1 or trigger_selected > trigger_total:
                self.show_info_message("Selected samples must be between 1 and total samples")
                return None

        return integration_time, averages, trigger_total, trigger_selected

    def validate_integration_time(self):
        try:
            integration_time = float(self.IntTimeEdt.text())
        except ValueError:
            self.show_info_message("Integration time must be numeric")
            return None

        if integration_time <= 0:
            self.show_info_message("Integration time must be positive")
            return None

        return integration_time

    def stop_current_measurement(self):
        if globals.dev_handle is None:
            self.acquisition_mode = "idle"
            self.avs_cb = None
            self.set_acquisition_controls()
            return True

        ret = AVS_StopMeasure(globals.dev_handle)
        if ret != 0:
            print("AVS_StopMeasure returned:", ret)

        self.measurement_generation += 1
        self.active_generation = self.measurement_generation
        self.acquisition_mode = "idle"
        self.avs_cb = None
        return ret == 0

    def start_callback_measurement(self, mode, nummeas, integration_time, averages):
        if self.transitioning:
            return False
        if globals.dev_handle is None:
            self.show_info_message("No spectrometer connected")
            return False

        self.transitioning = True
        try:
            if self.acquisition_mode != "idle":
                if not self.stop_current_measurement():
                    self.show_info_message("Could not stop current measurement")
                    self.set_acquisition_controls()
                    return False

            if self.configure_measurement(integration_time, averages) is None:
                self.acquisition_mode = "idle"
                self.set_acquisition_controls()
                return False

            globals.NrScanned = 0
            self.acquisition_mode = mode
            self.measurement_generation += 1
            self.active_generation = self.measurement_generation
            generation = self.active_generation
            self.avs_cb = AVS_MeasureCallbackFunc(
                lambda pparam1, pparam2, generation=generation: self.measure_cb(pparam1, pparam2, generation)
            )
            ret = AVS_MeasureCallback(globals.dev_handle, self.avs_cb, nummeas)
            print("AVS_MeasureCallback returned:", ret)
            if ret != 0:
                self.acquisition_mode = "idle"
                self.avs_cb = None
                self.show_info_message(f"Start measurement failed ({ret})")
                return False

            self.set_acquisition_controls()
            return True
        finally:
            self.transitioning = False

    def restart_live_measurement(self):
        params = self.validate_measurement_inputs(require_trigger=False)
        if params is None:
            self.acquisition_mode = "idle"
            self.set_acquisition_controls()
            return

        integration_time, averages, _, _ = params
        self.plot.progress_bar.setValue(0)
        self.start_callback_measurement("live", -1, integration_time, averages)

    def abort_finite_measurement(self):
        previous_mode = self.acquisition_mode
        self.stop_current_measurement()
        self.dark_spectra = None
        self.trigger_top_values = None
        self.trigger_top_spectra = None
        self.top_plots = []
        self.plot.progress_bar.setValue(0)
        self.set_acquisition_controls()
        if previous_mode in ("dark", "trigger"):
            QTimer.singleShot(100, self.restart_live_measurement)

    @pyqtSlot()
    def on_SaveDarkBtn_clicked(self):
        params = self.validate_measurement_inputs(require_trigger=True)
        if params is None:
            return

        integration_time, averages, nummeas, _ = params
        self.dark_spectra = np.zeros((nummeas, globals.pixels))
        self.plot.progress_bar.setValue(0)
        self.plot.progress_bar.setMaximum(nummeas)
        globals.savedark = False
        self.start_callback_measurement("dark", nummeas, integration_time, averages)

    @pyqtSlot()
#   if you leave out the @pyqtSlot() line, you will also get an extra signal!
#   so you might even get three!
    def on_OpenCommBtn_clicked(self):
        ret = AVS_Init(0)    
        print("AVS_Init returned:", ret)
        # QMessageBox.information(self,"Info","AVS_Init returned:  {0:d}".format(ret))
        ret = AVS_GetNrOfDevices()
        print("Number of devices found:", ret)
        # QMessageBox.information(self,"Info","AVS_GetNrOfDevices returned:  {0:d}".format(ret))
        if (ret > 0):
            mylist = AvsIdentityType * 1
            mylist = AVS_GetList(1)
            serienummer = str(mylist[0].SerialNumber.decode("utf-8"))
            #QMessageBox.information(self,"Info","Found Serialnumber: " + serienummer)
            globals.dev_handle = AVS_Activate(mylist[0])
            # QMessageBox.information(self,"Info","AVS_Activate returned:  {0:d}".format(globals.dev_handle))
            devcon = DeviceConfigType()
            devcon = AVS_GetParameter(globals.dev_handle, 63484)
            globals.pixels = devcon.m_Detector_m_NrPixels
            globals.wavelength = AVS_GetLambda(globals.dev_handle)
            #self.on_StartMeasBtn_clicked()

            print("Device handle:", globals.dev_handle)
            print("Pixels:", globals.pixels)

            #print("About to start measurement...")
            QTimer.singleShot(100, self.on_StartMeasBtn_clicked)
            print("Measurement start function called.")
        else:
            self.show_info_message("No devices were found!")
        return

    @pyqtSlot()
    def on_CloseCommBtn_clicked(self):
        # nothing for now
        return

    @pyqtSlot()
    def on_StartMeasBtn_clicked(self):
        self.restart_live_measurement()

    @pyqtSlot()
    def on_StopMeasBtn_clicked(self):
        if self.acquisition_mode in ("dark", "trigger"):
            self.abort_finite_measurement()
        else:
            self.stop_current_measurement()
            self.plot.progress_bar.setValue(0)
            self.set_acquisition_controls()

    @pyqtSlot()
    def on_TrigSaveBtn_clicked(self):
        if globals.savedark == False:
            self.show_info_message("No Dark")

        params = self.validate_measurement_inputs(require_trigger=True, require_selected=True)
        if params is None:
            return

        integration_time, averages, nummeas, numsel = params
        self.start_trigger_with_shutter_logic(integration_time, averages, nummeas, numsel)

    @pyqtSlot()
    def on_Avg200StartBtn_clicked(self):
        if globals.savedark == False:
            self.show_info_message("No Dark")

        integration_time = self.validate_integration_time()
        if integration_time is None:
            return

        averages = self.get_avg_preset_value()
        if averages is None:
            return

        self.start_trigger_with_shutter_logic(integration_time, averages, 1, 1)

    def start_trigger_with_shutter_logic(self, integration_time, averages, nummeas, numsel):
        """When shutter logic is enabled, close the shutter first and only start the
        measurement after the measured shutter latency has elapsed; otherwise start now."""
        if not self.EnableShutterLogic.isChecked():
            self.start_trigger_measurement(integration_time, averages, nummeas, numsel)
            return

        delay_ms = self.shutter_latency_ms
        if delay_ms is None:
            self.show_info_message("Measure shutter latency before enabling shutter logic")
            return

        try:
            self.shutter.close()
        except ShutterError as e:
            self.show_info_message(str(e))
            return
        except Exception as e:
            self.show_info_message(f"Shutter error: {e}")
            return
        self.update_shutter_button_style()

        QTimer.singleShot(
            int(round(delay_ms)),
            lambda: self.start_trigger_measurement(integration_time, averages, nummeas, numsel),
        )

    def start_trigger_measurement(self, integration_time, averages, nummeas, numsel):
        self.last_save_averaging = averages
        self.plot.progress_bar.setValue(0)
        self.plot.progress_bar.setMaximum(nummeas)

        self.top_plots = []  # Reset before starting
        thrxmin, thrxmax = self.plot.get_threshold_value() - self.plot.get_margin_value(), self.plot.get_threshold_value() + self.plot.get_margin_value()
        thrxmin_idx = np.searchsorted(globals.wavelength, thrxmin, side='left')
        thrxmax_idx = np.searchsorted(globals.wavelength, thrxmax, side='right')
        thrxmin_idx = max(0, min(thrxmin_idx, globals.pixels - 1))
        thrxmax_idx = max(thrxmin_idx + 1, min(thrxmax_idx, globals.pixels))
        #thrxmin_idx = min(range(len(globals.wavelength)), key=lambda i: abs(globals.wavelength[i] - thrxmin))
        #thrxmax_idx = min(range(len(globals.wavelength)), key=lambda i: abs(globals.wavelength[i] - thrxmax))
    
        self.trigger_total = nummeas
        self.trigger_selected = numsel
        self.trigger_min_idx = thrxmin_idx
        self.trigger_max_idx = thrxmax_idx
        self.trigger_top_values = np.full(numsel, -np.inf)
        self.trigger_top_spectra = np.zeros((numsel, globals.pixels))
        self.start_callback_measurement("trigger", nummeas, integration_time, averages)
            
        ''' ####Return If code from if current_value > top_values.min(): does not work.####
            if len(self.top_plots) < numsel:
                # Save the plot if less than 10 plots are saved
                self.save_plot(current_value)

            else:
                # Replace the lowest value plot if the current value is higher
                min_value = min(self.top_plots, key=lambda x: x[0])[0]
                #print(min_value)
                if current_value > min_value:
                    self.top_plots = [plot for plot in self.top_plots if plot[0] != min_value]
                    self.save_plot(current_value)
                #print(self.top_plots)
        self.average_top_plots()  # Call this after saving the top plots
        print(self.top_plots[0][0])
        save_path = QFileDialog.getSaveFileName(self, 'Save Data', '1064', 'Text Files (*.txt);;All Files (*)')[0]
        if save_path:
            # Save the data to the specified file path
            self.save_data(save_path)
        return'''
    
    def save_plot(self, current_value):
        # Render and save the current plot
        self.top_plots.append((current_value, globals.spectraldata[:globals.pixels]))
        self.top_plots.sort(reverse=True, key=lambda x: x[0])  # Sort plots by value

        # Create a new plot curve in green for the saved plot, viskas veikia gerai
        # curve = self.plot.plot_widget.plot(globals.wavelength[:globals.pixels], globals.spectraldata[:globals.pixels], pen='g')
    
    def average_top_plots(self):
        # Extract spectral data from top_plots and average them
        if not self.top_plots:
            print("No plots to average.")
            return
        
        # Get only the spectral data from top_plots
        spectral_data = np.array([plot[1] for plot in self.top_plots])
        
        # Compute the average of the spectral data
        globals.averagedspectrum = np.mean(spectral_data, axis=0)
        
        # Plot or save the averaged spectrum as needed
        self.avg_curve = self.plot.plot_widget.plot(globals.wavelength[:globals.pixels], globals.averagedspectrum)
        # self.plot.curve.setData(globals.wavelength[:globals.pixels], globals.averagedspectrum)
        
        # Optionally save the averaged data

        initial_dir = os.path.dirname(self.last_save_path) if self.last_save_path else SCRIPT_DIR
        averaging_number = getattr(self, "last_save_averaging", None)
        if averaging_number is None:
            averaging_number = int(self.NumAvgEdt.text())
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            'Save Averaged Data',
            os.path.join(initial_dir, 
            f'{int(self.LasernmBtn.text())} nm integr {float(self.IntTimeEdt.text())} ms av{averaging_number} ps {datetime.today().strftime("%Y-%m-%d")}.txt'), 
            'Text Files (*.txt);;All Files (*)'
        )
        if not save_path:  # If user cancels the dialog
            return

        # Update the last save path
        self.last_save_path = save_path

        # Call save_data with the chosen path
        self.save_data(self.last_save_path)


    @pyqtSlot()
    def update_plot(self):
        # print(globals.NrScanned)
        if globals.simulate_data:
            # Feed the scope from the simulator: a 532 nm Gaussian that decays
            # once the shutter closes. Mirrors what the real callback writes.
            self.simulator.set_closed(self.shutter.is_closed)
            globals.wavelength = self.simulator.wavelength
            globals.pixels = self.simulator.pixels
            globals.spectraldata = self.simulator.spectrum()
        self.plot.update_plot()
        self._run_rotation_logic()
        return

    @pyqtSlot(int, int, int)
    def handle_newdata(self, param1, param2, generation):
        if generation != self.active_generation or self.acquisition_mode == "idle":
            return

        if param2 < 0:
            self.show_info_message(f"Measurement callback error ({param2})")
            self.abort_finite_measurement()
            return

        try:
            ret = AVS_GetScopeData(globals.dev_handle)
            spectrum = np.array(ret[1][:globals.pixels])
        except Exception as e:
            print("AVS_GetScopeData failed:", e)
            self.show_info_message("Could not read spectrum")
            self.abort_finite_measurement()
            return

        globals.NrScanned += 1
        globals.spectraldata = spectrum

        if self.acquisition_mode == "live":
            return

        self.plot.progress_bar.setValue(globals.NrScanned)

        if self.acquisition_mode == "dark":
            self.process_dark_spectrum(spectrum)
        elif self.acquisition_mode == "trigger":
            self.process_trigger_spectrum(spectrum)

    def process_dark_spectrum(self, spectrum):
        if self.dark_spectra is None:
            return

        scan_index = globals.NrScanned - 1
        if 0 <= scan_index < len(self.dark_spectra):
            self.dark_spectra[scan_index, :] = spectrum

        if globals.NrScanned < len(self.dark_spectra):
            return

        globals.darkspectraldata = np.mean(self.dark_spectra, axis=0)
        globals.savedark = True
        self.dark_spectra = None
        self.stop_current_measurement()
        self.avg_curve = self.plot.plot_widget.plot(globals.wavelength[:globals.pixels], globals.darkspectraldata)
        self.SaveDarkBtn.setStyleSheet("""
            QPushButton {
                background-color: darkgreen;
                color: white;
                border: 1px solid #006400; /* Optional darker green border */
                border-radius: 5px;       /* Rounded corners */
            }
            QPushButton:hover {
                background-color: #228B22; /* Lighter green on hover */
            }
        """)
        self.set_acquisition_controls()
        QTimer.singleShot(100, self.restart_live_measurement)

    def process_trigger_spectrum(self, spectrum):
        if self.trigger_top_values is None or self.trigger_top_spectra is None:
            return

        current_value = np.max(spectrum[self.trigger_min_idx:self.trigger_max_idx])
        if current_value > self.trigger_top_values.min():
            min_idx = np.argmin(self.trigger_top_values)
            self.trigger_top_values[min_idx] = current_value
            self.trigger_top_spectra[min_idx, :] = spectrum

        if globals.NrScanned < self.trigger_total:
            return

        sorted_indices = np.argsort(-self.trigger_top_values)
        self.top_plots = [(self.trigger_top_values[i], self.trigger_top_spectra[i, :]) for i in sorted_indices]
        self.trigger_top_values = None
        self.trigger_top_spectra = None
        self.stop_current_measurement()
        self.set_acquisition_controls()
        self.average_top_plots()
        QTimer.singleShot(100, self.restart_live_measurement)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet("QWidget{font-size:10px}")
    app.setApplicationName("PyQt5 Spectrometer")
    # Connect lastWindowClosed signal to quit the application
    # app.lastWindowClosed.connect(app.quit)

    form = MainWindow()
    form.show()
    
    try:
        sys.exit(app.exec_())
    except Exception as e:
        print("Unhandled exception in main event loop:", e)
    finally:
        # Cleanup code
        if globals.dev_handle is not None:
            try:
                AVS_Deactivate(globals.dev_handle)
            except Exception as e:
                print(f"Error during cleanup: {e}")

if __name__ == "__main__":
    main()
