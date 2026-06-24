# Python QT app to controll an experiment setup

Written by Ignas with my additions to control shutters and in the future rotational stage, power meter.

# Usage

Run with `python spectrometer_program.pyw`

# Dependencies

`pip install`:

- `PyQT5`
- `pyqtgraph`
- `libximc` (for standa controller)

avaspec library (`avaspecx64.dll` for windows included, linux and mac support posible, but needs seperate drivers - talk to avaspec support :) )

# Development

Converting QT Designer `*.ui` file to usable `*.py` file (run at root of project):

`python -m PyQt5.uic.pyuic -x QT_design/form1.ui -o form1.py`