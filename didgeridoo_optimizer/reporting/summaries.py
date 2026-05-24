from __future__ import annotations

from typing import Any, Mapping, Sequence


INTERPRETATION_DISCLAIMER = (
    "Interprétation : résultat d’optimisation sous le modèle et la configuration courants ; "
    "ce n’est pas une validation physique, une garantie de jouabilité, ni une promotion matériau."
)


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
        out.append('proxy relatif de brillance/rayonnement HF favorable dans le modèle')
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
        validity = _validity_band_line(features, prefix='prudence 1D estimée')
        if validity:
            out.append(f"confiance 1D limitée ; {validity}")
        else:
            out.append('confiance 1D limitée ; lecture prudente des métriques')
    if int(features.get('peak_count', 0)) < 3:
        out.append('peu de pics détectés pour caractériser finement le comportement')
    if 'odd_only_score_limited_by_peak_count' in warnings:
        out.append('score odd/even limité par le nombre de pics détectés ; lecture prudente du profil harmonique')
    if 'large_bell_may_reduce_1d_validity' in warnings:
        conf_085 = _positive_float(features.get('model_validity_band_conf_085_hz'))
        if conf_085 is not None:
            out.append(f"grande cloche : validité 1D plus fragile ; prudence ≥0.85 estimée jusqu’à ~{_format_hz(conf_085)}")
        else:
            out.append('grande cloche : gain potentiel de rayonnement mais validité 1D plus fragile')
    if float(penalties.get('geometry_soft_penalty', 0.0)) > 0.1:
        out.append('géométrie un peu éloignée des contraintes souples de recherche')
    if float(penalties.get('segment_count_penalty', 0.0)) > 0.1:
        out.append('segmentation relativement complexe pour la fabrication')
    if 'material_loss_calibration_limited' in warnings:
        out.append('paramètres de pertes matériau à lire prudemment ; Q, magnitude et backpressure peuvent être sensibles à une calibration non établie')
    if 'high_losses_material' in warnings:
        out.append('matériau dissipatif : pics plus larges mais appui spectral potentiellement réduit')
    return out[:4]


def tradeoffs(result: Mapping[str, Any]) -> list[str]:
    features = dict(result.get('features', {}) or {})
    objective_scores = dict(result.get('objective_scores', {}) or {})
    warnings = list(result.get('warnings', []) or [])
    out: list[str] = []

    if float(objective_scores.get('radiation_brightness', 0.0)) >= 0.55 and float(features.get('model_confidence', 1.0)) < 0.85:
        out.append('proxy relatif de brillance/rayonnement HF plus élevé au prix d’une confiance 1D un peu plus basse')
    if float(features.get('fundamental_q') or 0.0) >= 15.0 and float(objective_scores.get('beginner_robustness', 1.0)) < 0.6:
        out.append('pics marqués et précis, mais tolérance joueur probablement moindre')
    if float(objective_scores.get('material_simplicity', 0.0)) < 0.8 and float(objective_scores.get('fabrication_simplicity', 0.0)) < 0.5:
        out.append('gains acoustiques partiels contre davantage de complexité de fabrication')
    if 'material_loss_calibration_limited' in warnings:
        out.append('Q, magnitude et backpressure peuvent refléter des paramètres de pertes non calibrés')
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
    validity_line = _validity_band_line(features)
    if validity_line:
        lines.append(validity_line + ".")

    s = strengths(result)
    w = weaknesses(result)
    t = tradeoffs(result)
    if s:
        lines.append('Points forts : ' + '; '.join(s) + '.')
    if w:
        lines.append('Points faibles : ' + '; '.join(w) + '.')
    if t:
        lines.append('Compromis : ' + '; '.join(t) + '.')
    lines.append(INTERPRETATION_DISCLAIMER)
    return ' '.join(lines)


