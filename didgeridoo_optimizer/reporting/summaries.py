from __future__ import annotations

from typing import Any, Mapping, Sequence


def strengths(result: Mapping[str, Any]) -> list[str]:
    features = dict(result.get('features', {}) or {})
    objective_scores = dict(result.get('objective_scores', {}) or {})
    penalties = dict(result.get('penalties', {}) or {})
    out: list[str] = []

    if float(features.get('model_confidence', 0.0)) >= 0.85:
        out.append('bonne confiance du modèle 1D dans la bande analysée')
    if float(objective_scores.get('drone_f0', 0.0)) >= 0.75 and features.get('f0_hz') is not None:
        out.append(f"fondamentale bien placée autour de {float(features['f0_hz']):.1f} Hz")
    if float(features.get('peak_count', 0.0)) >= 5:
        out.append('plusieurs pics d’impédance exploitables')
    if float(objective_scores.get('radiation_brightness', 0.0)) >= 0.55:
        out.append('bonne présence en hautes fréquences / rayonnement favorable')
    if float(objective_scores.get('toot', 0.0)) >= 0.55 and features.get('toot_ratio') is not None:
        out.append(f"toot plausible avec ratio ≈ {float(features['toot_ratio']):.2f}")
    if float(objective_scores.get('material_simplicity', 0.0)) >= 0.9 and float(penalties.get('material_change_penalty', 0.0)) == 0.0:
        out.append('layout matériaux simple et facile à fabriquer')
    return out[:4]


def weaknesses(result: Mapping[str, Any]) -> list[str]:
    features = dict(result.get('features', {}) or {})
    penalties = dict(result.get('penalties', {}) or {})
    warnings = list(result.get('warnings', []) or [])
    out: list[str] = []

    if float(features.get('model_confidence', 1.0)) < 0.7:
        out.append('confiance 1D limitée ; lecture prudente des métriques')
    if int(features.get('peak_count', 0)) < 3:
        out.append('peu de pics détectés pour caractériser finement le comportement')
    if 'large_bell_may_reduce_1d_validity' in warnings:
        out.append('grande cloche : gain potentiel de rayonnement mais validité 1D plus fragile')
    if float(penalties.get('geometry_soft_penalty', 0.0)) > 0.1:
        out.append('géométrie un peu éloignée des contraintes souples de recherche')
    if float(penalties.get('segment_count_penalty', 0.0)) > 0.1:
        out.append('segmentation relativement complexe pour la fabrication')
    if 'high_losses_material' in warnings:
        out.append('matériau dissipatif : pics plus larges mais appui spectral potentiellement réduit')
    return out[:4]


def tradeoffs(result: Mapping[str, Any]) -> list[str]:
    features = dict(result.get('features', {}) or {})
    objective_scores = dict(result.get('objective_scores', {}) or {})
    warnings = list(result.get('warnings', []) or [])
    out: list[str] = []

    if float(objective_scores.get('radiation_brightness', 0.0)) >= 0.55 and float(features.get('model_confidence', 1.0)) < 0.85:
        out.append('plus de rayonnement HF au prix d’une confiance 1D un peu plus basse')
    if float(features.get('fundamental_q') or 0.0) >= 15.0 and float(objective_scores.get('beginner_robustness', 1.0)) < 0.6:
        out.append('pics marqués et précis, mais tolérance joueur probablement moindre')
    if float(objective_scores.get('material_simplicity', 0.0)) < 0.8 and float(objective_scores.get('fabrication_simplicity', 0.0)) < 0.5:
        out.append('gains acoustiques partiels contre davantage de complexité de fabrication')
    if 'placeholder_feature_used' in warnings:
        out.append('certaines métriques restent MVP / placeholders et devront être raffinées plus tard')
    return out[:4]


def summarize_design(result: Mapping[str, Any], language: str = 'fr') -> str:
    if language.lower() != 'fr':
        raise ValueError("Only French summaries are implemented in this MVP reporting block.")

    features = dict(result.get('features', {}) or {})
    aggregate = float(result.get('aggregate_score', 0.0))
    design_id = str(result.get('design_id', 'design'))
    lines = [f"Design {design_id} — score agrégé {aggregate:.3f}."]
    f0 = features.get('f0_hz')
    if f0 is not None:
        lines.append(f"Fondamentale estimée : {float(f0):.1f} Hz ; {int(features.get('peak_count', 0))} pics détectés.")
    if features.get('toot_ratio') is not None:
        lines.append(f"Toot ratio estimé : {float(features['toot_ratio']):.2f}.")

    s = strengths(result)
    w = weaknesses(result)
    t = tradeoffs(result)
    if s:
        lines.append('Points forts : ' + '; '.join(s) + '.')
    if w:
        lines.append('Points faibles : ' + '; '.join(w) + '.')
    if t:
        lines.append('Compromis : ' + '; '.join(t) + '.')
    return ' '.join(lines)
