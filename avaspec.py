import sys
import inspect
import ctypes
import struct
import os
import threading
import globals
from PyQt5.QtCore import *

AVS_SERIAL_LEN = 10
VERSION_LEN = 16
DETECTOR_NAME_LEN = 20
USER_ID_LEN = 64
INVALID_AVS_HANDLE_VALUE = 1000
ERR_ETHCONN_REUSE = -27
SENS_HAMS9201 = 4
SENS_TCD1304 = 5
SENS_SU256LSB = 17
SENS_SU512LDB = 18
SENS_HAMS11639 = 22
SENS_HAMG9208_512 = 24
SENS_HAMS13496 = 26
SENS_HAMS11155_2048_02_DIFF = 30
SENSOR_OFFSET = 1
NUMBER_OF_SENSOR_TYPES = 31
NR_DEFECTIVE_PIXELS = 30
MAX_NR_PIXELS = 4096
CLIENT_ID_SIZE = 32

DSTR_STATUS_DSS_MASK = 0x01
DSTR_STATUS_FOE_MASK = 0x02
DSTR_STATUS_IERR_MASK = 0x04

_DEMO_MODE = False
_demo_stop_event = None
_demo_callback_thread = None
func = ctypes.CFUNCTYPE  # always available

try:
    if 'linux' in sys.platform:
        lib = ctypes.CDLL("/usr/local/lib/libavs.so.0")
    elif 'darwin' in sys.platform:
        lib = ctypes.CDLL("/usr/local/lib/libavs.0.dylib")
    else:
        import ctypes.wintypes
        _DLL_DIR = os.path.dirname(os.path.abspath(__file__))
        if ctypes.sizeof(ctypes.c_voidp) == 8:
            WM_MEAS_READY = 0x8001
            lib = ctypes.WinDLL(os.path.join(_DLL_DIR, "avaspecx64.dll"))
            func = ctypes.WINFUNCTYPE
        else:
            WM_MEAS_READY = 0x0401
            lib = ctypes.WinDLL(os.path.join(_DLL_DIR, "avaspec.dll"))
            func = ctypes.WINFUNCTYPE
except OSError as e:
    _DEMO_MODE = True
    lib = None
    print(f"[DEMO] libavs not found ({e}) — running with simulated spectrometer")

_DEMO_PIXELS = 2048
_DEMO_WL_START = 900.0
_DEMO_WL_END = 1100.0
_DEMO_HANDLE = 1


class AvsIdentityType(ctypes.Structure):
  _pack_ = 1
  _fields_ = [("SerialNumber", ctypes.c_char * AVS_SERIAL_LEN),
              ("UserFriendlyName", ctypes.c_char * USER_ID_LEN),
              ("Status", ctypes.c_char)]

class BroadcastAnswerType(ctypes.Structure):
  _pack_ = 1
  _fields_ = [("InterfaceType", ctypes.c_uint8),
              ("serial", ctypes.c_char * AVS_SERIAL_LEN),
              ("port", ctypes.c_uint16),
              ("status", ctypes.c_uint8),
              ("RemoteHostIp", ctypes.c_uint32),
              ("LocalIp", ctypes.c_uint32),
              ("reserved", ctypes.c_uint8 * 4)]

class MeasConfigType(ctypes.Structure):
  _pack_ = 1
  _fields_ = [("m_StartPixel", ctypes.c_uint16),
              ("m_StopPixel", ctypes.c_uint16),
              ("m_IntegrationTime", ctypes.c_float),
              ("m_IntegrationDelay", ctypes.c_uint32),
              ("m_NrAverages", ctypes.c_uint32),
              ("m_CorDynDark_m_Enable", ctypes.c_uint8),
              ("m_CorDynDark_m_ForgetPercentage", ctypes.c_uint8),
              ("m_Smoothing_m_SmoothPix", ctypes.c_uint16),
              ("m_Smoothing_m_SmoothModel", ctypes.c_uint8),
              ("m_SaturationDetection", ctypes.c_uint8),
              ("m_Trigger_m_Mode", ctypes.c_uint8),
              ("m_Trigger_m_Source", ctypes.c_uint8),
              ("m_Trigger_m_SourceType", ctypes.c_uint8),
              ("m_Control_m_StrobeControl", ctypes.c_uint16),
              ("m_Control_m_LaserDelay", ctypes.c_uint32),
              ("m_Control_m_LaserWidth", ctypes.c_uint32),
              ("m_Control_m_LaserWaveLength", ctypes.c_float),
              ("m_Control_m_StoreToRam", ctypes.c_uint16)]

