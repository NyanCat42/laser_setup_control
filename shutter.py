"""Shutter and rotation-stage control on Standa 8SMC axes via libximc.

Two stages share a controller chain:

* the shutter on AXIS 1 (Standa 8MDS-70, a DC mirror mount driven to its
  limit switches), operated with continuous left/right commands -
  ``command_right()`` closes the shutter, ``command_left()`` opens it;
* the rotation stage on AXIS 2 (Standa 8MR151-1, a stepper rotation mount).

Neither stage is allowed to move until its settings profile has been applied
and it has been homed via :meth:`initialize`. The profiles live in
``standa_python_profiles/`` and are the low-level (XILab-generated) form, so
they are executed against ``libximc.lowlevel`` and applied to the same device
handle that the high-level :class:`~libximc.highlevel.Axis` opened.
"""

import os
import sys

import libximc.highlevel as ximc
import libximc.lowlevel as _ll
from ctypes import byref


# When True, connection and profile application print detailed diagnostics
# (enumeration results, the device actually opened, and per-setting profile
# results). Set to False once the stages are working to quiet the console.
DEBUG = True


def _debug(message):
    if DEBUG:
        print(f"[standa] {message}", file=sys.stderr, flush=True)


# Each Standa controller has a unique, stable serial number. Matching by serial
# is the only reliable way to tell the shutter from the rotation stage: the
# order in which libximc enumerates USB controllers is not guaranteed and does
# not necessarily match the physical "AXIS 1 / AXIS 2" labels. Run
# `python shutter.py` (with XiLab closed) to list the serial of every connected
# controller, then fill these in. Left as None, the code falls back to the
# positional enumeration order below.
# Identified from the controllers' configured motor type (run `python shutter.py`
# to re-check): serial 39575 runs as a DC motor (the 8MDS-70 shutter), serial
# 39563 runs as a stepper (the 8MR151-1 rotation stage).
SHUTTER_SERIAL = 39575
ROTATION_SERIAL = 39563

# Fallback positions in the enumeration list, used only when the serial above is
# None. With just the two controllers connected they enumerate as 0 and 1.
SHUTTER_AXIS_INDEX = 0
ROTATION_AXIS_INDEX = 1

# PROBE opens each controller to read its identity (serial/name); it is required
# for serial matching. A controller that is busy (e.g. still open in XiLab) will
# not be enumerated.
_ENUM_FLAGS = (
    ximc.EnumerateFlags.ENUMERATE_PROBE
    | ximc.EnumerateFlags.ENUMERATE_NETWORK
)

PROFILES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "standa_python_profiles"
)

# Degrees of stage rotation per motor full step for the 8MR151-1 (0.01 deg
# full-step resolution). Combined with the microstep mode from the profile this
# lets the library report/accept positions in degrees. Adjust if a different
# rotation mount is fitted.
ROTATION_DEG_PER_STEP = 0.01


class ShutterError(RuntimeError):
    pass


class RotationError(RuntimeError):
    pass


def _load_profile(filename, func_name):
    """Return the profile-applying function from ``standa_python_profiles``.

    The profile files reference the settings structs, ``Result`` and ``byref``
    as free globals and define a ``set_profile_*(lib, id)`` function. We exec
    them in a namespace populated from ``libximc.lowlevel`` so that function can
    be applied to an opened device.
    """
    namespace = {name: getattr(_ll, name) for name in dir(_ll) if not name.startswith("__")}
    namespace["byref"] = byref
    path = os.path.join(PROFILES_DIR, filename)
    with open(path) as handle:
        exec(compile(handle.read(), filename, "exec"), namespace)
    return namespace[func_name]


_RESULT_NAMES = {
    getattr(_ll.Result, name): name
    for name in dir(_ll.Result)
    if not name.startswith("_")
}


def _result_name(result):
    return _RESULT_NAMES.get(result, str(result))


class _ProfileLib:
    """Proxy around ``libximc.lowlevel.lib`` used while applying a profile.

    The profile calls ``lib.set_<thing>(id, byref(struct))`` for ~35 settings and
    only reports the worst result. Wrapping the lib lets us:

    * log exactly which setting the controller rejects (when DEBUG is on), and
    * fix up ``engine_settings`` before it is sent: some controllers reject
      ``MicrostepMode = 0`` even for DC motors, and the 8MDS-70 profile ships
      ``MicrostepMode = 0``. ``min_microstep`` raises it to a valid value while
      leaving every other profile value untouched.
    """

    def __init__(self, lib, min_microstep=None):
        self._lib = lib
        self._min_microstep = min_microstep

    def __getattr__(self, name):
        attr = getattr(self._lib, name)
        if not name.startswith("set_"):
            return attr

        def wrapper(*args, **kwargs):
            if (
                name == "set_engine_settings"
                and self._min_microstep is not None
                and len(args) >= 2
            ):
                settings = getattr(args[1], "_obj", None)
                if settings is not None and settings.MicrostepMode < self._min_microstep:
                    _debug(
                        f"    set_engine_settings: raising MicrostepMode "
                        f"{settings.MicrostepMode} -> {self._min_microstep}"
                    )
                    settings.MicrostepMode = self._min_microstep
            result = attr(*args, **kwargs)
            if result != _ll.Result.Ok:
                _debug(f"    {name} -> {_result_name(result)} ({result})")
            return result

        return wrapper


