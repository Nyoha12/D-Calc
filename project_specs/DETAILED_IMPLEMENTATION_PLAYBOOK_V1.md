# DETAILED_IMPLEMENTATION_PLAYBOOK_V1.md

## Statut
Ce fichier sert à conserver le niveau **ultra concret** produit dans la conversation, au-delà des résumés plus compacts de :
- `IMPLEMENTATION_SPRINTS_V1.md`
- `MODULE_TODOS_ULTRA_CONCRETS_V1.md`
- `VALIDATION_BENCH_AE_V1.md`

Il ne remplace pas les fichiers centraux ; il les complète.

## Références centrales
- `PROGRAM_SPEC_V1.md` : source de vérité actuelle pour le pipeline complet
- `PHYSICS_AND_METRICS.md` : source de vérité actuelle pour le noyau physique
- `MATERIALS_POLICY_AND_UNCERTAINTY.md` : source de vérité actuelle pour l’incertitude
- `CONFIG_TEMPLATE_V1.yaml` : version de travail pour les entrées/config
- `materials_base_v1.yaml` : base V1 exploitable mais partiellement provisoire
- `wood_variant_rules_v1.yaml` : règles de variants bois
- `deep-research-report.md` : base physique/recommandations/cas de référence

---

## 1. Priorité absolue
La prochaine conversation doit **commencer directement par le MVP linéaire**.
Ordre recommandé :

1. `materials/models.py`
2. `materials/database.py`
3. `materials/variants.py`
4. `materials/uncertainty.py`
5. `geometry/models.py`
6. `geometry/builders.py`
7. `geometry/discretization.py`
8. `geometry/constraints.py`
9. `acoustics/air.py`
10. `acoustics/losses.py`
11. `acoustics/radiation.py`
12. `acoustics/transfer_matrix.py`
13. `acoustics/peaks.py`
14. `acoustics/features.py`
15. `optimization/objectives.py`
16. `pipeline/evaluate_linear.py`

Ne pas repartir en conception abstraite si ce bloc n’a pas démarré.

---

## 2. Bloc `materials` — détail

### `materials/models.py`
Dataclasses à écrire :
- `AcousticParameter(nominal, min, max, status, confidence)`
- `MaterialVariant(humidity_state, finish, grade, knot_class, density_class)`
- `Material(id, base_material, family, subtype, variant, beta, porosity_leak, wall_loss, manufacturability, cost_level, mass_level, recommended_for_mouthpiece, recommended_for_body, recommended_for_bell, source_status, confidence_overall, research_priority, notes="")`

### `materials/database.py`
Fonctions :
- `MaterialDatabase.from_yaml(path)`
- `get(material_id)`
- `list_ids()`
- `filter_allowed(ids)`

Validations :
- ids uniques
- champs présents
- `min <= nominal <= max`
- booléens `recommended_for_*`

### `materials/variants.py`
Fonctions :
- `generate_variant(...)`
- `apply_modifiers(...)`
- `validate_variant(...)`
- `scale_parameter(param, factor, widen_factor=1.0)`

Règles :
- modifier `nominal`, `min`, `max`
- ne pas écraser `status`, `confidence`, `research_priority`

Tests clefs :
- `birch + humid + raw` plus dissipatif que `birch + airdry + raw`
- `birch + epoxy_lined` moins dissipatif que `birch + raw`
- variant bois sur `pvc_pressure` proprement ignoré/rejeté

### `materials/uncertainty.py`
Fonctions :
- `sample_parameters(material, n)`
- `calibration_priority_score(material, sensitivities)`

Règle :
- priorité ≈ incertitude relative × sensibilité

---

## 3. Bloc `geometry` — détail

### `geometry/models.py`
- `Segment(kind, length_cm, d_in_cm, d_out_cm, material_id, profile_params=None, position_start_cm=0.0, position_end_cm=0.0)`
- `Design(id, segments, metadata)`

### `geometry/builders.py`
- `build(genome)`
- `assign_positions(design)`
- `total_length_cm(design)`

### `geometry/discretization.py`
- `discretize(design, max_segment_cm=1.0)`

Règle :
- cônes et cloches doivent être transformés en petits segments uniformes avant le TMM

### `geometry/constraints.py`
- `validate(design, config)` -> liste erreurs
- `soft_penalties(design, config)` -> dict pénalités

---

## 4. Bloc `acoustics` — détail