class DeviceConfigType(ctypes.Structure):
  _pack_ = 1
  _fields_ = [("m_Len", ctypes.c_uint16),
              ("m_ConfigVersion", ctypes.c_uint16),
              ("m_aUserFriendlyId", ctypes.c_char * USER_ID_LEN),
              ("m_Detector_m_SensorType", ctypes.c_uint8),
              ("m_Detector_m_NrPixels", ctypes.c_uint16),
              ("m_Detector_m_aFit", ctypes.c_float * 5),
              ("m_Detector_m_NLEnable", ctypes.c_bool),
              ("m_Detector_m_aNLCorrect", ctypes.c_double * 8),
              ("m_Detector_m_aLowNLCounts", ctypes.c_double),
              ("m_Detector_m_aHighNLCounts", ctypes.c_double),
              ("m_Detector_m_Gain", ctypes.c_float * 2),
              ("m_Detector_m_Reserved", ctypes.c_float),
              ("m_Detector_m_Offset", ctypes.c_float * 2),
              ("m_Detector_m_ExtOffset", ctypes.c_float),
              ("m_Detector_m_DefectivePixels", ctypes.c_uint16 * 30),
              ("m_Irradiance_m_IntensityCalib_m_Smoothing_m_SmoothPix", ctypes.c_uint16),
              ("m_Irradiance_m_IntensityCalib_m_Smoothing_m_SmoothModel", ctypes.c_uint8),
              ("m_Irradiance_m_IntensityCalib_m_CalInttime", ctypes.c_float),
              ("m_Irradiance_m_IntensityCalib_m_aCalibConvers", ctypes.c_float * 4096),
              ("m_Irradiance_m_CalibrationType", ctypes.c_uint8),
              ("m_Irradiance_m_FiberDiameter", ctypes.c_uint32),
              ("m_Reflectance_m_Smoothing_m_SmoothPix", ctypes.c_uint16),
              ("m_Reflectance_m_Smoothing_m_SmoothModel", ctypes.c_uint8),
              ("m_Reflectance_m_CalInttime", ctypes.c_float),
              ("m_Reflectance_m_aCalibConvers", ctypes.c_float * 4096),
              ("m_SpectrumCorrect", ctypes.c_float * 4096),
              ("m_StandAlone_m_Enable", ctypes.c_bool),
              ("m_StandAlone_m_Meas_m_StartPixel", ctypes.c_uint16),
              ("m_StandAlone_m_Meas_m_StopPixel", ctypes.c_uint16),
              ("m_StandAlone_m_Meas_m_IntegrationTime", ctypes.c_float),
              ("m_StandAlone_m_Meas_m_IntegrationDelay", ctypes.c_uint32),
              ("m_StandAlone_m_Meas_m_NrAverages", ctypes.c_uint32),
              ("m_StandAlone_m_Meas_m_CorDynDark_m_Enable", ctypes.c_uint8),
              ("m_StandAlone_m_Meas_m_CorDynDark_m_ForgetPercentage", ctypes.c_uint8),
              ("m_StandAlone_m_Meas_m_Smoothing_m_SmoothPix", ctypes.c_uint16),
              ("m_StandAlone_m_Meas_m_Smoothing_m_SmoothModel", ctypes.c_uint8),
              ("m_StandAlone_m_Meas_m_SaturationDetection", ctypes.c_uint8),
              ("m_StandAlone_m_Meas_m_Trigger_m_Mode", ctypes.c_uint8),
              ("m_StandAlone_m_Meas_m_Trigger_m_Source", ctypes.c_uint8),
              ("m_StandAlone_m_Meas_m_Trigger_m_SourceType", ctypes.c_uint8),
              ("m_StandAlone_m_Meas_m_Control_m_StrobeControl", ctypes.c_uint16),
              ("m_StandAlone_m_Meas_m_Control_m_LaserDelay", ctypes.c_uint32),
              ("m_StandAlone_m_Meas_m_Control_m_LaserWidth", ctypes.c_uint32),
              ("m_StandAlone_m_Meas_m_Control_m_LaserWaveLength", ctypes.c_float),
              ("m_StandAlone_m_Meas_m_Control_m_StoreToRam", ctypes.c_uint16),
              ("m_StandAlone_m_Nmsr", ctypes.c_int16),
              ("m_DynamicStorage", ctypes.c_uint8 * 12),
              ("m_Temperature_1_m_aFit", ctypes.c_float * 5),
              ("m_Temperature_2_m_aFit", ctypes.c_float * 5),
              ("m_Temperature_3_m_aFit", ctypes.c_float * 5),
              ("m_TecControl_m_Enable", ctypes.c_bool),
              ("m_TecControl_m_Setpoint", ctypes.c_float),
              ("m_TecControl_m_aFit", ctypes.c_float * 2),
              ("m_ProcessControl_m_AnalogLow", ctypes.c_float * 2),
              ("m_ProcessControl_m_AnalogHigh", ctypes.c_float * 2),
              ("m_ProcessControl_m_DigitalLow", ctypes.c_float * 10),
              ("m_ProcessControl_m_DigitalHigh", ctypes.c_float * 10),
              ("m_EthernetSettings_m_IpAddr", ctypes.c_uint32),
              ("m_EthernetSettings_m_NetMask", ctypes.c_uint32),
              ("m_EthernetSettings_m_Gateway", ctypes.c_uint32),
              ("m_EthernetSettings_m_DhcpEnabled", ctypes.c_uint8),
              ("m_EthernetSettings_m_TcpPort", ctypes.c_uint16),
              ("m_EthernetSettings_m_LinkStatus", ctypes.c_uint8),
              ("m_EthernetSettings_m_ClientIdType", ctypes.c_uint8),
              ("m_EthernetSettings_m_ClientIdCustom", ctypes.c_char * 32),
              ("m_EthernetSettings_m_Reserved", ctypes.c_uint8 * 79),
              ("m_Reserved", ctypes.c_uint8 * 9608),
              ("m_OemData", ctypes.c_uint8 * 4096)]

