from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..optimization.selector import FinalSelector


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def diversity_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    result = dict(candidate.get('result', candidate) or {})
    design = result.get('design')
    features = dict(result.get('features', {}) or {})

    if hasattr(design, 'metadata'):
        metadata = dict(getattr(design, 'metadata', {}) or {})
        segments = list(getattr(design, 'segments', []) or [])
    else:
        design = dict(design or {})
        metadata = dict(design.get('metadata', {}) or {})
        segments = list(design.get('segments', []) or [])

    topology = str(metadata.get('topology', candidate.get('genome', {}).get('topology', 'unknown')))

    total_length_cm = metadata.get('total_length_cm')
    if total_length_cm is None and hasattr(design, 'total_length_cm'):
        total_length_cm = float(getattr(design, 'total_length_cm'))
    elif total_length_cm is None:
        total_length_cm = sum(float(_field(seg, 'length_cm', 0.0)) for seg in segments)

    if segments:
        first = segments[0]
        last = segments[-1]
        d_in = float(_field(first, 'd_in_cm', 0.0))
        d_out = float(_field(last, 'd_out_cm', 0.0))
        material_layout = tuple(str(_field(seg, 'material_id', '')) for seg in segments)
        kinds = tuple(str(_field(seg, 'kind', '')) for seg in segments)
    else:
        d_in = 0.0
        d_out = 0.0
        material_layout = tuple()
        kinds = tuple()

    return (
        topology,
        round(float(total_length_cm) / 5.0),
        round(d_in / 0.2),
        round(d_out / 0.5),
        tuple(sorted(set(material_layout))),
        kinds,
        round(float(features.get('f0_hz') or 0.0) / 2.0),
    )


def deduplicate(candidates: Sequence[Mapping[str, Any]], config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    best_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for candidate in candidates:
        normalized = dict(candidate)
        key = diversity_key(normalized)
        current = best_by_key.get(key)
        current_score = float((current or {}).get('aggregate_score', dict((current or {}).get('result', {}) or {}).get('aggregate_score', float('-inf'))))
        candidate_score = float(normalized.get('aggregate_score', dict(normalized.get('result', {}) or {}).get('aggregate_score', float('-inf'))))
        if current is None or candidate_score > current_score:
            best_by_key[key] = normalized
    return list(best_by_key.values())


def rank(candidates: Sequence[Mapping[str, Any]], config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = dict(config or {})
    deduped = deduplicate(candidates, cfg)
    method = str(dict(cfg.get('optimization', {}) or {}).get('final_selector', 'knee'))
    selector = FinalSelector()
    ranked = selector.rank_top_n(deduped, len(deduped), method, cfg)
    for index, candidate in enumerate(ranked, start=1):
        candidate['report_rank'] = index
        candidate['report_diversity_key'] = diversity_key(candidate)
    return ranked
