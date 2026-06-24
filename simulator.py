"""Simulated spectrometer source for testing without hardware.

When the "Simulate Spectrometer Data" option is enabled the main window feeds
this generator's output into the same globals (wavelength / spectraldata) the
real spectrometer fills, so the scope shows a Gaussian emission peak at 532 nm.

Closing the shutter holds the light for SIM_SHUTTER_DELAY seconds (the shutter's
mechanical latency) and then lets the peak decay exponentially to zero, which is
what the dialog's "Measure Latency" tool times against the 10% threshold.

A second peak at 1064 nm models the unstable first-harmonic pump beam. Its
intensity wanders slowly over time, independent of the shutter, so the rotation
stage / ND-filter intensity-stabilisation loop can be exercised without
spectrometer hardware. (The wander is a free-running disturbance: it does not
respond to the stage, so the loop's corrections won't visibly cancel it here.)
"""

import time
import numpy as np

SIM_PEAK_WAVELENGTH = 532.0   # nm, centre of the simulated emission line
SIM_PEAK_FWHM = 8.0           # nm, full width at half maximum of the peak
SIM_FULL_LEVEL = 50000.0      # counts at the peak while fully open
SIM_NOISE = 50.0              # counts, std-dev of additive gaussian noise

SIM_SHUTTER_DELAY = 0.5       # s the light persists after a close command
SIM_DECAY_TAU = 0.15          # s exponential decay time constant once it starts

# 1064 nm first-harmonic pump, with a slow drifting intensity to stabilise against.
SIM_PUMP_WAVELENGTH = 1064.0  # nm, centre of the simulated pump line
SIM_PUMP_FWHM = 10.0          # nm, full width at half maximum of the pump peak
SIM_PUMP_LEVEL = 40000.0      # counts at the pump peak, before drift
SIM_PUMP_DRIFT = 0.35         # fractional drift amplitude about SIM_PUMP_LEVEL
SIM_PUMP_PERIODS = (6.7, 2.9) # s, periods of the superimposed drift components

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

        pump_sigma = SIM_PUMP_FWHM / (2.0 * np.sqrt(2.0 * np.log(2.0)))
        self._pump_profile = np.exp(
            -((self.wavelength - SIM_PUMP_WAVELENGTH) ** 2) / (2.0 * pump_sigma ** 2)
        )

        self._closed_at = None
        self._start = time.perf_counter()

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
        """Current 532 nm peak counts, mirroring what np.max(spectrum) would give."""
        return SIM_FULL_LEVEL * self.intensity_factor()

    def pump_level(self):
        """Current 1064 nm pump peak counts: a slow free-running drift.

        Two superimposed sinusoids give a smooth, quasi-irregular wander the
        stabilisation loop can chase, without responding to the rotation stage.
        """
        t = time.perf_counter() - self._start
        wander = 0.0
        for period in SIM_PUMP_PERIODS:
            wander += np.sin(2.0 * np.pi * t / period)
        wander /= len(SIM_PUMP_PERIODS)  # normalise to roughly [-1, 1]
        return SIM_PUMP_LEVEL * (1.0 + SIM_PUMP_DRIFT * wander)

    def spectrum(self):
        """A fresh noisy spectrum: the 532 nm line plus the drifting 1064 nm pump."""
        data = SIM_FULL_LEVEL * self.intensity_factor() * self._profile
        data = data + self.pump_level() * self._pump_profile
        data = data + np.random.normal(0.0, SIM_NOISE, size=data.shape)
        return data