class DstrStatusType(ctypes.Structure):
  _pack_ = 1
  _fields_ = [("m_TotalScans", ctypes.c_uint32),
              ("m_UsedScans", ctypes.c_uint32),
              ("m_Flags", ctypes.c_uint32),
              ("m_IsStopEvent", ctypes.c_uint8),
              ("m_IsOverflowEvent", ctypes.c_uint8),
              ("m_IsInternalErrorEvent", ctypes.c_uint8),
              ("m_Reserved", ctypes.c_uint8)]


def _demo_spectrum():
    import numpy as np
    wl = [_DEMO_WL_START + (_DEMO_WL_END - _DEMO_WL_START) * i / (_DEMO_PIXELS - 1) for i in range(_DEMO_PIXELS)]
    arr = (ctypes.c_double * 4096)()
    for i in range(_DEMO_PIXELS):
        peak = 30000.0 * (2.718281828 ** (-((wl[i] - 1064.0) ** 2) / (2 * 5.0 ** 2)))
        import random
        noise = random.gauss(0, 150)
        arr[i] = max(0.0, min(65535.0, peak + noise + 800.0))
    return arr


def AVS_Init(a_Port=0):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int)
    paramflags = (1, "port",),
    AVS_Init = prototype(("AVS_Init", lib), paramflags)
    return AVS_Init(a_Port)

def AVS_Done():
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int)
    AVS_Done = prototype(("AVS_Done", lib),)
    return AVS_Done()

def AVS_GetNrOfDevices():
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int)
    AVS_GetNrOfDevices = prototype(("AVS_GetNrOfDevices", lib),)
    return AVS_GetNrOfDevices()

def AVS_UpdateUSBDevices():
    if _DEMO_MODE:
        return 1
    prototype = func(ctypes.c_int)
    AVS_UpdateUSBDevices = prototype(("AVS_UpdateUSBDevices", lib),)
    return AVS_UpdateUSBDevices()

