from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..pipeline.evaluate_linear import evaluate as evaluate_linear


@dataclass(slots=True)
class TimeDomainResonator:
    sample_rate_hz: int
    impulse_kernel: np.ndarray
    metadata: dict[str, Any]
    _flow_state: np.ndarray = field(init=False, repr=False)
    _last_pressure: float = field(init=False, repr=False, default=0.0)

    def __post_init__(self) -> None:
        self.impulse_kernel = np.asarray(self.impulse_kernel, dtype=float)
        self.reset()

    @classmethod
    def from_linear_model(
        cls,
        design: Mapping[str, Any],
        config: Mapping[str, Any],
        linear_pipeline: Any,
        materials: str | Path | None = None,
    ) -> "TimeDomainResonator":
        if isinstance(design, Mapping) and "zin" in design and "freq_hz" in design:
            linear_result = design
        else:
            if hasattr(linear_pipeline, "evaluate"):
                linear_result = linear_pipeline.evaluate(design, config, materials)
            else:
                linear_result = linear_pipeline(design, config, materials)
        return cls.from_linear_result(linear_result, config)

    @classmethod
    def from_linear_result(cls, linear_result: Mapping[str, Any], config: Mapping[str, Any]) -> "TimeDomainResonator":
        nonlinear_cfg = dict((config or {}).get("nonlinear_simulation", {}) or {})
        sample_rate_hz = min(int(nonlinear_cfg.get("sample_rate_hz", 44100)), 12000)
        freq_hz = np.asarray(linear_result.get("freq_hz", []), dtype=float)
        zin = np.asarray(linear_result.get("zin", []), dtype=complex)
        if freq_hz.size == 0 or zin.size == 0:
            kernel = np.zeros(32, dtype=float)
            kernel[0] = 1.0
            return cls(sample_rate_hz=sample_rate_hz, impulse_kernel=kernel, metadata={"source": "empty_linear_result"})

        n_kernel = min(256, max(64, int(sample_rate_hz * 0.01)))
        kernel = cls._kernel_from_spectrum(freq_hz, zin, sample_rate_hz, n_kernel)
        return cls(
            sample_rate_hz=sample_rate_hz,
            impulse_kernel=kernel,
            metadata={
                "source": "linear_result",
                "kernel_length": int(kernel.size),
                "f0_hz": float((linear_result.get("features", {}) or {}).get("f0_hz") or 0.0),
            },
        )

    @staticmethod
    def _kernel_from_spectrum(freq_hz: np.ndarray, zin: np.ndarray, sample_rate_hz: int, n_kernel: int) -> np.ndarray:
        n_fft = max(2 * n_kernel, 2048)
        positive_freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate_hz)
        real_interp = np.interp(positive_freqs, freq_hz, np.real(zin), left=np.real(zin[0]), right=np.real(zin[-1]))
        imag_interp = np.interp(positive_freqs, freq_hz, np.imag(zin), left=np.imag(zin[0]), right=np.imag(zin[-1]))
        spectrum = real_interp + 1j * imag_interp
        scale = max(np.max(np.abs(spectrum)), 1e-9)
        spectrum = spectrum / scale
        impulse = np.fft.irfft(spectrum, n=n_fft)
        window = np.hanning(2 * n_kernel)[:n_kernel]
        kernel = np.asarray(impulse[:n_kernel] * window, dtype=float)
        if not np.any(np.abs(kernel) > 0.0):
            kernel[0] = 1.0
        kernel *= 200.0
        return kernel

    def impulse_response(self) -> np.ndarray:
        return self.impulse_kernel.copy()

    def pressure_from_flow(self, flow_signal: Sequence[float] | np.ndarray) -> np.ndarray:
        flow_arr = np.asarray(flow_signal, dtype=float)
        if flow_arr.size == 0:
            return np.zeros(0, dtype=float)
        pressure = np.convolve(flow_arr, self.impulse_kernel, mode="full")[: flow_arr.size]
        return np.asarray(pressure, dtype=float)

    def reset(self) -> None:
        self._flow_state = np.zeros_like(self.impulse_kernel, dtype=float)
        self._last_pressure = 0.0

    def step(self, u_t: float) -> float:
        self._flow_state[1:] = self._flow_state[:-1]
        self._flow_state[0] = float(u_t)
        self._last_pressure = float(np.dot(self.impulse_kernel, self._flow_state))
        return self._last_pressure