### `acoustics/air.py`
- `AirProperties(rho, c, temperature_c, humidity_percent)`

### `acoustics/losses.py`
Fonctions :
- `effective_beta(material)`
- `attenuation_alpha(omega, diameter_m, material)`
- `complex_wavenumber(omega, diameter_m, material, air)`

Formule MVP :
- `alpha = 1e-5 * beta * sqrt(omega) / d`
- `alpha_eff = alpha * (1 + wall_loss + porosity_leak)`
- `k = omega/c - 1j*alpha_eff`

Tests :
- diamètre ↑ ⇒ alpha ↓
- fréquence ↑ ⇒ alpha ↑
- matériau plus dissipatif ⇒ alpha ↑
- aucun NaN à `omega=0`

### `acoustics/radiation.py`
Fonctions :
- `end_correction_m(radius_m)`
- `radiation_impedance(omega, radius_m, air)`
- `radiation_proxy_metrics(freq_hz, zr, bands=None)`

Formule MVP :
- `DeltaL = 0.613 * a`
- `Zr ≈ Zc * ((ka)^2 / 4 + 1j * k * DeltaL)`

Tests :
- rayon ↑ ⇒ `DeltaL` ↑
- `Re(Zr) >= 0`
- grande sortie ⇒ proxy radiation plus fort

### `acoustics/transfer_matrix.py`
Fonctions :
- `area_from_diameter(diameter_m)`
- `characteristic_impedance(rho, c, area_m2)`
- `segment_matrix(...)`
- `propagate_impedance_uniform_segment(z_load, zc, k, length_m)`
- `input_impedance(...)`

Formules MVP :
- `Zc = rho*c/S`
- propagation d’impédance :
  `Zin = Zc * (1j*tan(kL) + Zload/Zc) / (1 + 1j*(Zload/Zc)*tan(kL))`

Test analytique obligatoire :
- cylindre uniforme contre
  `Zin = Zc * (ZL + 1j*Zc*tan(kL)) / (Zc + 1j*ZL*tan(kL))`

### `acoustics/peaks.py`
Fonctions :
- `find_peaks(freq_hz, zin_mag, config)`
- `peak_width_half_height(...)`
- `estimate_q(...)`

Sortie de pic :
- `index`
- `frequency_hz`
- `magnitude`
- `q`
- `left_hz`
- `right_hz`
- `prominence`

### `acoustics/features.py`
Fonctions :
- `extract(...)`
- `first_playable_peak(peaks)`
- `harmonicity_error(peaks, f0_hz)`
- `odd_only_score(peaks, f0_hz)`
- `local_slope(...)`
- `band_statistics(...)`

Features MVP :
- `f0_hz`
- `fundamental_peak_magnitude`
- `fundamental_q`
- `peak_count`
- `peaks`
- `harmonicity_error`
- `odd_only_score`
- `backpressure_proxy`
- `band_stats`
- `brightness_proxy`
- `toot_ratio`
- `toot_quality`
- `model_confidence`

Placeholders propres :
- `vocal_control_proxy`
- `transient_proxy`

---

## 5. `optimization/objectives.py` — détail

Fonctions :
- `score_objectives(features, design, config)`
- `hard_constraints_ok(features, design, config)`
- `penalties(design, features, config)`
- `aggregate_score(objective_scores, penalties, config)`

Objectifs MVP à brancher :
- `drone_f0`
- `impedance_peaks`
- `peak_quality_Q`
- `harmonicity`
- `backpressure`
- `radiation_brightness`
- `toot`
- `fabrication_simplicity`
- `material_simplicity`

Pénalités MVP :
- `segment_count_penalty`
- `material_change_penalty`
- `topology_penalty`
- `low_confidence_penalty`
- `geometry_soft_penalty`

---

## 6. `pipeline/evaluate_linear.py` — détail

Ordre exact :
1. valider géométrie
2. discrétiser
3. construire grille fréquentielle
4. calculer `zin`
5. calculer `zin_mag`
6. statistiques de bandes
7. extraire pics
8. extraire features
9. vérifier contraintes dures
10. scorer objectifs + pénalités
11. retourner résultat structuré

Structure de sortie :
- `design_id`
- `design`
- `valid`
- `errors`
- `warnings`
- `freq_hz`
- `zin`
- `zin_mag`
- `peaks`
- `features`
- `objective_scores`
- `penalties`
- `aggregate_score`

