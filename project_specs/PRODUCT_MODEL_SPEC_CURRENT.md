# PRODUCT_MODEL_SPEC_CURRENT

## 1. Document status

This document consolidates the current product and model contract for D-Calc.
It is a current-state specification, not a roadmap.

| Claim | Status | Sources |
|---|---|---|
| D-Calc is a didgeridoo optimizer intended to compare, rank, and report parametric didgeridoo designs. | sourced | `README.md`, `project_specs/PROGRAM_SPEC_V1.md` |
| The current code repository is the source of truth for implemented behavior. Generated artifacts and seed manifests are supporting evidence only. | sourced | `README_REPO_SEED.md`, `AGENTS.md` |
| This document should describe user-facing contract, internal implementation, validation/calibration workflow, and open decisions separately. | inferred | Reconciles repository workflow rules with current scattered specs. |
| This document must not imply material promotion, globally established coefficients, or validation truth from artifacts alone. | sourced | `AGENTS.md`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md`, `project_specs/CALIBRATION_PATCH_EXPORT_STATES.md` |

## 2. Program purpose

| Claim | Status | Sources |
|---|---|---|
| The program explores internal didgeridoo geometry, material assignment, acoustic metrics, playability proxies, robustness, and nonlinear MVP refinements. | sourced | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| The current product goal is decision support for optimization work, not a guarantee that generated designs are physically built or globally validated. | inferred | `README.md`, `AGENTS.md`, validation and calibration rules. |
| The primary result is a selected best design plus ranked alternatives and supporting reports. | sourced | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| The intended external audience is not yet explicit. | open decision | Existing files do not state whether the main user is a maker, player, researcher, or developer. |

## 3. Intended user inputs

| Input | Contract | Status | Sources |
|---|---|---|---|
| Optimizer config YAML | User provides or selects a YAML config with project, units, environment, geometry, topology, materials, player, frequency, objectives, optimization, nonlinear, runtime, and reporting sections. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Material database YAML | User/config points to a material database file containing validated material records. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/materials/database.py` |
| Wood variant rules YAML | User/config may point to variant rules; the database loader can auto-load adjacent rules. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/materials/database.py` |
| Output directory | User/config provides `project.output_dir`; the runner resolves it relative to the config file when not absolute. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Fixed design input | Direct design dictionaries or `Design` objects can be evaluated by lower-level pipelines. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/geometry/builders.py` |
| Public CLI contract | A stable user CLI for the full optimizer is not defined; `run_optimizer.py` has a minimal module entry point tied to a default path. | experimental / MVP placeholder | `didgeridoo_optimizer/pipeline/run_optimizer.py` |

## 4. Config schema and stable/experimental fields

| Area | Contract | Status | Sources |
|---|---|---|---|
| Stable current config shape | The current template groups settings under `project`, `units`, `environment`, `geometry_constraints`, `topology`, `mouthpiece`, `bell`, `materials`, `player_model`, `frequency_analysis`, `objectives`, `uncertainty_management`, `optimization`, `runtime_estimation`, `nonlinear_simulation`, and `reporting`. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml` |
| Implemented fields | Geometry constraints, topology bell controls, material paths, allowed materials, objective weights/enabled flags, frequency grid, optimization counts, nonlinear enable/top-N, and reporting save flags are consumed by current code. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/optimization/search_space.py`, `didgeridoo_optimizer/optimization/objectives.py` |
| Partially implemented fields | Some config sections exist in the template but are not fully enforced by the current code, including broader uncertainty management and some player/model fields. | inferred | Template fields exceed direct consumers found in current implementation. |
| Experimental fields | `vocal_control`, `transients_noise`, `nonlinear_threshold`, and `nonlinear_stability` are present in config; only nonlinear scores are populated by the nonlinear MVP, while vocal/transient feature proxies are placeholders. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/acoustics/features.py`, `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Stable-vs-experimental boundary | The repo does not yet define which config fields are a public compatibility promise. | open decision | No dedicated schema/versioning policy found. |

