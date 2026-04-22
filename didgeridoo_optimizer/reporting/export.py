from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import yaml

from .plots import plot_impedance, plot_pareto, plot_radiation
from .summaries import summarize_design


def _to_builtin(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _to_builtin(v) for k, v in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        if np.iscomplexobj(value):
            return [{'real': float(v.real), 'imag': float(v.imag)} for v in value.tolist()]
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, complex):
        return {'real': float(value.real), 'imag': float(value.imag)}
    if isinstance(value, Mapping):
        return {str(k): _to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_builtin(v) for v in value]
    return value


def export_json(results: Any, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(_to_builtin(results), handle, ensure_ascii=False, indent=2)
    return out


def export_yaml(results: Any, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', encoding='utf-8') as handle:
        yaml.safe_dump(_to_builtin(results), handle, allow_unicode=True, sort_keys=False)
    return out


def export_csv_scores(ranked: Sequence[Mapping[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for item in ranked:
        result = dict(item.get('result', item) or {})
        features = dict(result.get('features', {}) or {})
        row = {
            'report_rank': item.get('report_rank'),
            'design_id': result.get('design_id', item.get('design_id', item.get('id'))),
            'aggregate_score': result.get('aggregate_score', item.get('aggregate_score')),
            'valid': result.get('valid', item.get('valid')),
            'f0_hz': features.get('f0_hz'),
            'peak_count': features.get('peak_count'),
            'fundamental_q': features.get('fundamental_q'),
            'toot_ratio': features.get('toot_ratio'),
            'brightness_proxy': features.get('brightness_proxy'),
            'model_confidence': features.get('model_confidence'),
        }
        for name, score in dict(result.get('objective_scores', {}) or {}).items():
            row[f'objective__{name}'] = score
        for name, score in dict(result.get('penalties', {}) or {}).items():
            row[f'penalty__{name}'] = score
        rows.append(_to_builtin(row))

    fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else ['design_id']
    with open(out, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out


def export_best_design_bundle(best: Mapping[str, Any], out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = dict(best.get('result', best) or {})
    summary_text = summarize_design(result, language='fr')
    summary_path = out / 'best_design_summary.txt'
    summary_path.write_text(summary_text + '\n', encoding='utf-8')

    design_path = export_json(result, out / 'best_design_result.json')
    yaml_path = export_yaml(result, out / 'best_design_result.yaml')
    imp_path = plot_impedance(result, out / 'best_design_impedance.png')
    rad_path = plot_radiation(result, out / 'best_design_radiation.png')
    return {
        'summary_txt': str(summary_path),
        'result_json': str(design_path),
        'result_yaml': str(yaml_path),
        'impedance_plot': str(imp_path),
        'radiation_plot': str(rad_path),
    }
