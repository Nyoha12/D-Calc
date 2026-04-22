# IMPLEMENTATION_SPRINTS_V1.md

## Sprint 0 — socle
- arborescence réelle du package
- chargement config
- chargement matériaux
- tests smoke

## Sprint 1 — moteur linéaire nominal
- `materials/*`
- `geometry/*`
- `acoustics/losses.py`
- `acoustics/radiation.py`
- `acoustics/transfer_matrix.py`
- `acoustics/peaks.py`
- `acoustics/features.py`
- `optimization/objectives.py`
- `pipeline/evaluate_linear.py`

### Critère de fini
Un design simple produit un vrai `Z_in(f)`, des pics/Q, des features MVP et un score linéaire complet.

## Sprint 2 — validation physique A–E
- `tests/validation_cases.py`
- `tests/validation_runner.py`
- Cas A–E

### Critère de fini
Les assertions de tendance physique passent sur A–E.

## Sprint 3 — exploration automatique
- `optimization/runtime_estimator.py`
- `optimization/search_space.py`
- `optimization/pareto.py`
- `optimization/selector.py`

## Sprint 4 — reporting
- `reporting/ranking.py`
- `reporting/summaries.py`
- `reporting/export.py`
- `reporting/plots.py`

## Sprint 5 — robustesse
- `player/models.py`
- `player/vocal_tract.py`
- `player/robustness.py`
- `pipeline/evaluate_robustness.py`

## Sprint 6 — non-linéaire MVP
- `nonlinear/lips.py`
- `nonlinear/resonator_td.py`
- `nonlinear/thresholds.py`
- `nonlinear/regimes.py`
- `pipeline/evaluate_nonlinear.py`

## Sprint 7 — intégration finale
- `pipeline/run_optimizer.py`

## Sprint 8 — calibration ciblée
- sensibilité
- matériaux critiques
- ajustements ciblés de `beta`, `porosity_leak`, `wall_loss`
