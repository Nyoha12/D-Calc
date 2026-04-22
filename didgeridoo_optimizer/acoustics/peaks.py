from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np


def find_peaks(
    freq_hz: Sequence[float] | np.ndarray,
    zin_mag: Sequence[float] | np.ndarray,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, float | int | None]]:
    freq = np.asarray(freq_hz, dtype=float)
    mag = np.asarray(zin_mag, dtype=float)
    if freq.size != mag.size:
        raise ValueError("freq_hz and zin_mag must have the same length.")
    if freq.size < 3:
        return []

    peak_cfg = dict(dict((config or {}).get("frequency_analysis", {}) or {}).get("peak_detection", {}) or {})
    min_prominence = float(peak_cfg.get("min_prominence", 0.01))
    min_distance_hz = float(peak_cfg.get("min_distance_hz", 5.0))
    max_number = int(peak_cfg.get("max_number_of_peaks", 30))

    step_hz = float(np.median(np.diff(freq))) if freq.size > 1 else min_distance_hz
    min_distance_pts = max(1, int(round(min_distance_hz / max(step_hz, 1e-9))))

    candidates: list[tuple[float, int]] = []
    for index in range(1, mag.size - 1):
        if mag[index] <= mag[index - 1] or mag[index] <= mag[index + 1]:
            continue
        prominence = _estimate_prominence(mag, index, min_distance_pts)
        if prominence >= min_prominence:
            candidates.append((prominence, index))

    candidates.sort(key=lambda item: (item[0], mag[item[1]]), reverse=True)
    selected: list[int] = []
    for _prominence, index in candidates:
        if all(abs(index - other) >= min_distance_pts for other in selected):
            selected.append(index)
        if len(selected) >= max_number:
            break

    peaks = []
    for index in sorted(selected):
        prominence = _estimate_prominence(mag, index, min_distance_pts)
        left_hz, right_hz = peak_width_half_height(freq, mag, index)
        peaks.append(
            {
                "index": int(index),
                "frequency_hz": float(freq[index]),
                "magnitude": float(mag[index]),
                "q": estimate_q(freq, mag, index),
                "left_hz": left_hz,
                "right_hz": right_hz,
                "prominence": float(prominence),
            }
        )
    return peaks


def peak_width_half_height(
    freq_hz: Sequence[float] | np.ndarray,
    zin_mag: Sequence[float] | np.ndarray,
    peak_index: int,
) -> tuple[float | None, float | None]:
    freq = np.asarray(freq_hz, dtype=float)
    mag = np.asarray(zin_mag, dtype=float)
    if peak_index <= 0 or peak_index >= mag.size - 1:
        return None, None

    left_base = float(np.min(mag[: peak_index + 1]))
    right_base = float(np.min(mag[peak_index:]))
    baseline = max(left_base, right_base)
    level = baseline + 0.5 * max(mag[peak_index] - baseline, 0.0)

    left = peak_index
    while left > 0 and mag[left] > level:
        left -= 1

    right = peak_index
    while right < mag.size - 1 and mag[right] > level:
        right += 1

    left_hz = float(freq[left]) if left != peak_index else None
    right_hz = float(freq[right]) if right != peak_index else None
    return left_hz, right_hz


def estimate_q(
    freq_hz: Sequence[float] | np.ndarray,
    zin_mag: Sequence[float] | np.ndarray,
    peak_index: int,
) -> float | None:
    freq = np.asarray(freq_hz, dtype=float)
    left_hz, right_hz = peak_width_half_height(freq, zin_mag, peak_index)
    if left_hz is None or right_hz is None:
        return None
    width = max(right_hz - left_hz, 1e-9)
    return float(freq[peak_index] / width)


def _estimate_prominence(mag: np.ndarray, peak_index: int, window_pts: int) -> float:
    left_start = max(0, peak_index - window_pts)
    right_stop = min(mag.size, peak_index + window_pts + 1)
    left_min = float(np.min(mag[left_start : peak_index + 1]))
    right_min = float(np.min(mag[peak_index:right_stop]))
    return float(mag[peak_index] - max(left_min, right_min))