## 5. Design schema

| Claim | Status | Sources |
|---|---|---|
| A design has an `id`, a list of `Segment` objects, and metadata. | sourced | `didgeridoo_optimizer/geometry/models.py` |
| Each segment has `kind`, `length_cm`, `d_in_cm`, `d_out_cm`, `material_id`, optional `profile_params`, and optional position fields. | sourced | `didgeridoo_optimizer/geometry/models.py` |
| Supported segment kinds in the data model are `mouthpiece`, `cylinder`, `cone`, `flare_conical`, `flare_exponential`, `flare_powerlaw`, `branch`, and `helmholtz_neck`. | sourced | `didgeridoo_optimizer/geometry/models.py` |
| The optimizer search genome is an internal representation that is decoded into the design schema. | sourced | `didgeridoo_optimizer/optimization/search_space.py` |
| External fixed-design evaluation is possible at the pipeline level but not specified as a user-facing product workflow. | inferred | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, missing public CLI/API spec. |

## 6. Supported geometries and topologies

| Geometry/topology | Current contract | Status | Sources |
|---|---|---|---|
| Cylinder-only | Generated and evaluated by the search space and linear pipeline. | sourced | `didgeridoo_optimizer/optimization/search_space.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Truncated cone | Generated and evaluated by the search space and linear pipeline. | sourced | `didgeridoo_optimizer/optimization/search_space.py`, `project_specs/04_validation_VALIDATION_BENCH_AE_V1.md` |
| Cylinder plus bell | Generated when bells are allowed; bell kinds map to conical, exponential, and power-law flare segment kinds. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/optimization/search_space.py`, `didgeridoo_optimizer/geometry/constraints.py` |
| Multisegment body plus optional bell | Generated and repaired as a body chain of cylinder/cone segments, optionally with a bell. | sourced | `didgeridoo_optimizer/optimization/search_space.py` |
| Mouthpiece geometry | The model supports a `mouthpiece` segment kind and constraints, but current search generation does not appear to construct mouthpiece segments. | inferred | `didgeridoo_optimizer/geometry/models.py`, `didgeridoo_optimizer/geometry/constraints.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Branches and Helmholtz resonators | Segment kinds exist in the data model, but config disables branches/Helmholtz and the current search space does not generate them. | experimental / MVP placeholder | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/geometry/models.py`, `didgeridoo_optimizer/optimization/search_space.py` |

## 7. Material model

| Claim | Status | Sources |
|---|---|---|
| Material records must include identity, family/subtype, practical suitability, and acoustic model fields. | sourced | `didgeridoo_optimizer/materials/database.py` |
| Acoustic parameters include nominal/min/max/status fields for `beta`, `porosity_leak`, and `wall_loss`. | sourced | `didgeridoo_optimizer/materials/database.py`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Material parameter status distinguishes `sourced`, `inferred`, and `to_calibrate`. | sourced | `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Exact material loss coefficients are initial plausible values unless sourced or calibrated; they must not be presented as globally established by default. | sourced | `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md`, `AGENTS.md` |
| Wood variants can be generated from base material plus encoded variant fields when variant rules are available. | sourced | `didgeridoo_optimizer/materials/database.py`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Calibration patches can clone a material database with modified acoustic values for validation/replay without editing the source material DB. | sourced | `didgeridoo_optimizer/materials/database.py`, `didgeridoo_optimizer/pipeline/run_calibration.py` |

## 8. Linear acoustic model

| Claim | Status | Sources |
|---|---|---|
| The implemented linear model uses a one-dimensional transfer-matrix style propagation through discretized segments. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/transfer_matrix.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Characteristic impedance is computed from air density, sound speed, and bore area. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/transfer_matrix.py` |
| Internal losses use a material-dependent attenuation model based on beta, frequency, diameter, wall loss, and porosity leak. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/losses.py` |
| Radiation load uses a simplified open-end radiation impedance and end correction. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/radiation.py` |
| Linear validity is summarized by a model-confidence score based on transverse cutoff relative to the analysis maximum frequency. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/features.py` |
| The linear model is the main decision surface for current optimization; nonlinear modeling is a later/top-candidate refinement. | sourced | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |

## 9. Metrics and confidence

| Metric/output | Current contract | Status | Sources |
|---|---|---|---|
| `f0_hz` | First detected playable impedance peak. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Peak list and peak count | Extracted from `Z_in` magnitude and passed through reports. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/acoustics/features.py` |
| Fundamental magnitude and Q | Derived from the first detected peak. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Harmonicity and odd-only score | Computed from peak ratios relative to f0. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Backpressure proxy | Currently uses the fundamental peak magnitude. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Brightness/radiation proxy | Uses radiation or high-frequency band statistics depending on available radiation data. | sourced | `didgeridoo_optimizer/acoustics/features.py`, `didgeridoo_optimizer/acoustics/radiation.py` |
| Toot ratio and quality | Uses the ratio between the first two detected peaks. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Vocal-control and transient proxies | Returned as `None` in the current feature extractor. | experimental / MVP placeholder | `didgeridoo_optimizer/acoustics/features.py` |
| Confidence | Current `model_confidence` is a 1D-validity proxy, not an empirical guarantee that the design will perform as predicted. | sourced | `didgeridoo_optimizer/acoustics/features.py`, `AGENTS.md` |