def AVS_UpdateETHDevices(spectrometers=1):
    if _DEMO_MODE:
        return ()
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(BroadcastAnswerType * spectrometers))
    paramflags = (1, "listsize",), (2, "requiredsize",), (2, "ETHlist",),
    PT_AVS_UpdateETHDevices = prototype(("AVS_UpdateETHDevices", lib), paramflags)
    reqBufferSize, ETHlist = PT_AVS_UpdateETHDevices(spectrometers * 26)
    if reqBufferSize != spectrometers * 26:
        ETHlist = AVS_UpdateETHDevices(reqBufferSize // 26)
    return ETHlist

def AVS_GetList(spectrometers=1):
    if _DEMO_MODE:
        dev = AvsIdentityType()
        dev.SerialNumber = b"DEMO0001"
        dev.UserFriendlyName = b"Demo Spectrometer"
        dev.Status = b"\x00"
        arr = (AvsIdentityType * 1)(dev)
        return arr
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(AvsIdentityType * spectrometers))
    paramflags = (1, "listsize",), (2, "requiredsize",), (2, "IDlist",),
    PT_GetList = prototype(("AVS_GetList", lib), paramflags)
    reqBufferSize, spectrometerList = PT_GetList(spectrometers * 75)
    if reqBufferSize != spectrometers * 75:
        spectrometerList = AVS_GetList(reqBufferSize // 75)
    return spectrometerList

def AVS_GetHandleFromSerial(deviceSerial):
    if _DEMO_MODE:
        return _DEMO_HANDLE
    prototype = func(ctypes.c_int, ctypes.c_char_p)
    paramflags = (1, "deviceSerial",),
    AVS_Activate = prototype(("AVS_Activate", lib), paramflags)
    if type(deviceSerial) is str:
        deviceSerial = deviceSerial.encode("utf-8")
    return AVS_Activate(deviceSerial)

def AVS_Activate(deviceId):
    if _DEMO_MODE:
        return _DEMO_HANDLE
    datatype = ctypes.c_byte * 75
    temp = datatype()
    x = 0
    while x < 9:
        temp[x] = deviceId.SerialNumber[x]
        x += 1
    temp[9] = 0
    x += 1
    while x < 74:
        temp[x] = 0
        x += 1
    temp[74] = int.from_bytes(deviceId.Status, byteorder='big')
    prototype = func(ctypes.c_int, ctypes.c_byte * 75)
    paramflags = (1, "deviceId",),
    AVS_Activate = prototype(("AVS_Activate", lib), paramflags)
    return AVS_Activate(temp)

def AVS_Deactivate(handle):
    if _DEMO_MODE:
        return True
    prototype = func(ctypes.c_bool, ctypes.c_int)
    prototype.restype = ctypes.c_bool
    paramflags = (1, "handle",),
    AVS_Deactivate = prototype(("AVS_Deactivate", lib), paramflags)
    return AVS_Deactivate(handle)

def AVS_UseHighResAdc(handle, enable):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_bool)
    paramflags = (1, "handle",), (1, "enable",),
    AVS_UseHighResAdc = prototype(("AVS_UseHighResAdc", lib), paramflags)
    return AVS_UseHighResAdc(handle, enable)

def AVS_GetVersionInfo(handle):
    if _DEMO_MODE:
        return b"DEMO", b"DEMO", b"DEMO"
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_char * VERSION_LEN, ctypes.c_char * VERSION_LEN, ctypes.c_char * VERSION_LEN)
    paramflags = (1, "handle",), (2, "FPGAversion",), (2, "FWversion",), (2, "DLLversion",),
    AVS_GetVersionInfo = prototype(("AVS_GetVersionInfo", lib), paramflags)
    return AVS_GetVersionInfo(handle)

def AVS_PrepareMeasure(handle, measconf):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(MeasConfigType))
    paramflags = (1, "handle",), (1, "measconf",),
    AVS_PrepareMeasure = prototype(("AVS_PrepareMeasure", lib), paramflags)
    return AVS_PrepareMeasure(handle, measconf)

def AVS_Measure(handle, windowhandle, nummeas):
    if _DEMO_MODE:
        return 0
    if not (('linux' in sys.platform) or ('darwin' in sys.platform)):
        prototype = func(ctypes.c_int, ctypes.c_int, ctypes.wintypes.HWND, ctypes.c_int16)
    else:
        prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int16)
    paramflags = (1, "handle",), (1, "windowhandle",), (1, "nummeas"),
    AVS_Measure = prototype(("AVS_Measure", lib), paramflags)
    return AVS_Measure(handle, windowhandle, nummeas)


class AVS_MeasureCallbackFunc(object):
    def __init__(self, function):
        self.prototype = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        if _DEMO_MODE:
            self._raw_function = function
            self.callback = None
        else:
            self.callback = self.prototype(function)


