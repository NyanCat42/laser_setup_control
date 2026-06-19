"""Simulated spectrometer source for testing without hardware.

When the "Simulate Spectrometer Data" option is enabled the main window feeds
this generator's output into the same globals (wavelength / spectraldata) the
real spectrometer fills, so the scope shows a Gaussian emission peak at 532 nm.

Closing the shutter holds the light for SIM_SHUTTER_DELAY seconds (the shutter's
mechanical latency) and then lets the peak decay exponentially to zero, which is
what the dialog's "Measure Latency" tool times against the 10% threshold.
"""

import time
import numpy as np

SIM_PEAK_WAVELENGTH = 532.0   # nm, centre of the simulated emission line
SIM_PEAK_FWHM = 8.0           # nm, full width at half maximum of the peak
SIM_FULL_LEVEL = 50000.0      # counts at the peak while fully open
SIM_NOISE = 50.0              # counts, std-dev of additive gaussian noise

SIM_SHUTTER_DELAY = 0.5       # s the light persists after a close command
SIM_DECAY_TAU = 0.15          # s exponential decay time constant once it starts

SIM_PIXELS = 2048
SIM_WL_MIN = 180.0            # nm, matches the scope's default x-range
SIM_WL_MAX = 1170.0


class SpectrometerSimulator:
    def __init__(self):
        self.wavelength = np.linspace(SIM_WL_MIN, SIM_WL_MAX, SIM_PIXELS)
        self.pixels = SIM_PIXELS

        sigma = SIM_PEAK_FWHM / (2.0 * np.sqrt(2.0 * np.log(2.0)))
        self._profile = np.exp(
            -((self.wavelength - SIM_PEAK_WAVELENGTH) ** 2) / (2.0 * sigma ** 2)
        )
        self._closed_at = None

    def set_closed(self, closed):
        """Track shutter state; records the close time on the closing edge."""
        if closed:
            if self._closed_at is None:
                self._closed_at = time.perf_counter()
        else:
            self._closed_at = None

    def intensity_factor(self):
        """Fraction of full intensity (1.0 open, decaying to 0 after close)."""
        if self._closed_at is None:
            return 1.0
        elapsed = time.perf_counter() - self._closed_at
        if elapsed < SIM_SHUTTER_DELAY:
            return 1.0
        return float(np.exp(-(elapsed - SIM_SHUTTER_DELAY) / SIM_DECAY_TAU))

    def peak(self):
        """Current peak counts, mirroring what np.max(spectrum) would give."""
        return SIM_FULL_LEVEL * self.intensity_factor()

    def spectrum(self):
        """A fresh noisy Gaussian spectrum scaled by the current intensity."""
        data = SIM_FULL_LEVEL * self.intensity_factor() * self._profile
        data = data + np.random.normal(0.0, SIM_NOISE, size=data.shape)
        return data