class _StandaAxis:
    """Shared connection / profile / homing handling for one Standa axis."""

    _error_cls = RuntimeError
    _name = "Axis"
    # Engine type this stage must run as. The settings profile applies
    # engine_settings *before* entype_settings, and a controller still in the
    # wrong mode rejects them (e.g. a stepper rejects the DC profile's
    # MicrostepMode=0 with ValueError). Forcing the engine type first avoids that.
    _engine_type = None
    # Minimum MicrostepMode to force into engine_settings while applying the
    # profile (None = leave as-is). The controllers reject MicrostepMode=0.
    _min_microstep = None

    def __init__(self, axis_index, serial, profile_filename, profile_func_name):
        self._axis_index = axis_index
        self._serial = serial
        self._profile_filename = profile_filename
        self._profile_func_name = profile_func_name
        self._axis = None
        self._initialized = False

    def connect(self):
        if self._axis is not None:
            return

        _debug(f"{self._name}: enumerating (flags={_ENUM_FLAGS.value}) ...")
        devices = ximc.enumerate_devices(_ENUM_FLAGS, "addr=")
        _debug(f"{self._name}: {len(devices)} controller(s) detected")
        for position, device in enumerate(devices):
            _debug(
                f"  [{position}] uri={device.get('uri')} "
                f"serial={device.get('device_serial')} "
                f"controller='{device.get('ControllerName', '').strip()}' "
                f"positioner='{device.get('PositionerName', '').strip()}'"
            )
        if not devices:
            raise self._error_cls(
                "No Standa controllers detected. Make sure XiLab (or any other "
                "program using the controllers) is closed - a controller can be "
                "open in only one application at a time."
            )

        uri = self._select_uri(devices)
        _debug(f"{self._name}: opening {uri}")
        axis = ximc.Axis(uri)
        axis.open_device()
        self._axis = axis
        self._log_identity()

    def _log_identity(self):
        if not DEBUG:
            return
        try:
            serial = self._axis.get_serial_number()
        except Exception as err:
            serial = f"<error: {err}>"
        try:
            positioner = self._axis.get_stage_name().PositionerName.strip()
        except Exception as err:
            positioner = f"<error: {err}>"
        _debug(
            f"{self._name}: opened device_id={self._axis._device_id} "
            f"serial={serial} positioner='{positioner}'"
        )

    def _select_uri(self, devices):
        if self._serial is not None:
            for device in devices:
                if device.get("device_serial") == self._serial:
                    _debug(f"{self._name}: matched serial {self._serial}")
                    return device["uri"]
            raise self._error_cls(
                f"{self._name} controller (serial {self._serial}) not found among "
                f"{len(devices)} detected controller(s). Run 'python shutter.py' to "
                f"list serials, and check XiLab is closed."
            )

        if len(devices) <= self._axis_index:
            raise self._error_cls(
                f"{self._name} not found: need controller at index {self._axis_index} "
                f"but only {len(devices)} controller(s) detected. If one is missing, "
                f"close XiLab (a controller opens in only one app at a time). To match "
                f"by serial instead, run 'python shutter.py' and set the serial in "
                f"shutter.py."
            )
        _debug(f"{self._name}: using index {self._axis_index} (no serial configured)")
        return devices[self._axis_index]["uri"]

    def disconnect(self):
        if self._axis is None:
            return
        try:
            self._axis.close_device()
        finally:
            self._axis = None
            self._initialized = False

    @property
    def is_connected(self):
        return self._axis is not None

    @property
    def is_initialized(self):
        return self._initialized

    def initialize(self):
        """Apply the settings profile and home the stage."""
        self.connect()
        self._apply_profile()
        self._axis.command_home()
        self._axis.command_wait_for_stop(10)
        self._initialized = True

    def _pre_profile(self):
        """Put the controller into the right engine type before the profile."""
        if self._engine_type is None:
            return
        entype = self._axis.get_entype_settings()
        if int(entype.EngineType) == self._engine_type:
            return
        _debug(
            f"{self._name}: forcing EngineType {int(entype.EngineType)} -> "
            f"{self._engine_type} before applying profile"
        )
        entype.EngineType = self._engine_type
        entype.DriverType = ximc.DriverType.DRIVER_TYPE_INTEGRATE
        self._axis.set_entype_settings(entype)

    def _apply_profile(self):
        _debug(
            f"{self._name}: applying profile {self._profile_filename} to "
            f"device_id={self._axis._device_id}"
        )
        self._pre_profile()
        # The profile writes a blank controller_name, wiping the user's friendly
        # label (e.g. "Axis 1"). Save it and restore it afterwards.
        saved_name = self._axis.get_controller_name()
        profile_func = _load_profile(self._profile_filename, self._profile_func_name)
        lib = _ProfileLib(_ll.lib, self._min_microstep)
        result = profile_func(lib, self._axis._device_id)
        _debug(f"{self._name}: profile result {_result_name(result)} ({result})")
        if result != _ll.Result.Ok:
            raise self._error_cls(
                f"Failed to apply {self._name} profile "
                f"({_result_name(result)} / {result}) - see console for the "
                f"rejected setting(s)"
            )
        self._restore_controller_name(saved_name)

    def _restore_controller_name(self, saved_name):
        if not saved_name.ControllerName.strip():
            return
        try:
            self._axis.set_controller_name(saved_name)
            _debug(
                f"{self._name}: restored controller name "
                f"'{saved_name.ControllerName.strip()}'"
            )
        except Exception as err:
            _debug(f"{self._name}: could not restore controller name: {err}")

    def _require_initialized(self):
        if not self._initialized:
            raise self._error_cls(
                f"{self._name} not initialised - apply the profile first"
            )