def AVS_MeasureCallback(handle, cb, nummeas):
    if _DEMO_MODE:
        global _demo_stop_event, _demo_callback_thread

        if _demo_stop_event is not None:
            _demo_stop_event.set()

        stop = threading.Event()
        _demo_stop_event = stop
        total = int(nummeas)
        raw_fn = cb._raw_function

        def run():
            import time
            count = 0
            while not stop.is_set():
                time.sleep(0.02)
                if stop.is_set():
                    break
                p1 = (ctypes.c_int * 1)(0)
                p2 = (ctypes.c_int * 1)(0)
                raw_fn(p1, p2)
                count += 1
                if total > 0 and count >= total:
                    break

        _demo_callback_thread = threading.Thread(target=run, daemon=True)
        _demo_callback_thread.start()
        return 0

    prototype = func(ctypes.c_int, ctypes.c_int, cb.prototype, ctypes.c_int16)
    paramflags = (1, "handle",), (1, "adres",), (1, "nummeas"),
    AVS_MeasureCallback = prototype(("AVS_MeasureCallback", lib), paramflags)
    return AVS_MeasureCallback(handle, cb.callback, nummeas)


class AVS_DstrCallbackFunc(object):
    def __init__(self, function):
        self.prototype = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint))
        self.callback = self.prototype(function)

def AVS_SetDstrStatusCallback(handle, cb):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, cb.prototype)
    paramflags = (1, "handle",), (1, "adres",),
    AVS_SetDstrStatusCallback = prototype(("AVS_SetDstrStatusCallback", lib), paramflags)
    return AVS_SetDstrStatusCallback(handle, cb.callback)

def AVS_GetDstrStatus(handle):
    if _DEMO_MODE:
        return DstrStatusType()
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(DstrStatusType))
    paramflags = (1, "handle",), (2, "dstrstatus",),
    AVS_GetDstrStatus = prototype(("AVS_GetDstrStatus", lib), paramflags)
    return AVS_GetDstrStatus(handle)

def AVS_StopMeasure(handle):
    if _DEMO_MODE:
        global _demo_stop_event
        if _demo_stop_event is not None:
            _demo_stop_event.set()
            _demo_stop_event = None
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int)
    paramflags = (1, "handle",),
    AVS_StopMeasure = prototype(("AVS_StopMeasure", lib), paramflags)
    return AVS_StopMeasure(handle)

def AVS_PollScan(handle):
    if _DEMO_MODE:
        return 1
    prototype = func(ctypes.c_bool, ctypes.c_int)
    paramflags = (1, "handle",),
    AVS_PollScan = prototype(("AVS_PollScan", lib), paramflags)
    return AVS_PollScan(handle)

def AVS_GetScopeData(handle):
    if _DEMO_MODE:
        arr = _demo_spectrum()
        timestamp = ctypes.c_uint32(0)
        return timestamp, arr
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_double * 4096))
    paramflags = (1, "handle",), (2, "timelabel",), (2, "spectrum",),
    AVS_GetScopeData = prototype(("AVS_GetScopeData", lib), paramflags)
    timestamp, spectrum = AVS_GetScopeData(handle)
    return timestamp, spectrum

def AVS_GetSaturatedPixels(handle):
    if _DEMO_MODE:
        return (ctypes.c_uint8 * 4096)()
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_uint8 * 4096))
    paramflags = (1, "handle",), (2, "saturated",),
    AVS_GetSaturatedPixels = prototype(("AVS_GetSaturatedPixels", lib), paramflags)
    return AVS_GetSaturatedPixels(handle)

def AVS_GetLambda(handle):
    if _DEMO_MODE:
        arr = (ctypes.c_double * 4096)()
        for i in range(_DEMO_PIXELS):
            arr[i] = _DEMO_WL_START + (_DEMO_WL_END - _DEMO_WL_START) * i / (_DEMO_PIXELS - 1)
        return arr
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_double * 4096))
    paramflags = (1, "handle",), (2, "wavelength",),
    AVS_GetLambda = prototype(("AVS_GetLambda", lib), paramflags)
    return AVS_GetLambda(handle)

def AVS_GetNumPixels(handle):
    if _DEMO_MODE:
        return _DEMO_PIXELS
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_short))
    paramflags = (1, "handle",), (2, "numPixels",),
    AVS_GetNumPixels = prototype(("AVS_GetNumPixels", lib), paramflags)
    return AVS_GetNumPixels(handle)

def AVS_GetDigIn(handle, portId):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint8))
    paramflags = (1, "handle",), (1, "portId",), (2, "value",),
    AVS_GetDigIn = prototype(("AVS_GetDigIn", lib), paramflags)
    return AVS_GetDigIn(handle, portId)

def AVS_SetDigOut(handle, portId, value):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_uint8, ctypes.c_uint8)
    paramflags = (1, "handle",), (1, "portId",), (1, "value",),
    AVS_SetDigOut = prototype(("AVS_SetDigOut", lib), paramflags)
    return AVS_SetDigOut(handle, portId, value)

