# MODULE_TODOS_ULTRA_CONCRETS_V1.md

## Bloc 1 — materials
### `materials/models.py`
- `AcousticParameter`
- `MaterialVariant`
- `Material`

### `materials/database.py`
- `MaterialDatabase.from_yaml(path)`
- `get(material_id)`
- `list_ids()`
- `filter_allowed(ids)`
- validations : ids uniques, plages cohérentes, champs présents

### `materials/variants.py`
- `generate_variant(...)`
- `apply_modifiers(...)`
- `validate_variant(...)`
- `scale_parameter(...)`

### `materials/uncertainty.py`
- `sample_parameters(material, n)`
- `calibration_priority_score(material, sensitivities)`

## Bloc 2 — acoustics linéaire
### `acoustics/losses.py`
- `effective_beta(material)`
- `attenuation_alpha(omega, diameter_m, material)`
- `complex_wavenumber(omega, diameter_m, material, air)`

### `acoustics/radiation.py`
- `end_correction_m(radius_m)`
- `radiation_impedance(omega, radius_m, air)`
- `radiation_proxy_metrics(freq_hz, zr, bands=None)`

### `acoustics/transfer_matrix.py`
- `area_from_diameter(diameter_m)`
- `characteristic_impedance(rho, c, area_m2)`
- `segment_matrix(...)`
- `propagate_impedance_uniform_segment(z_load, zc, k, length_m)`
- `input_impedance(...)`

### `acoustics/peaks.py`
- `find_peaks(freq_hz, zin_mag, config)`
- `peak_width_half_height(freq_hz, zin_mag, peak_index)`
- `estimate_q(freq_hz, zin_mag, peak_index)`

### `acoustics/features.py`
- `extract(...)`
- `first_playable_peak(peaks)`
- `harmonicity_error(peaks, f0_hz)`
- `odd_only_score(peaks, f0_hz)`
- `local_slope(...)`
- `band_statistics(...)`

### `optimization/objectives.py`
- `score_objectives(features, design, config)`
- `hard_constraints_ok(features, design, config)`
- `penalties(design, features, config)`
- `aggregate_score(objective_scores, penalties, config)`

### `pipeline/evaluate_linear.py`
- `evaluate(design, config)`

## Bloc 3 — validation
- `tests/validation_cases.py`
- `tests/validation_runner.py`
- Cas A–E

## Bloc 4 — optimisation
### `optimization/runtime_estimator.py`
- `analytical_estimate(config)`
- `benchmark(config, linear_pipeline)`
- `combined_estimate(config, linear_pipeline)`

### `optimization/search_space.py`
- `sample_random()`
- `mutate(genome)`
- `crossover(genome_a, genome_b)`
- `decode(genome)`
- `repair_genome(genome)`
- `is_valid_genome(genome)`

### `optimization/pareto.py`
- `run()`
- `initialize_population()`
- `evaluate_population(population)`
- `dominates(a, b)`
- `pareto_front(evaluated)`
- `next_generation(evaluated)`

### `optimization/selector.py`
- `select_best(candidates, method, config)`
- `rank_top_n(candidates, n, method, config)`

## Bloc 5 — reporting
- `reporting/ranking.py`
- `reporting/summaries.py`
- `reporting/export.py`
- `reporting/plots.py`

## Bloc 6 — robustesse
- `player/robustness.py`
- `pipeline/evaluate_robustness.py`

## Bloc 7 — non-linéaire
- `nonlinear/lips.py`
- `nonlinear/resonator_td.py`
- `nonlinear/thresholds.py`
- `nonlinear/regimes.py`
- `pipeline/evaluate_nonlinear.py`

## Bloc 8 — intégration
- `pipeline/run_optimizer.py`
