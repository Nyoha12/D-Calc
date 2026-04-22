from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def plot_impedance(result: Mapping[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    freq = np.asarray(result.get('freq_hz', []), dtype=float)
    zin_mag = np.asarray(result.get('zin_mag', []), dtype=float)
    peaks = list(result.get('peaks', []) or [])

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(freq, zin_mag)
    if peaks:
        ax.scatter([p['frequency_hz'] for p in peaks], [p['magnitude'] for p in peaks], s=15)
    ax.set_xlabel('Frequency (Hz)')
    ax.set_ylabel('|Zin|')
    ax.set_title(f"Input impedance — {result.get('design_id', 'design')}")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def plot_radiation(result: Mapping[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    freq = np.asarray(result.get('freq_hz', []), dtype=float)
    metrics = dict(dict(result.get('features', {}) or {}).get('radiation_metrics', {}) or {})
    bands = dict(metrics.get('bands', {}) or {})

    fig, ax = plt.subplots(figsize=(7, 4.5))
    if bands:
        names = list(bands.keys())
        values = [float(dict(bands[name]).get('mean_real_admittance', 0.0)) for name in names]
        ax.bar(names, values)
        ax.set_ylabel('Mean real admittance')
        ax.set_title(f"Radiation proxy bands — {result.get('design_id', 'design')}")
    else:
        ax.plot(freq, np.zeros_like(freq))
        ax.set_title('Radiation proxy unavailable')
    ax.grid(True, axis='y', alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def plot_pareto(ranked: Sequence[Mapping[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    xs = []
    ys = []
    labels = []
    for item in ranked:
        result = dict(item.get('result', item) or {})
        feats = dict(result.get('features', {}) or {})
        xs.append(float(feats.get('f0_hz') or 0.0))
        ys.append(float(result.get('aggregate_score', item.get('aggregate_score', 0.0))))
        labels.append(str(result.get('design_id', item.get('id', 'candidate'))))

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(xs, ys)
    for x, y, label in zip(xs[:10], ys[:10], labels[:10]):
        ax.annotate(label, (x, y), fontsize=7, alpha=0.8)
    ax.set_xlabel('f0 (Hz)')
    ax.set_ylabel('Aggregate score')
    ax.set_title('Pareto / compromise overview')
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out