## 10. Optimization objectives

| Claim | Status | Sources |
|---|---|---|
| Enabled objectives are scored independently from extracted features and design properties. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Implemented objective names include `drone_f0`, `impedance_peaks`, `peak_quality_Q`, `harmonicity`, `backpressure`, `radiation_brightness`, `toot`, `fabrication_simplicity`, `material_simplicity`, `beginner_robustness`, and `expert_robustness`. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Unknown enabled objective names receive score `0.0`. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Hard constraints are currently implemented for `drone_f0` and `impedance_peaks` when their config entries enable `hard_constraint`. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Penalties include segment count, material changes, unsupported topology, low confidence, and geometry soft penalties. | sourced | `didgeridoo_optimizer/optimization/objectives.py`, `didgeridoo_optimizer/geometry/constraints.py` |
| Aggregate score is a weighted normalized objective score minus total penalty. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Pareto and final selection behavior exists in code, but this document does not restate it as a public stability guarantee until selector semantics are explicitly versioned. | inferred | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/DETAILED_IMPLEMENTATION_PLAYBOOK_V1.md` |

## 11. Robustness phase

| Claim | Status | Sources |
|---|---|---|
| Robustness is run after linear ranking on a configurable top-N candidate set. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/evaluate_robustness.py` |
| Current robustness scenarios include beginner and expert player profiles, tongue-high and tongue-low vocal tract presets, and material scenarios such as humid, dry, and epoxy-lined-if-wood when material variants are available. | sourced | `didgeridoo_optimizer/player/robustness.py` |
| Robustness output includes score mean/std, valid fraction, probability of meeting targets, sensitivity summary, worst/best scenario, and scenario results. | sourced | `didgeridoo_optimizer/player/robustness.py` |
| The robustness phase updates beginner and expert robustness objective scores before re-ranking. | sourced | `didgeridoo_optimizer/pipeline/evaluate_robustness.py`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| The exact user-facing meaning of `probability_meeting_targets` is not yet specified beyond the internal scenario aggregation. | open decision | No dedicated product definition found. |

## 12. Nonlinear refinement scope

