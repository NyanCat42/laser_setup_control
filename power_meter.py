"""Ophir power-meter control (PD300-1W on a Juno USB interface) via the Ophir COM server.

Mirrors the shutter/rotation controller pattern: a small wrapper with
``initialize`` / ``read`` / ``disconnect`` and a dedicated exception, so the GUI
can drive it without touching COM directly.

IMPORTANT - why comtypes EARLY binding (see test_juno.py for the full story):
  the Ophir x64 COM server's IDispatch path is broken on this StarLab build -
  every late-bound call returns "Library not registered". ``GetModule(dll)`` +
  ``CreateObject(..., interface=ICoLMMeasurement2)`` calls the dual interface
  directly through the vtable and works.

Close StarLab before connecting - the Juno can be open in only one program at a
time.
"""

import os
import sys

# Default StarLab COM DLL location; adjust if StarLab is installed elsewhere.
DLL = r"C:\Program Files\Ophir Optronics\StarLab 4.00\COM x64\OphirLMMeasurement.dll"
CHANNEL = 0  # Juno is single-channel


class PowerMeterError(RuntimeError):
    """Raised when the power meter cannot be initialised or read."""


# Holds a throwaway COM object so the Ophir DLL stays resident once preloaded.
_warm_object = None


def preload(dll_path=DLL):
    """Force the Ophir COM DLL (and its dependency chain) into the process early.

    MUST be called before Qt is initialised. Qt loads its own copies of some
    shared DLLs; if those load first, the Ophir DLL's init routine loses the
    dependency resolution and CreateObject fails with WinError -2147023782
    ("DLL initialization routine failed"). Loading the Ophir server first makes
    later CreateObject calls succeed even after Qt is up.

    Returns True on success. Never raises - if comtypes/StarLab/the DLL is
    missing the app must still launch (the Initialise button will then report the
    real error). Keeps one COM object alive so the DLL is not unloaded.
    """
    global _warm_object
    # The Ophir server is apartment-threaded; set before comtypes initialises COM.
    sys.coinit_flags = 2  # COINIT_APARTMENTTHREADED (STA)
    try:
        import comtypes.client as cc
    except ImportError:
        return False
    if not os.path.exists(dll_path):
        return False
    try:
        mod = cc.GetModule(dll_path)
        _warm_object = cc.CreateObject(
            mod.CoLMMeasurement, interface=mod.ICoLMMeasurement2
        )
        return True
    except Exception:
        return False


class PowerMeterController:
    """Wraps the Ophir COM server for a single Juno + PD300 head."""

    def __init__(self, dll_path=DLL, channel=CHANNEL):
        self.dll_path = dll_path
        self.channel = channel
        self.serial = None
        self._ophir = None
        self._handle = None
        self._streaming = False
        self._initialized = False

    @property
    def is_connected(self):
        return self._initialized

    def initialize(self):
        """Open the Juno, verify the sensor, and start streaming. Returns the serial."""
        # The Ophir server is apartment-threaded; this must be set before comtypes
        # first initialises COM. Qt already runs the main thread as STA, so this is
        # consistent. Imported lazily so the app still launches without comtypes.
        sys.coinit_flags = 2  # COINIT_APARTMENTTHREADED (STA)
        try:
            import comtypes.client as cc
        except ImportError as e:
            raise PowerMeterError(
                "comtypes not installed. Run: pip install comtypes"
            ) from e

        if not os.path.exists(self.dll_path):
            raise PowerMeterError(
                "Ophir COM DLL not found - is StarLab installed?\n" + self.dll_path
            )

        try:
            mod = cc.GetModule(self.dll_path)
            ophir = cc.CreateObject(
                mod.CoLMMeasurement, interface=mod.ICoLMMeasurement2
            )
        except Exception as e:
            raise PowerMeterError(f"Could not load Ophir COM server: {e}") from e

        serials = list(ophir.ScanUSB())
        if not serials:
            raise PowerMeterError(
                "No Ophir USB device found. Is the Juno plugged in and StarLab closed?"
            )

        handle = ophir.OpenUSBDevice(serials[0])
        if not ophir.IsSensorExists(handle, self.channel):
            try:
                ophir.Close(handle)
            except Exception:
                pass
            raise PowerMeterError(
                f"No sensor on channel {self.channel}. "
                "Is the PD300 plugged into the Juno?"
            )

        ophir.StartStream(handle, self.channel)

        self._ophir = ophir
        self._handle = handle
        self.serial = str(serials[0])
        self._streaming = True
        self._initialized = True
        return self.serial

    def read(self):
        """Most recent power sample in watts, or None if no new data is buffered."""
        if not self._initialized:
            raise PowerMeterError("Power meter not initialised")
        data, _timestamps, _statuses = self._ophir.GetData(self._handle, self.channel)
        if not data:
            return None
        return float(data[-1])

    def disconnect(self):
        """Stop streaming and release the device. Safe to call when not connected."""
        if self._ophir is not None and self._handle is not None:
            if self._streaming:
                try:
                    self._ophir.StopStream(self._handle, self.channel)
                except Exception:
                    pass
            try:
                self._ophir.Close(self._handle)
            except Exception:
                pass
        self._ophir = None
        self._handle = None
        self._streaming = False
        self._initialized = False