def summarize_post_run_interpretation(
    summary: Mapping[str, Any],
    *,
    exports: Mapping[str, Any] | None = None,
    language: str = 'fr',
) -> str:
    if language.lower() != 'fr':
        raise ValueError("Only French interpretation notes are implemented in this MVP reporting block.")

    summary_map = dict(summary or {})
    exports_map = dict(exports if exports is not None else summary_map.get('exports', {}) or {})
    best = dict(summary_map.get('best_design', {}) or {})
    result = dict(best.get('result', best) or {})
    features = dict(result.get('features', {}) or {})
    result_warnings = [str(item) for item in list(result.get('warnings', []) or [])]
    summary_warnings = [str(item) for item in list(summary_map.get('warnings', []) or [])]
    warnings = list(dict.fromkeys([*summary_warnings, *result_warnings]))
    top_20 = summary_map.get('top_20', [])
    top_20_count = len(top_20) if isinstance(top_20, Sequence) and not isinstance(top_20, (str, bytes)) else 0

    sections: list[str] = [
        "\n".join(
            [
                "Aide non contractuelle",
                "Ce fichier est une aide d’interprétation non contractuelle pour le run courant.",
                "Il ne constitue pas une validation physique, une garantie de jouabilité, une recommandation de fabrication ou une promotion matériau.",
            ]
        ),
        "\n".join(
            [
                "Run summary",
                f"- Schema report : {_display(summary_map.get('schema_version'))}",
                (
                    "- Config schema : "
                    f"{_display(summary_map.get('config_schema_version'))} "
                    f"({_display(summary_map.get('config_schema_status'))})"
                ),
                f"- Runtime actual seconds : {_format_number(summary_map.get('runtime_actual_seconds'))}",
                f"- Linear phase : {_phase_line(summary_map.get('linear_results'))}",
                f"- Robustness phase : {_phase_line(summary_map.get('robust_results'))}",
                f"- Nonlinear phase : {_phase_line(summary_map.get('nonlinear_results'))}",
                f"- Exports principaux : {_export_summary(exports_map)}",
            ]
        ),
        "\n".join(
            [
                "Best design",
                f"- Candidat sélectionné : {_display(result.get('design_id') or best.get('design_id') or best.get('id'))}",
                "- Lecture : compromis calculé sous le modèle et la configuration courants.",
                f"- Score agrégé : {_format_number(result.get('aggregate_score') or best.get('aggregate_score'))}",
                f"- f0_hz : {_format_number(features.get('f0_hz'))}",
                f"- peak_count : {_display(features.get('peak_count'))}",
                f"- fundamental_q : {_format_number(features.get('fundamental_q'))}",
                f"- toot_ratio : {_format_number(features.get('toot_ratio'))}",
                f"- brightness_proxy : {_format_number(features.get('brightness_proxy'))}",
                f"- backpressure_proxy : {_format_number(features.get('backpressure_proxy'))}",
                f"- model_confidence : {_format_number(features.get('model_confidence'))}",
                *_model_validity_output_lines(features),
                f"- warnings : {_list_summary(warnings)}",
            ]
        ),
        "\n".join(
            [
                "Metric status",
                "- f0, peaks et Q sont des métriques calculées par le modèle.",
                "- brightness, backpressure, toot, harmonicity et odd/even sont des proxy, pas des garanties de qualité ou de jouabilité.",
                "- odd_only_score dépend du nombre de pics conservés par la détection ; il doit être lu prudemment si peu de pics sont disponibles.",
                "- Q, magnitude de pic et backpressure dépendent fortement des paramètres de pertes matériaux ; ces paramètres doivent être lus selon leur statut de calibration.",
                "- warnings est advisory : à lire comme signal de prudence, pas comme politique de validation.",
                "- vocal_control_proxy et transient_proxy restent des placeholders si leur valeur est absente.",
                "- Les matériaux doivent être lus selon leurs statuts ; ce fichier ne promeut aucun matériau.",
            ]
        ),
        "\n".join(
            [
                "Physical limits",
                "- Le modèle acoustique principal est linéaire et 1D.",
                "- Les résultats dépendent de la bande de fréquence configurée.",
                "- Les bandes de validité 1D sont des seuils calculés du modèle courant ; elles ne constituent pas une validation expérimentale.",
                "- Les pertes, la radiation et les paramètres matériaux doivent être lus prudemment.",
                "- Robustness et nonlinear, même présents, ne constituent pas une validation physique.",
            ]
        ),
        "\n".join(
            [
                "Alternatives to inspect",
                (
                    "- top20_scores.csv peut être consulté pour comparer des compromis calculés "
                    f"({top_20_count} candidat(s) dans le payload courant)."
                ),
                "- Ce fichier ne choisit pas automatiquement une meilleure alternative physique.",
            ]
        ),
        "\n".join(
            [
                "What to try next",
                "- Modifier un seul axe à la fois, relancer, puis comparer score, warnings, f0, model_confidence et alternatives.",
                "- Éviter de lire ce run comme prescription de fabrication.",
            ]
        ),
        "\n".join(["Raw outputs guide", *_raw_output_lines(exports_map)]),
    ]
    return "\n\n".join(sections)


