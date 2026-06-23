from __future__ import annotations

from collections import deque
import threading
from typing import Any

import mne
from mne.time_frequency import psd_array_welch
import numpy as np
from scipy import signal

from .protocol import EegSample
from ..time_utils import now_iso

V_REF = 4.5
PGA_GAIN = 24.0
LSB_TO_VOLTS = V_REF / PGA_GAIN / (2**15 - 1)
DEFAULT_ELECTRODE_NAMES = ("C3", "Cz", "C4", "", "", "", "", "")


def band_power(frequencies: np.ndarray, power: np.ndarray, low: float, high: float) -> float:
    mask = (frequencies >= low) & (frequencies <= high)
    if not np.any(mask):
        return 0.0
    return float(np.mean(power[:, mask]))


def channel_band_power(frequencies: np.ndarray, power: np.ndarray, low: float, high: float) -> np.ndarray:
    mask = (frequencies >= low) & (frequencies <= high)
    if not np.any(mask):
        return np.zeros(power.shape[0], dtype=float)
    return np.mean(power[:, mask], axis=1)


def normalize_electrode_names(names: tuple[str, ...] | list[str] | None, channel_count: int) -> tuple[str, ...]:
    configured = tuple((name or "").strip() for name in (names or DEFAULT_ELECTRODE_NAMES))
    return configured[:channel_count] + ("",) * max(0, channel_count - len(configured))