def AVS_SetPwmOut(handle, portId, frequency, dutycycle):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint8)
    paramflags = (1, "handle",), (1, "portId",), (1, "frequency",), (1, "dutycycle",),
    AVS_SetPwmOut = prototype(("AVS_SetPwmOut", lib), paramflags)
    return AVS_SetPwmOut(handle, portId, frequency, dutycycle)

def AVS_GetAnalogIn(handle, portId):
    if _DEMO_MODE:
        return 0.0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_uint8, ctypes.POINTER(ctypes.c_float))
    paramflags = (1, "handle",), (1, "portId",), (2, "value",),
    AVS_GetAnalogIn = prototype(("AVS_GetAnalogIn", lib), paramflags)
    return AVS_GetAnalogIn(handle, portId)

def AVS_SetAnalogOut(handle, portId, value):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_uint8, ctypes.c_float)
    paramflags = (1, "handle",), (1, "portId",), (1, "value",),
    AVS_SetAnalogOut = prototype(("AVS_SetAnalogOut", lib), paramflags)
    return AVS_SetAnalogOut(handle, portId, value)

def AVS_GetParameter(handle, size=63484):
    if _DEMO_MODE:
        devcon = DeviceConfigType()
        devcon.m_Detector_m_NrPixels = _DEMO_PIXELS
        return devcon
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(DeviceConfigType))
    paramflags = (1, "handle",), (1, "size",), (2, "reqsize",), (2, "deviceconfig",),
    AVS_GetParameter = prototype(("AVS_GetParameter", lib), paramflags)
    ret = AVS_GetParameter(handle, size)
    if ret[0] != size:
        ret = AVS_GetParameter(ret[0])
    return ret[1]

def AVS_SetParameter(handle, deviceconfig):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(DeviceConfigType))
    paramflags = (1, "handle",), (1, "deviceconfig",),
    AVS_SetParameter = prototype(("AVS_SetParameter", lib), paramflags)
    return AVS_SetParameter(handle, deviceconfig)

def AVS_ResetParameter(handle):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int)
    paramflags = (1, "handle",),
    AVS_ResetParameter = prototype(("AVS_ResetParameter", lib), paramflags)
    return AVS_ResetParameter(handle)

def AVS_SetSyncMode(handle, enable):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_bool)
    paramflags = (1, "handle",), (1, "enable",),
    AVS_SetSyncMode = prototype(("AVS_SetSyncMode", lib), paramflags)
    return AVS_SetSyncMode(handle, enable)

def AVS_GetDeviceType(handle):
    if _DEMO_MODE:
        return 1
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_byte))
    paramflags = (1, "handle",), (2, "devicetype",),
    AVS_GetDeviceType = prototype(("AVS_GetDeviceType", lib), paramflags)
    return AVS_GetDeviceType(handle)

def AVS_GetDetectorName(handle, SensorType):
    if _DEMO_MODE:
        return b"DemoDetector"
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_byte, ctypes.c_char * DETECTOR_NAME_LEN)
    paramflags = (1, "handle",), (1, "SensorType",), (2, "SensorName",),
    AVS_GetDetectorName = prototype(("AVS_GetDetectorName", lib), paramflags)
    return AVS_GetDetectorName(handle, SensorType)

def AVS_SetSensitivityMode(handle, enable):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_uint32)
    paramflags = (1, "handle",), (1, "enable",),
    AVS_SetSensitivityMode = prototype(("AVS_SetSensitivityMode", lib), paramflags)
    return AVS_SetSensitivityMode(handle, enable)

def AVS_SetPrescanMode(handle, enable):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int, ctypes.c_bool)
    paramflags = (1, "handle",), (1, "enable",),
    AVS_SetPrescanMode = prototype(("AVS_SetPrescanMode", lib), paramflags)
    return AVS_SetPrescanMode(handle, enable)

def AVS_ResetDevice(handle):
    if _DEMO_MODE:
        return 0
    prototype = func(ctypes.c_int, ctypes.c_int)
    paramflags = (1, "handle",),
    AVS_ResetDevice = prototype(("AVS_ResetDevice", lib), paramflags)
    return AVS_ResetDevice(handle)

def AVS_EnableLogging(enable):
    if _DEMO_MODE:
        return 1
    prototype = func(ctypes.c_int, ctypes.c_bool)
    paramflags = (1, "enable",),
    AVS_EnableLogging = prototype(("AVS_EnableLogging", lib), paramflags)
    return AVS_EnableLogging(enable)