def _display(value: Any) -> str:
    if value is None:
        return "non renseigné"
    return str(value)


def _format_number(value: Any) -> str:
    if value is None:
        return "non renseigné"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0.0 else None


def _format_hz(value: float) -> str:
    return f"{value:.0f} Hz"


def _validity_band_line(features: Mapping[str, Any], *, prefix: str = 'Bande de prudence 1D estimée') -> str | None:
    conf_085 = _positive_float(features.get('model_validity_band_conf_085_hz'))
    conf_070 = _positive_float(features.get('model_validity_band_conf_070_hz'))
    if conf_085 is None or conf_070 is None:
        return None
    return f"{prefix} : confiance ≥0.85 jusqu’à ~{_format_hz(conf_085)}, ≥0.70 jusqu’à ~{_format_hz(conf_070)}"


def _model_validity_output_lines(features: Mapping[str, Any]) -> list[str]:
    fields = [
        "model_validity_f10_hz",
        "model_validity_band_conf_085_hz",
        "model_validity_band_conf_070_hz",
        "model_validity_band_conf_060_hz",
    ]
    return [f"- {field} : {_format_number(features.get(field))}" for field in fields if _positive_float(features.get(field)) is not None]


def _phase_line(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "non présente"
    data = dict(value)
    parts: list[str] = ["présente"]
    if "evaluation_count" in data:
        parts.append(f"evaluation_count={_display(data.get('evaluation_count'))}")
    if "selected_count" in data:
        parts.append(f"selected_count={_display(data.get('selected_count'))}")
    if isinstance(data.get("ranked"), Sequence):
        parts.append(f"ranked_count={len(data.get('ranked') or [])}")
    return ", ".join(parts)


def _export_summary(exports: Mapping[str, Any]) -> str:
    keys = [key for key in ("summary_json", "summary_yaml", "top20_csv", "pareto_plot", "best_design_bundle") if key in exports]
    return ", ".join(keys) if keys else "aucun export principal annoncé"


def _list_summary(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "aucun warning annoncé"


def _raw_output_lines(exports: Mapping[str, Any]) -> list[str]:
    if not exports:
        return ["- Aucun export annoncé dans le payload courant."]

    lines: list[str] = []
    labels = {
        "summary_json": "optimizer_summary.json",
        "summary_yaml": "optimizer_summary.yaml",
        "top20_csv": "top20_scores.csv",
        "pareto_plot": "pareto_overview.png",
    }
    for key, label in labels.items():
        if key in exports:
            lines.append(f"- {label} : {exports[key]}")

    bundle = dict(exports.get('best_design_bundle', {}) or {})
    bundle_labels = {
        "summary_txt": "best_design_summary.txt",
        "result_json": "best_design_result.json",
        "result_yaml": "best_design_result.yaml",
        "impedance_plot": "best_design_impedance.png",
        "radiation_plot": "best_design_radiation.png",
    }
    for key, label in bundle_labels.items():
        if key in bundle:
            lines.append(f"- {label} : {bundle[key]}")

    return lines or ["- Aucun export brut principal annoncé dans le payload courant."]
