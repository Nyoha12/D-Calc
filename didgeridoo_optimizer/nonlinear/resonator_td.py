from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


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
        requested_model = str(
            nonlinear_cfg.get(
                "resonator_model_type",
                nonlinear_cfg.get("resonator_scaling_mode", "legacy"),
            )
        )
        if freq_hz.size == 0 or zin.size == 0:
            kernel = np.zeros(32, dtype=float)
            kernel[0] = 1.0
            return cls(
                sample_rate_hz=sample_rate_hz,
                impulse_kernel=kernel,
                metadata={
                    "source": "empty_linear_result",
                    "resonator_model_type": "legacy",
                    "resonator_scaling_mode": "empty_linear_result",
                    "kernel_length": int(kernel.size),
                    "kernel_duration_s": float(kernel.size / max(sample_rate_hz, 1)),
                    "experimental": False,
                    "requested_resonator_model_type": requested_model,
                },
            )

        f0_hz = float((linear_result.get("features", {}) or {}).get("f0_hz") or 0.0)
        base_metadata = {
            "source": "linear_result",
            "f0_hz": f0_hz,
        }

        if requested_model == "fir_long_logfit":
            kernel, extra_metadata = cls._fir_long_logfit_kernel_from_spectrum(
                freq_hz,
                zin,
                sample_rate_hz,
                linear_result,
                nonlinear_cfg,
            )
            if kernel is None:
                n_kernel = min(256, max(64, int(sample_rate_hz * 0.01)))
                kernel = cls._kernel_from_spectrum(freq_hz, zin, sample_rate_hz, n_kernel)
                extra_metadata = {
                    "resonator_model_type": "legacy",
                    "resonator_scaling_mode": "legacy_normalized",
                    "requested_resonator_model_type": requested_model,
                    "kernel_length": int(kernel.size),
                    "kernel_duration_s": float(kernel.size / max(sample_rate_hz, 1)),
                    "experimental": False,
                    "fallback_reason": "fir_long_logfit_reference_points_unavailable",
                }
            return cls(
                sample_rate_hz=sample_rate_hz,
                impulse_kernel=kernel,
                metadata={**base_metadata, **extra_metadata},
            )

        if requested_model not in {"legacy", "normalized_legacy", "legacy_current"}:
            raise ValueError(f"Unknown nonlinear_simulation.resonator_model_type={requested_model!r}.")

        n_kernel = min(256, max(64, int(sample_rate_hz * 0.01)))
        kernel = cls._kernel_from_spectrum(freq_hz, zin, sample_rate_hz, n_kernel)
        return cls(
            sample_rate_hz=sample_rate_hz,
            impulse_kernel=kernel,
            metadata={
                **base_metadata,
                "kernel_length": int(kernel.size),
                "kernel_duration_s": float(kernel.size / max(sample_rate_hz, 1)),
                "resonator_model_type": "legacy",
                "resonator_scaling_mode": "legacy_normalized",
                "experimental": False,
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

    @classmethod
    def _fir_long_logfit_kernel_from_spectrum(
        cls,
        freq_hz: np.ndarray,
        zin: np.ndarray,
        sample_rate_hz: int,
        linear_result: Mapping[str, Any],
        nonlinear_cfg: Mapping[str, Any],
    ) -> tuple[np.ndarray | None, dict[str, Any]]:
        duration_s = float(
            nonlinear_cfg.get(
                "resonator_kernel_duration_s",
                nonlinear_cfg.get("kernel_duration_s", 1.0),
            )
        )
        max_duration_s = float(nonlinear_cfg.get("resonator_max_kernel_duration_s", 2.0))
        duration_s = max(0.02, min(duration_s, max_duration_s))
        n_kernel = max(64, int(round(sample_rate_hz * duration_s)))
        kernel = cls._physical_fir_kernel_from_spectrum(freq_hz, zin, sample_rate_hz, n_kernel)
        reference_points = cls._scaling_reference_points(linear_result, freq_hz)
        if reference_points.size == 0:
            return None, {}

        target = np.abs(cls._interpolate_complex(reference_points, freq_hz, zin))
        response = np.abs(cls._kernel_frequency_response(kernel, sample_rate_hz, reference_points))
        valid = np.isfinite(target) & np.isfinite(response) & (target > 0.0) & (response > 0.0)
        if not np.any(valid):
            return None, {}

        scale = float(np.exp(np.mean(np.log(target[valid] / response[valid]))))
        if not np.isfinite(scale) or scale <= 0.0:
            return None, {}
        scale = float(max(min(scale, 1.0e12), 1.0e-12))
        kernel = np.asarray(kernel * scale, dtype=float)
        fit = cls._frequency_response_fit_metadata(freq_hz, zin, kernel, sample_rate_hz)
        metadata = {
            "resonator_model_type": "fir_long_logfit",
            "resonator_scaling_mode": "log_magnitude_multipoint",
            "kernel_duration_s": float(kernel.size / max(sample_rate_hz, 1)),
            "kernel_length": int(kernel.size),
            "scaling_reference_points_hz": [float(value) for value in reference_points[valid]],
            "scaling_gain": scale,
            "experimental": True,
            **fit,
        }
        return kernel, metadata

    @staticmethod
    def _physical_fir_kernel_from_spectrum(
        freq_hz: np.ndarray,
        zin: np.ndarray,
        sample_rate_hz: int,
        n_kernel: int,
    ) -> np.ndarray:
        n_fft = max(2 * n_kernel, 32768)
        positive_freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate_hz)
        real_interp = np.interp(positive_freqs, freq_hz, np.real(zin), left=np.real(zin[0]), right=np.real(zin[-1]))
        imag_interp = np.interp(positive_freqs, freq_hz, np.imag(zin), left=np.imag(zin[0]), right=np.imag(zin[-1]))
        spectrum = real_interp + 1j * imag_interp
        kernel = np.asarray(np.fft.irfft(spectrum, n=n_fft)[:n_kernel], dtype=float)
        if not np.any(np.abs(kernel) > 0.0):
            kernel[0] = 1.0
        return kernel

    @classmethod
    def _scaling_reference_points(cls, linear_result: Mapping[str, Any], freq_hz: np.ndarray) -> np.ndarray:
        freq_min = float(np.min(freq_hz))
        freq_max = float(np.max(freq_hz))
        features = dict(linear_result.get("features", {}) or {})
        peaks = list(linear_result.get("peaks", []) or [])
        candidates: list[float] = []

        def add(value: Any) -> None:
            try:
                point = float(value)
            except (TypeError, ValueError):
                return
            if not np.isfinite(point) or point < freq_min or point > freq_max:
                return
            if any(abs(point - existing) <= 1.0e-6 for existing in candidates):
                return
            candidates.append(point)

        f0_hz = features.get("f0_hz")
        add(f0_hz)
        if len(peaks) >= 2:
            add(dict(peaks[1]).get("frequency_hz"))
        if f0_hz is not None:
            add(3.0 * float(f0_hz))
        add(500.0)
        add(1000.0)
        return np.asarray(candidates, dtype=float)

    @staticmethod
    def _interpolate_complex(points_hz: np.ndarray, freq_hz: np.ndarray, values: np.ndarray) -> np.ndarray:
        return np.interp(points_hz, freq_hz, np.real(values)) + 1j * np.interp(points_hz, freq_hz, np.imag(values))

    @classmethod
    def _kernel_frequency_response(cls, kernel: np.ndarray, sample_rate_hz: int, points_hz: np.ndarray) -> np.ndarray:
        n_fft = max(32768, 2 ** int(np.ceil(np.log2(max(2 * kernel.size, 2048)))))
        response = np.fft.rfft(kernel, n=n_fft)
        response_freq_hz = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate_hz)
        return cls._interpolate_complex(points_hz, response_freq_hz, response)

    @classmethod
    def _frequency_response_fit_metadata(
        cls,
        freq_hz: np.ndarray,
        zin: np.ndarray,
        kernel: np.ndarray,
        sample_rate_hz: int,
    ) -> dict[str, float]:
        ratio_40_1000 = cls._frequency_response_ratio(freq_hz, zin, kernel, sample_rate_hz, 40.0, 1000.0, 256)
        ratio_40_3000 = cls._frequency_response_ratio(freq_hz, zin, kernel, sample_rate_hz, 40.0, 3000.0, 512)

        def mean_abs_log10(ratio: np.ndarray) -> float:
            return float(np.mean(np.abs(np.log10(np.maximum(ratio, 1.0e-30)))))

        return {
            "frequency_response_fit_error_40_1000": mean_abs_log10(ratio_40_1000),
            "frequency_response_fit_error_40_3000": mean_abs_log10(ratio_40_3000),
            "max_over_response": float(np.max(ratio_40_3000)),
            "max_under_response": float(np.max(1.0 / np.maximum(ratio_40_3000, 1.0e-30))),
        }

    @classmethod
    def _frequency_response_ratio(
        cls,
        freq_hz: np.ndarray,
        zin: np.ndarray,
        kernel: np.ndarray,
        sample_rate_hz: int,
        f_min_hz: float,
        f_max_hz: float,
        sample_count: int,
    ) -> np.ndarray:
        lo = max(float(np.min(freq_hz)), f_min_hz)
        hi = min(float(np.max(freq_hz)), f_max_hz)
        if hi <= lo:
            return np.ones(1, dtype=float)
        sample_hz = np.geomspace(max(lo, 1.0e-9), hi, sample_count)
        target = np.abs(cls._interpolate_complex(sample_hz, freq_hz, zin))
        response = np.abs(cls._kernel_frequency_response(kernel, sample_rate_hz, sample_hz))
        return response / np.maximum(target, 1.0e-30)

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