Warnings MVP :
- `low_model_confidence`
- `few_detected_peaks`
- `high_losses_material`
- `large_bell_may_reduce_1d_validity`
- `placeholder_feature_used`

---

## 7. Banc de validation A–E — détail

### Cas A — cylindre de référence
- 140 cm
- 3.8 cm
- `pvc_pressure`
Variantes :
- longer 160 cm
- shorter 120 cm
- narrower 3.2 cm
- wider 4.5 cm

Assertions minimales :
- `f0_hz` existe
- `peak_count > 2`
- plus long => `f0_hz` plus bas
- match analytique acceptable sur les premiers pics
- bonne confiance modèle

### Cas B — cône tronqué
- 140 cm
- 3.0 → 7.0 cm
- `pvc_pressure`

Assertions :
- `toot_ratio` existe
- `toot_ratio` dans une plage plausible
- `odd_only_score` et `harmonicity_error` existent
- plusieurs pics exploitables

### Cas C — cylindre + cloche
- cylindre 120 cm @ 3.8 cm
- cloche 20 cm, 3.8 → 12 cm

Assertions :
- brillance plus élevée que sans cloche
- métriques HF plus élevées
- confiance 1D plus basse pour très grande sortie

### Cas D — multi-segments avec marches
- plusieurs segments cylindriques à diamètres alternés

Assertions :
- plusieurs pics exploitables
- différence locale de structure des pics par rapport à une version lissée
- harmonicité différente de la version lissée

### Cas E — irrégulier dissipatif
- profil élargi progressivement, matériau bois plus dissipatif
- variantes : référence plus lisse, version epoxy-linée

Assertions :
- `Q` plus faible sur la version dissipative
- nombre de pics non supérieur à la version lisse
- effet matériaux visible sur la netteté des pics

Tendances physiques obligatoires :
- longueur ↑ ⇒ `f0` ↓
- pertes ↑ ⇒ `Q` ↓
- cloche ↑ ⇒ radiation HF ↑ et confiance 1D ↓ si sortie très large
- cylindre ≈ comportement plus odd-only qu’un cône tronqué
- multi-segments ⇒ sculpture locale de `Z(f)`

---

## 8. `runtime_estimator.py` — détail
Fonctions :
- `analytical_estimate(config)`
- `benchmark(config, linear_pipeline)`
- `combined_estimate(config, linear_pipeline)`

Modèle MVP :
- coût linéaire ∝ `N_eval * N_f * N_seg_eff * C_topology * C_materials`
- coût robustesse ∝ `N_top * N_beginner_expert * N_tract * N_matvar`
- coût non-linéaire ∝ `N_top_nonlinear * pressure_scan_points * sample_rate * duration`

Warnings MVP :
- `high_frequency_grid_cost`
- `many_segments_cost`
- `multi_material_combinatorics_high`
- `topology_complexity_high`
- `nonlinear_cost_high`

---

## 9. `search_space.py` — détail

Fonctions :
- `sample_random()`
- `mutate(genome)`
- `crossover(genome_a, genome_b)`
- `decode(genome)`
- `repair_genome(genome)`
- `is_valid_genome(genome)`

Topologies MVP :
- `cylinder_only`
- `cylinder_plus_bell`
- `truncated_cone`
- `multisegment_body_plus_optional_bell`

Bornes internes prudentes :
- entrée corps : 2.5–5.5 cm
- sortie corps standard : 3.5–10 cm
- sortie cloche standard : 6–20 cm
- sortie cloche >20 cm rare
- diamètres sur grille 0.1 cm

Mutations MVP :
- longueur segment
- diamètre segment
- `cylinder ↔ cone`
- cloche
- matériau
- split/merge segment

---

## 10. `pareto.py` / `selector.py` — détail

### `pareto.py`
Fonctions :
- `run()`
- `initialize_population()`
- `evaluate_population(population)`
- `dominates(a, b)`
- `pareto_front(evaluated)`
- `next_generation(evaluated)`

Règle :
- dominance sur **objectifs actifs normalisés**, pas sur `aggregate_score` seul

Répartition génération suivante :
- 30 % élites
- 40 % mutation
- 20 % crossover
- 10 % random fresh

### `selector.py`
Fonctions :
- `select_best(candidates, method, config)`
- `rank_top_n(candidates, n, method, config)`

Méthodes :
- `weighted_sum`
- `minimax`
- `knee`

---

## 11. `reporting/` — détail

