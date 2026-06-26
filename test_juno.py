"""Minimal test for an Ophir PD300-1W sensor on a Juno USB interface.

Reads a few power samples and prints them. Windows-only. Run:

    python test_juno.py

Requires:
  - StarLab installed (provides the Ophir COM DLL)
  - pip install comtypes

IMPORTANT - why comtypes and not pywin32:
  The Ophir x64 COM server's IDispatch::Invoke / GetIDsOfNames path is broken on
  this build - every late-bound call (pywin32 Dispatch, comtypes dynamic) returns
  "Library not registered" (the DLL embeds typelib v10.10 but internally requests
  v10.11, which isn't registered). comtypes' EARLY binding calls the dual
  interface directly through the vtable, bypassing that path, so it works.
  The key is GetModule(dll) + CreateObject(..., interface=ICoLMMeasurement2).

Also: close StarLab before running - the Juno can be open in only one program
at a time.
"""

import sys

# Must be set before comtypes is imported: the Ophir server is apartment-threaded.
sys.coinit_flags = 2  # COINIT_APARTMENTTHREADED (STA)

try:
    import comtypes.client as cc
except ImportError:
    sys.exit("comtypes not installed.  Run:  pip install comtypes")

DLL = r"C:\Program Files\Ophir Optronics\StarLab 4.00\COM x64\OphirLMMeasurement.dll"

CHANNEL = 0       # Juno is single-channel
SAMPLES = 10


def main():
    try:
        mod = cc.GetModule(DLL)  # generate early-bound interface classes from the TLB
    except Exception as e:
        sys.exit(f"Could not load Ophir type library (is StarLab installed?): {e}")

    ophir = cc.CreateObject(mod.CoLMMeasurement, interface=mod.ICoLMMeasurement2)
    print(f"Ophir COM version: {ophir.GetVersion()}")

    serials = ophir.ScanUSB()
    if not serials:
        sys.exit("No Ophir USB device found. Is the Juno plugged in and is "
                 "StarLab closed?")
    serials = list(serials)
    print(f"Found device(s): {serials}  ->  opening {serials[0]}")
    handle = ophir.OpenUSBDevice(serials[0])

    if not ophir.IsSensorExists(handle, CHANNEL):
        ophir.Close(handle)
        sys.exit(f"No sensor on channel {CHANNEL}. Is the PD300 plugged into the Juno?")

    # Identify the head (each call returns its [out] params as a tuple).
    try:
        print(f"Device info: {ophir.GetDeviceInfo(handle)}")
        print(f"Sensor info: {ophir.GetSensorInfo(handle, CHANNEL)}")
    except Exception as e:
        print(f"(info query failed, continuing) {e}")

    ophir.StartStream(handle, CHANNEL)
    print(f"\nReading {SAMPLES} samples...\n")
    got = 0
    try:
        while got < SAMPLES:
            data, timestamps, statuses = ophir.GetData(handle, CHANNEL)
            for value, ts in zip(data, timestamps):
                print(f"  t={ts:>10.3f}   P = {value:.4e} W")
                got += 1
                if got >= SAMPLES:
                    break
    finally:
        ophir.StopStream(handle, CHANNEL)
        ophir.Close(handle)
        print("\nDone, device closed.")


if __name__ == "__main__":
    main()
