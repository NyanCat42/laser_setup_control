# Python QT app to controll an experiment setup

Written by Ignas with my additions to control Standa shutter and rotational stage.

The app acquires and averages spectra from an Avantes (AvaSpec) spectrometer while
driving a Standa 8SMC shutter and rotation stage. The experiment: closing the
shutter on an optically-poled waveguide generates a second harmonic at 532 nm, and
the goal is to measure that peak right after the shutter closes.

# Functionality

- **Live spectrum view** — continuous acquisition plotted in real time (pyqtgraph),
  with configurable integration time and per-scan averaging.
- **Dark measurement** — averages N scans into a stored dark spectrum that is
  subtracted from saved data.
- **Triggered acquisition** — collects N spectra, keeps the top-N highest peaks
  within a draggable threshold/margin window around the target wavelength, averages
  them, and prompts to save. A one-click averaging preset (e.g. "200 Avg Start") is
  also available.
- **Shutter control** — open/close the Standa shutter from the toolbar, plus an
  init/homing button.
- **Shutter-latency tool** — measures how long after closing the shutter the signal
  at a chosen wavelength drops by a set percentage; the result delays the start of
  triggered acquisition so it begins right as the shutter closes.
- **Rotation-stage (ND-filter) feedback** — a bang-bang control loop nudges the
  rotation stage to hold the intensity at a target wavelength near a target value,
  stabilising the unstable 1064 nm first harmonic. Configurable step, deadband,
  loop frequency, and direction.
- **Data saving** — exports wavelength / sample / dark / reference / scope-corrected
  columns to a text file with an experiment header.

# Usage

Run with `python spectrometer_program.pyw`

# Dependencies

`pip install`:

- `PyQT5`
- `pyqtgraph`
- `libximc` (for standa controller)

avaspec library (`avaspecx64.dll` for windows included, linux and mac support posible, but needs seperate drivers - talk to avaspec support :). For testing the program works without the library)

# Development

Converting QT Designer `*.ui` file to usable `*.py` file (run at root of project):

`pyuic5 QT_design/form1.ui -o form1.py`

On Windows try:

`python -m PyQt5.uic.pyuic -x QT_design/form1.ui -o form1.py`