class ShutterController(_StandaAxis):
    _error_cls = ShutterError
    _name = "Shutter"
    _engine_type = ximc.EngineType.ENGINE_TYPE_DC.value
    # The 8MDS-70 profile sets MicrostepMode=0, which the controller rejects even
    # in DC mode; FULL is the smallest valid value and is fine for a DC motor.
    _min_microstep = ximc.MicrostepMode.MICROSTEP_MODE_FULL.value

    def __init__(self, axis_index=SHUTTER_AXIS_INDEX, serial=SHUTTER_SERIAL):
        super().__init__(axis_index, serial, "8MDS-70.py", "set_profile_8MDS_70")
        self._closed = False

    def initialize(self):
        super().initialize()
        # Homing leaves the shutter on one of its limit switches; read which.
        self._sync_closed_from_status()

    @property
    def is_closed(self):
        return self._closed

    def close(self):
        self._require_initialized()
        self._axis.command_right()
        self._closed = True

    def open(self):
        self._require_initialized()
        self._axis.command_left()
        self._closed = False

    def toggle(self):
        if self._closed:
            self.open()
        else:
            self.close()

    def status_text(self):
        if not self._initialized:
            return "Not initialised"
        flags = self._axis.get_status().GPIOFlags
        if flags & ximc.GPIOFlags.STATE_LEFT_EDGE:
            return "Open (left endstop)"
        if flags & ximc.GPIOFlags.STATE_RIGHT_EDGE:
            return "Closed (right endstop)"
        return "Moving"

    def _sync_closed_from_status(self):
        flags = self._axis.get_status().GPIOFlags
        if flags & ximc.GPIOFlags.STATE_RIGHT_EDGE:
            self._closed = True
        elif flags & ximc.GPIOFlags.STATE_LEFT_EDGE:
            self._closed = False


class RotationController(_StandaAxis):
    _error_cls = RotationError
    _name = "Rotation stage"
    _engine_type = ximc.EngineType.ENGINE_TYPE_STEP.value

    def __init__(self, axis_index=ROTATION_AXIS_INDEX, serial=ROTATION_SERIAL):
        super().__init__(axis_index, serial, "8MR151-1.py", "set_profile_8MR151_1")

    def initialize(self):
        super().initialize()
        # Configure user units so positions are read/commanded in degrees.
        microstep = self._axis.get_engine_settings().MicrostepMode
        self._axis.set_calb(ROTATION_DEG_PER_STEP, microstep)

    def move_to(self, degrees):
        self._require_initialized()
        self._axis.command_move_calb(float(degrees))
        self._axis.command_wait_for_stop(10)

    def get_angle(self):
        self._require_initialized()
        return self._axis.get_position_calb().Position

    def status_text(self):
        if not self._initialized:
            return "Not initialised"
        return f"{self.get_angle():.2f} deg"


def list_devices():
    """Return the list of enumerated Standa controllers (dicts from libximc)."""
    return ximc.enumerate_devices(_ENUM_FLAGS, "addr=")


_ENGINE_TYPE_NAMES = {
    getattr(ximc.EngineType, name).value: name
    for name in (
        "ENGINE_TYPE_NONE",
        "ENGINE_TYPE_DC",
        "ENGINE_TYPE_2DC",
        "ENGINE_TYPE_STEP",
        "ENGINE_TYPE_TEST",
        "ENGINE_TYPE_BRUSHLESS",
    )
    if hasattr(ximc.EngineType, name)
}


