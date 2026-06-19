"""Shutter control on a Standa 8SMC-based axis via libximc.

The shutter is operated by sending continuous left/right commands —
`command_right()` closes the shutter, `command_left()` opens it.
The class hides device discovery, lazy connection, and safe teardown
behind a tiny API used by the UI: open(), close(), toggle(), is_closed.
"""

import libximc.highlevel as ximc


SHUTTER_AXIS_INDEX = 1


class ShutterError(RuntimeError):
    pass


class ShutterController:
    def __init__(self, axis_index=SHUTTER_AXIS_INDEX):
        self._axis_index = axis_index
        self._axis = None
        self._closed = False

    def connect(self):
        if self._axis is not None:
            return

        enum_flags = (
            ximc.EnumerateFlags.ENUMERATE_PROBE
            | ximc.EnumerateFlags.ENUMERATE_NETWORK
        )
        devices = ximc.enumerate_devices(enum_flags, "addr=")
        if len(devices) <= self._axis_index:
            raise ShutterError(
                f"Shutter axis {self._axis_index} not found "
                f"(only {len(devices)} device(s) detected)"
            )

        uri = devices[self._axis_index]["uri"]
        axis = ximc.Axis(uri)
        axis.open_device()
        self._axis = axis

    def disconnect(self):
        if self._axis is None:
            return
        try:
            self._axis.close_device()
        finally:
            self._axis = None

    @property
    def is_connected(self):
        return self._axis is not None

    @property
    def is_closed(self):
        return self._closed

    def close(self):
        self._ensure_connected()
        self._axis.command_right()
        self._closed = True

    def open(self):
        self._ensure_connected()
        self._axis.command_left()
        self._closed = False

    def toggle(self):
        if self._closed:
            self.open()
        else:
            self.close()

    def _ensure_connected(self):
        if self._axis is None:
            self.connect()