class EegFeatureMonitor:
    def __init__(
        self,
        sampling_rate: float = 250.0,
        window_seconds: float = 10.0,
        step_seconds: float = 5.0,
        channel_count: int = 8,
        electrode_names: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self.sampling_rate = sampling_rate
        self.window_samples = round(window_seconds * sampling_rate)
        self.step_samples = round(step_seconds * sampling_rate)
        self.channel_count = channel_count
        self.electrode_names = normalize_electrode_names(electrode_names, channel_count)
        self.buffer: deque[tuple[float, ...]] = deque(maxlen=self.window_samples)
        self.raw_buffer: deque[tuple[int, ...]] = deque(maxlen=self.window_samples)
        self.samples_since_update = 0
        self.last_sample_index: int | None = None
        self.discontinuity_count = 0
        self.latest: dict[str, Any] | None = None
        self.lock = threading.Lock()
        self.notch_b, self.notch_a = signal.iirnotch(50.0, 30.0, self.sampling_rate)
        self.band_b, self.band_a = signal.butter(
            2,
            [0.5, 30.0],
            btype="bandpass",
            fs=self.sampling_rate,
        )
        state_len = max(len(self.notch_a), len(self.notch_b)) - 1
        band_state_len = max(len(self.band_a), len(self.band_b)) - 1
        self.notch_states = np.zeros((self.channel_count, state_len))
        self.band_states = np.zeros((self.channel_count, band_state_len))

    def update_electrode_names(self, names: tuple[str, ...] | list[str]) -> None:
        normalized = normalize_electrode_names(names, self.channel_count)
        if any(normalized):
            with self.lock:
                self.electrode_names = normalized
                self.latest = None

    def reset(self) -> None:
        with self.lock:
            self.buffer.clear()
            self.raw_buffer.clear()
            self.samples_since_update = 0
            self.last_sample_index = None
            self.discontinuity_count = 0
            self.latest = None
            self.notch_states.fill(0.0)
            self.band_states.fill(0.0)

    def add(self, sample: EegSample) -> None:
        with self.lock:
            if self.last_sample_index is not None:
                step = (sample.sample_index - self.last_sample_index) & 0xFFFF
                if step != 1:
                    self.buffer.clear()
                    self.raw_buffer.clear()
                    self.samples_since_update = 0
                    self.latest = None
                    self.discontinuity_count += 1
            self.last_sample_index = sample.sample_index
            raw = tuple(sample.signals[: self.channel_count])
            self.raw_buffer.append(raw)
            filtered = []
            for channel_index, value in enumerate(raw):
                notch_value, self.notch_states[channel_index] = signal.lfilter(
                    self.notch_b,
                    self.notch_a,
                    [float(value)],
                    zi=self.notch_states[channel_index],
                )
                band_value, self.band_states[channel_index] = signal.lfilter(
                    self.band_b,
                    self.band_a,
                    notch_value,
                    zi=self.band_states[channel_index],
                )
                filtered.append(float(band_value[0] * LSB_TO_VOLTS))
            self.buffer.append(tuple(filtered))
            self.samples_since_update += 1

    def status(self) -> dict[str, Any]:
        with self.lock:
            should_calculate = (
                len(self.buffer) >= self.window_samples
                and (self.latest is None or self.samples_since_update >= self.step_samples)
            )
            values = np.asarray(self.buffer, dtype=float).T if should_calculate else None
            raw_values = np.asarray(self.raw_buffer, dtype=float) if self.raw_buffer else None
            if should_calculate:
                self.samples_since_update = 0
            buffered_samples = len(self.buffer)

        if values is not None:
            calculated = self._calculate(values)
            with self.lock:
                self.latest = calculated

        with self.lock:
            return {
                "ready": self.latest is not None,
                "buffered_samples": buffered_samples,
                "required_samples": self.window_samples,
                "discontinuity_count": self.discontinuity_count,
                "signal_quality": self._signal_quality(raw_values),
                "data": self.latest,
            }

    def _calculate(self, values: np.ndarray) -> dict[str, Any]:
        electrode_names = self.electrode_names[: values.shape[0]]
        active_indices = [index for index, name in enumerate(electrode_names) if name]
        if not active_indices:
            active_indices = list(range(values.shape[0]))
        info = mne.create_info(
            [name or f"CH{index + 1}" for index, name in enumerate(electrode_names)],
            sfreq=self.sampling_rate,
            ch_types="eeg",
        )
        raw = mne.io.RawArray(values, info, verbose=False)
        data = raw.get_data()
        n_per_seg = min(data.shape[1], 1024)
        power, frequencies = psd_array_welch(
            data,
            sfreq=self.sampling_rate,
            fmin=0.5,
            fmax=30.0,
            n_fft=max(n_per_seg, min(data.shape[1], 2048)),
            n_overlap=min(n_per_seg - 1, 768),
            n_per_seg=n_per_seg,
            average="mean",
            window="hann",
            remove_dc=True,
            output="power",
            verbose=False,
        )
        active_power = power[active_indices, :]
        theta = band_power(frequencies, active_power, 4.0, 7.0)
        alpha = band_power(frequencies, active_power, 8.0, 12.0)
        beta = band_power(frequencies, active_power, 13.0, 30.0)
        alpha_channels = channel_band_power(frequencies, power, 8.0, 12.0)
        electrode_lookup = {name.upper(): index for index, name in enumerate(electrode_names) if name}
        left_frontal = electrode_lookup.get("F3")
        right_frontal = electrode_lookup.get("F4")
        if left_frontal is not None and right_frontal is not None:
            approach_avoidance = np.log(max(alpha_channels[right_frontal], np.finfo(float).tiny)) - np.log(
                max(alpha_channels[left_frontal], np.finfo(float).tiny)
            )
            approach_avoidance_available = True
        else:
            approach_avoidance = 0.0
            approach_avoidance_available = False
        engagement = beta / max(alpha + theta, np.finfo(float).tiny)
        workload = theta / max(alpha, np.finfo(float).tiny)
        return {
            "calculated_at": now_iso(),
            "theta": round(theta, 12),
            "alpha": round(alpha, 12),
            "beta": round(beta, 12),
            "engagement": round(float(engagement), 4),
            "workload": round(float(workload), 4),
            "approach_avoidance": round(float(approach_avoidance), 4),
            "approach_avoidance_available": approach_avoidance_available,
            "electrode_names": list(electrode_names),
            "active_electrodes": [electrode_names[index] or f"CH{index + 1}" for index in active_indices],
            "frequencies": np.round(frequencies, 3).tolist(),
            "psd_db": np.round(10.0 * np.log10(np.maximum(power, np.finfo(float).tiny)), 3).tolist(),
        }

    def _signal_quality(self, values: np.ndarray | None) -> list[dict[str, float | str]]:
        if values is None:
            return []
        quality = []
        for channel_index in range(values.shape[1]):
            channel = values[:, channel_index]
            quality.append(
                {
                    "name": f"CH{channel_index + 1}",
                    "standard_deviation_uv": round(float(np.std(channel) * LSB_TO_VOLTS * 1e6), 3),
                    "clipping_fraction": round(
                        float(np.mean((channel <= -32768) | (channel >= 32767))),
                        6,
                    ),
                }
            )
        return quality


def synthetic_sample(index: int, sampling_rate: float = 250.0) -> EegSample:
    t = index / sampling_rate
    values = []
    for channel in range(8):
        theta = 1200 * np.sin(2 * np.pi * (5.5 + channel * 0.03) * t)
        alpha = 900 * np.sin(2 * np.pi * (10.0 + channel * 0.04) * t)
        beta = 450 * np.sin(2 * np.pi * (18.0 + channel * 0.05) * t)
        values.append(int(theta + alpha + beta))
    return EegSample(sample_index=index & 0xFFFF, signals=tuple(values), trigger=0)