def _probe_motor(uri):
    """Open a controller and report its motor type, to tell shutter from stage.

    The shutter (8MDS-70) runs as a DC motor; the rotation stage (8MR151-1) runs
    as a stepper. Reading the configured engine type identifies each controller
    regardless of enumeration order.
    """
    axis = ximc.Axis(uri)
    axis.open_device()
    try:
        engine_type = int(axis.get_entype_settings().EngineType)
        microstep = axis.get_engine_settings().MicrostepMode
    finally:
        axis.close_device()
    name = _ENGINE_TYPE_NAMES.get(engine_type, str(engine_type))
    if engine_type == ximc.EngineType.ENGINE_TYPE_DC.value:
        role = "-> looks like the SHUTTER (8MDS-70)"
    elif engine_type == ximc.EngineType.ENGINE_TYPE_STEP.value:
        role = "-> looks like the ROTATION stage (8MR151-1)"
    else:
        role = "-> unconfigured / unknown"
    return name, microstep, role


def _uri_for_serial(serial):
    for device in list_devices():
        if device.get("device_serial") == serial:
            return device["uri"]
    raise RuntimeError(f"No controller with serial {serial} detected")


def probe_engine_settings(serial=SHUTTER_SERIAL):
    """Find which engine_settings value the shutter controller rejects.

    The 8MDS-70 profile fails at set_engine_settings with ValueError. This sends
    the profile's engine_settings plus a few variants and reports the result
    code of each, so we can see exactly which field is the problem.
    """
    uri = _uri_for_serial(serial)
    axis = ximc.Axis(uri)
    axis.open_device()
    device_id = axis._device_id
    try:
        entype = axis.get_entype_settings()
        print(f"serial {serial} @ {uri}")
        print(f"  current EngineType : {int(entype.EngineType)} "
              f"({_ENGINE_TYPE_NAMES.get(int(entype.EngineType))})")
        current = axis.get_engine_settings()
        print(f"  current MicrostepMode={current.MicrostepMode} "
              f"StepsPerRev={current.StepsPerRev} EngineFlags={int(current.EngineFlags)}")

        def trial(label, **overrides):
            settings = _ll.engine_settings_t()
            settings.NomVoltage = 800
            settings.NomCurrent = 176
            settings.NomSpeed = 12000
            settings.uNomSpeed = 0
            settings.EngineFlags = 224  # LIMIT_RPM | LIMIT_CURR | LIMIT_VOLT
            settings.Antiplay = 50
            settings.MicrostepMode = 0
            settings.StepsPerRev = 64
            for key, value in overrides.items():
                setattr(settings, key, value)
            result = _ll.lib.set_engine_settings(device_id, byref(settings))
            print(f"  {label:38s} -> {_result_name(result)} ({result})")

        print("  --- trials (base = exact 8MDS-70 profile values) ---")
        trial("base (MicrostepMode=0)")
        trial("MicrostepMode=FULL(1)", MicrostepMode=1)
        trial("MicrostepMode=FRAC_256(9)", MicrostepMode=9)
        trial("EngineFlags=0, MicrostepMode=0", EngineFlags=0)
        trial("EngineFlags=0, MicrostepMode=1", EngineFlags=0, MicrostepMode=1)
        trial("StepsPerRev=0, MicrostepMode=1", StepsPerRev=0, MicrostepMode=1)
    finally:
        axis.close_device()


if __name__ == "__main__":
    # Diagnostic: list every connected Standa controller, read its motor type,
    # suggest which is the shutter vs the rotation stage, then probe which
    # engine_settings value the shutter rejects. Close XiLab first.
    found = list_devices()
    if not found:
        print(
            "No Standa controllers detected.\n"
            "Close XiLab (and any other app using them) and re-run - a controller "
            "can be open in only one program at a time."
        )
        sys.exit(0)

    print(f"Detected {len(found)} controller(s):\n")
    serials = set()
    for position, device in enumerate(found):
        uri = device.get("uri")
        serials.add(device.get("device_serial"))
        print(f"  index {position}")
        print(f"    uri            : {uri}")
        print(f"    device_serial  : {device.get('device_serial')}")
        try:
            engine_name, microstep, role = _probe_motor(uri)
            print(f"    EngineType     : {engine_name} {role}")
            print(f"    MicrostepMode  : {microstep}")
        except Exception as err:
            print(f"    (could not probe motor type: {err})")
        print()

    if SHUTTER_SERIAL in serials:
        print("=== shutter engine_settings probe ===")
        try:
            probe_engine_settings()
        except Exception as err:
            print(f"  engine probe failed: {err}")