### `ranking.py`
- `rank(candidates, config)`
- `deduplicate(candidates, config)`
- `diversity_key(candidate)`

### `summaries.py`
- `summarize_design(result, language="fr")`
- `strengths(result)`
- `weaknesses(result)`
- `tradeoffs(result)`

### `export.py`
- `export_json(results, path)`
- `export_yaml(results, path)`
- `export_csv_scores(ranked, path)`
- `export_best_design_bundle(best, out_dir)`

### `plots.py`
- `plot_impedance(result, path)`
- `plot_radiation(result, path)`
- `plot_pareto(ranked, path)`

---

## 12. Robustesse — détail

### `player/robustness.py`
Fonctions :
- `evaluate(design_result, config)`
- `sample_scenarios(design_result, config)`
- `evaluate_scenario(design_result, scenario, config)`
- `aggregate_robustness(scenario_results, config)`

Scénarios MVP :
- `nominal`
- `beginner_profile`
- `expert_profile`
- `neutral_tract`
- `tongue_high`
- `tongue_low`
- `humid_materials`
- `dry_materials`
- `epoxy_lined_if_wood`

Sortie :
- `robust_score`
- `score_mean`
- `score_std`
- `valid_fraction`
- `probability_meeting_targets`
- `sensitivity_summary`
- `worst_case_scenario`
- `best_case_scenario`

### `pipeline/evaluate_robustness.py`
- `evaluate(design_result, config)`
- `evaluate_batch(design_results, config)`

---

## 13. Non-linéaire — détail

### `nonlinear/lips.py`
Fonctions :
- `derivatives(t, state, params, p_acoustic)`
- `opening(state)`
- `flow(state, params, p_acoustic, air)`
- `energy_features(state, params)`

Modèle MVP :
- oscillateur 1 ddl + Bernoulli + `h+ = max(h,0)`

### `nonlinear/resonator_td.py`
Fonctions :
- `from_linear_model(design, config, linear_pipeline)`
- `impulse_response()`
- `pressure_from_flow(flow_signal)`
- `reset()`
- `step(u_t)`

Méthode MVP :
- FIR / réponse impulsionnelle dérivée de `Zin(f)`

### `nonlinear/thresholds.py`
Fonctions :
- `estimate_threshold(...)`
- `simulate_at_pressure(...)`
- `onset_detected(...)`

Critères onset :
- `rms_pressure` et `rms_flow` au-dessus d’un seuil
- pas juste un transitoire mourant
- fréquence dominante nette

### `nonlinear/regimes.py`
Fonctions :
- `analyze(simulation_result, config)`
- `detect_stability(signal, fs_hz)`
- `detect_subharmonics(signal, fs_hz, reference_freq_hz=None)`
- `detect_extinction(signal)`
- `detect_regime_switch(signal, fs_hz)`

### `pipeline/evaluate_nonlinear.py`
- `evaluate(design_result, config)`
- `evaluate_batch(design_results, config)`

---

## 14. `pipeline/run_optimizer.py` — détail

Fonctions :
- `run(config_path)`
- `load_context(config_path)`
- `estimate_runtime(config, linear_pipeline)`
- `run_linear_phase(config, context)`
- `run_robustness_phase(ranked_linear, config, context)`
- `run_nonlinear_phase(ranked_robust, config, context)`
- `finalize(linear_results, robust_results, nonlinear_results, runtime_info, config, context)`

Sortie finale :
- `config`
- `runtime_estimate`
- `runtime_actual_seconds`
- `linear_results`
- `robust_results`
- `nonlinear_results`
- `best_design`
- `top_20`
- `warnings`
- `exports`

---

## 15. Ordre de codage concret
1. `materials/*`
2. `geometry/*`
3. `acoustics/losses.py`
4. `acoustics/radiation.py`
5. `acoustics/transfer_matrix.py`
6. `acoustics/peaks.py`
7. `acoustics/features.py`
8. `optimization/objectives.py`
9. `pipeline/evaluate_linear.py`
10. banc A–E
11. `runtime_estimator.py`
12. `search_space.py`
13. `pareto.py`
14. `selector.py`
15. `reporting/*`
16. `robustness`
17. `nonlinear`
18. `run_optimizer.py`

---

## 16. Mise à jour des fichiers du Project
Ne pas mettre à jour les fichiers à chaque tour.
Le prochain vrai jalon qui justifiera une mise à jour du Project est :
- **MVP linéaire fonctionnel**
- **banc A–E stabilisé**