| Claim | Status | Sources |
|---|---|---|
| Nonlinear simulation is a top-candidate refinement after linear and robustness phases when enabled. | sourced | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| The current nonlinear MVP uses a time-domain resonator derived from the linear model, default lip parameters, pressure threshold scanning, and regime analysis. | sourced | `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py`, `didgeridoo_optimizer/nonlinear/*` |
| Nonlinear outputs include threshold pressure, onset status, scan results, simulation pressure, regime metrics, RMS pressure/flow, impulse kernel length, and reference f0. | sourced | `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Nonlinear scores update `nonlinear_threshold` and `nonlinear_stability` objective scores when the phase runs. | sourced | `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| The current nonlinear model is MVP-level and should be treated as advisory unless separately validated for a decision. | experimental / MVP placeholder | `project_specs/PROGRAM_SPEC_V1.md`, `project_specs/PHYSICS_AND_METRICS.md`, `AGENTS.md` |

## 13. Constraints and A-E validation

| Claim | Status | Sources |
|---|---|---|
| Geometry validation enforces configured ranges for total length, body segment count, segment length, diameters, steps, reverse taper, local constrictions/expansions, mouthpiece, and bell. | sourced | `didgeridoo_optimizer/geometry/constraints.py`, `project_specs/CONFIG_TEMPLATE_V1.yaml` |
| Geometry soft penalties are separate from hard validation errors and can affect aggregate score. | sourced | `didgeridoo_optimizer/geometry/constraints.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| The A-E validation bench checks minimum physical trends for the linear model. | sourced | `project_specs/04_validation_VALIDATION_BENCH_AE_V1.md`, `didgeridoo_optimizer/tests/validation_runner.py` |
| A-E cases cover cylinder length/diameter behavior, truncated cone behavior, bell radiation/confidence behavior, multisegment local peak structure, and dissipative material effects. | sourced | `project_specs/04_validation_VALIDATION_BENCH_AE_V1.md`, `didgeridoo_optimizer/tests/validation_runner.py` |
| Passing A-E is validation of the implemented linear trend checks, not proof that any material coefficient or optimized design is globally established. | sourced | `AGENTS.md`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| The exact release gate for A-E after future model changes is not fully specified. | open decision | No dedicated release policy found. |

## 14. Output and report contract

| Output | Current contract | Status | Sources |
|---|---|---|---|
| Python return payload | `run_optimizer.run()` returns config, runtime estimate, actual runtime, linear results, robust results, nonlinear results, best design, top 20, warnings, and export paths. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| JSON/YAML summary | When enabled, final summaries are written as `optimizer_summary.json` and `optimizer_summary.yaml`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| CSV scores | When enabled, ranked top candidates are written to `top20_scores.csv` with score, validity, core features, objective scores, and penalties. | sourced | `didgeridoo_optimizer/reporting/export.py` |
| Plots | When enabled, Pareto overview and best-design impedance/radiation plots are exported. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| Best-design bundle | When a best candidate exists, the exporter writes a French text summary, JSON/YAML result files, and impedance/radiation plots. | sourced | `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/reporting/summaries.py` |
| Full frequency arrays | Full `freq_hz`, `zin`, and `zin_mag` arrays exist in in-memory linear results but are removed from lightened final summary payloads. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Report language | French text summaries are the only implemented summary language. | sourced | `didgeridoo_optimizer/reporting/summaries.py` |
| Public file naming/versioning | Export file names exist, but no versioned report schema is defined. | open decision | No dedicated report schema/version spec found. |

## 15. Calibration and patch workflow

| Claim | Status | Sources |
|---|---|---|
| Calibration patch artifacts must distinguish proposal, replayed patch, accepted patch, and patch-to-calibrate states. | sourced | `project_specs/CALIBRATION_PATCH_EXPORT_STATES.md`, `didgeridoo_optimizer/reporting/patch_exports.py` |
| `materials_patch_suggestions.yaml` is proposal-only and must not be treated as accepted truth. | sourced | `project_specs/CALIBRATION_PATCH_EXPORT_STATES.md`, `AGENTS.md` |
| Current patch-state derivation recognizes replayed patch keys including `patch_replayed`, `directed_patch`, `semidirected_patch`, `family_patch`, `family_multiseed_patch`, and `weighted_patch`. | sourced | `didgeridoo_optimizer/reporting/patch_exports.py` |
| Accepted decisions include `accept_local_only`, `accept_family`, and `accept_weighted`; `keep_as_to_calibrate` maps replayed patch content to the to-calibrate state. | sourced | `didgeridoo_optimizer/reporting/patch_exports.py`, `project_specs/CALIBRATION_PATCH_EXPORT_STATES.md` |
| Calibration artifacts are traceability aids and replay guidance; they are not by themselves product truth or material database promotion. | sourced | `README_REPO_SEED.md`, `AGENTS.md` |
| Material promotion requires explicit future decision and validation evidence; this document performs no promotion. | sourced | `AGENTS.md`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |

## 16. MVP placeholders and unsupported features

| Feature | Current status | Status | Sources |
|---|---|---|---|
| Full optimizer CLI | Minimal module entry point exists, but no stable CLI argument contract is implemented. | experimental / MVP placeholder | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Vocal-control feature | Feature key exists but value is currently `None`. | experimental / MVP placeholder | `didgeridoo_optimizer/acoustics/features.py` |
| Transient/noise feature | Feature key exists but value is currently `None`. | experimental / MVP placeholder | `didgeridoo_optimizer/acoustics/features.py` |
| Branch topology | Segment kind exists, but search/config do not currently enable it as a generated topology. | experimental / MVP placeholder | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/geometry/models.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Helmholtz topology | Segment kind exists, but search/config do not currently enable it as a generated topology. | experimental / MVP placeholder | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/geometry/models.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Nonlinear model | Implemented as MVP refinement; not documented as decision-grade physical validation. | experimental / MVP placeholder | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Material coefficients | Some values are plausible initial values and remain subject to calibration. | sourced | `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Report schema versioning | Current exports exist, but no explicit version field/schema compatibility policy is defined. | open decision | `didgeridoo_optimizer/reporting/export.py` |

## 17. Limitations

| Limitation | Status | Sources |
|---|---|---|
| The main acoustic model is one-dimensional and exposes a confidence proxy when large diameters push against the 1D validity assumption. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/features.py` |
| Current metrics are proxies derived from impedance, radiation, and MVP nonlinear simulation; they are not direct perceptual guarantees. | inferred | `didgeridoo_optimizer/acoustics/features.py`, `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Material uncertainty remains central; calibration is required before treating sensitive coefficients as established. | sourced | `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md`, `AGENTS.md` |
| Results in `results/` can support traceability but must be interpreted with code and replay/validation context. | sourced | `README_REPO_SEED.md`, `AGENTS.md` |
| Unknown enabled objectives score as zero rather than failing fast. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| The product does not currently define user-facing acceptance thresholds for all metrics. | open decision | No dedicated acceptance spec found. |

## 18. Open decisions

| Decision | Why it matters | Status | Candidate sources to reconcile |
|---|---|---|---|
| Define the intended primary user and output language. | Affects report text, terminology, and default workflow. | open decision | `README.md`, `didgeridoo_optimizer/reporting/summaries.py` |
| Define stable public config fields and schema versioning. | Prevents accidental breaking changes as the optimizer evolves. | open decision | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Decide whether to add a stable optimizer CLI contract. | Current module entry point is not sufficient as a user product contract. | open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Decide whether fixed-design evaluation is a supported user workflow. | Current lower-level APIs allow it, but docs do not define it. | open decision | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/geometry/models.py` |
| Define whether nonlinear outputs are advisory or gate-worthy. | Prevents overclaiming MVP nonlinear predictions. | open decision | `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py`, `project_specs/PHYSICS_AND_METRICS.md` |
| Define evidence required for material database promotion. | Prevents local calibration wins from becoming global coefficients without validation. | open decision | `AGENTS.md`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md`, `project_specs/CALIBRATION_PATCH_EXPORT_STATES.md` |
| Decide future status of branch and Helmholtz topologies. | They exist in the data model but are disabled/ungenerated. | open decision | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/geometry/models.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Define report schema/version compatibility. | Existing output files are useful, but downstream users need stable fields. | open decision | `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
