# PRODUCT_MODEL_SPEC_CURRENT

## 1. Document status

This document consolidates the current product and model contract for D-Calc.
It is a current-state specification, not a roadmap. The optimizer, fixed-design
file boundary and separate diagnostics below are implemented interfaces with
distinct schemas; this document does not extend their public stability guarantees.

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
| The optimizer returns a selected best design plus ranked alternatives. Fixed-design returns one evaluated profile; separate diagnostics return impedance/forced-response observables without optimizer selection. | sourced | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| The intended external audience is not yet explicit. | open decision | Existing files do not state whether the main user is a maker, player, researcher, or developer. |

## 3. Intended user inputs

| Input | Contract | Status | Sources |
|---|---|---|---|
| Optimizer config YAML | User provides or selects a YAML config with project, units, environment, geometry, topology, materials, player, frequency, objectives, optimization, nonlinear, runtime, and reporting sections. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Material database YAML | User/config points to material records checked for required fields/shape by the loader. Shape validation does not establish physical coefficients or calibration. | sourced | `didgeridoo_optimizer/materials/database.py`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Wood variant rules YAML | User/config may point to variant rules; the database loader can auto-load adjacent rules. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/materials/database.py` |
| Output directory | User/config provides `project.output_dir`; the runner resolves it relative to the config file when not absolute. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Fixed design input | `python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path> --design <path>` accepts a physical YAML/JSON design through the strict file loader. Direct dictionaries/`Design` objects also remain usable through lower-level pipelines. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/design_input.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Fixed-design execution | Implemented: one linear evaluation with features/scores, separate neutral exports and optional dry-run. No optimization, Pareto, ranking, robustness, nonlinear or runtime estimation, even when configured. | sourced | `didgeridoo_optimizer/pipeline/fixed_design.py`, `didgeridoo_optimizer/reporting/fixed_design.py` |
| CLI contracts | `python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path>` runs the optimizer; adding `--design` switches routes. Both accept `--output-dir` and `--dry-run`. `tools.forced_response_compare` and `tools.thermo_reference_compare` are separate diagnostic CLIs. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `tools/forced_response_compare.py`, `tools/thermo_reference_compare.py`, `project_specs/USER_IO_CONTRACT_CURRENT.md` |

The optimizer dry-run checks config/schema and resolved path existence without
loading the material database or evaluating geometry. Fixed-design dry-run also
loads materials, resolves IDs and validates geometry/analysis inputs. Both avoid
acoustics and output creation. Relative optimizer/fixed output paths resolve from
the config directory, while DESIGN resolves from caller cwd without fallback.
Diagnostic output paths resolve from cwd.
See [USER_IO_CONTRACT_CURRENT](USER_IO_CONTRACT_CURRENT.md) for exact options,
payloads and failure rules, and [README](../README.md) for executable examples.

## 4. Config schema and stable/experimental fields

| Area | Contract | Status | Sources |
|---|---|---|---|
| Current config sections | The template groups settings under `project`, `units`, `environment`, `geometry_constraints`, `topology`, `mouthpiece`, `bell`, `materials`, `player_model`, `frequency_analysis`, `objectives`, `uncertainty_management`, `optimization`, `runtime_estimation`, `nonlinear_simulation` and `reporting`. Presence does not mean every field is consumed or stabilized. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Config schema metadata | The template declares `schema_version: dcalc.optimizer.config.v1`; missing schema versions are accepted as legacy/current v1 and unsupported schema versions fail early. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Implemented fields | Geometry constraints, topology bell controls, material paths, allowed materials, objective weights/enabled flags, frequency grid, optimization counts, nonlinear enable/top-N, and reporting save flags are consumed by current code. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/optimization/search_space.py`, `didgeridoo_optimizer/optimization/objectives.py` |
| Partially implemented fields | Some config sections exist in the template but are not fully enforced by the current code, including broader uncertainty management and some player/model fields. | inferred | Template fields exceed direct consumers found in current implementation. |
| Experimental fields | `vocal_control`, `transients_noise`, `nonlinear_threshold`, and `nonlinear_stability` are present in config; only nonlinear scores are populated by the nonlinear MVP, while vocal/transient feature proxies are placeholders. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/acoustics/features.py`, `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Stable-vs-experimental boundary | A minimal `dcalc.optimizer.config.v1` compatibility policy is documented for schema behavior, path resolution, stable consumed fields, reporting flags, and compatible evolution. Broader config compatibility remains open. | sourced / open decision | `project_specs/USER_IO_CONTRACT_CURRENT.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Material config interpretation | Config fields that select material files or material IDs do not promote material patches, validate coefficients, or make material values globally established. | sourced | `AGENTS.md`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |

## 5. Design schema

| Claim | Status | Sources |
|---|---|---|
| A design has an `id`, a list of `Segment` objects, and metadata. | sourced | `didgeridoo_optimizer/geometry/models.py` |
| Each segment has `kind`, `length_cm`, `d_in_cm`, `d_out_cm`, `material_id`, optional `profile_params`, and optional position fields. | sourced | `didgeridoo_optimizer/geometry/models.py` |
| Supported segment kinds in the data model are `mouthpiece`, `cylinder`, `cone`, `flare_conical`, `flare_exponential`, `flare_powerlaw`, `branch`, and `helmholtz_neck`. | sourced | `didgeridoo_optimizer/geometry/models.py` |
| The optimizer search genome is an internal representation that is decoded into the design schema. | sourced | `didgeridoo_optimizer/optimization/search_space.py` |
| The fixed-design file boundary is implemented: a mapping with optional `id`/`metadata`, nonempty `segments` and no schema-version field; it is distinct from a search genome or optimizer result bundle. | sourced | `didgeridoo_optimizer/pipeline/design_input.py`, `project_specs/FIXED_DESIGN_EVALUATION_WORKFLOW_DRAFT.md` |

At the file boundary, dimensions are finite positive real numbers; material IDs
must resolve; optional positions are checked then recomputed. Unknown structural
fields, duplicate keys, YAML merge directives, nontext mapping keys and cyclic or
non-JSON-compatible metadata are rejected. A truthy `metadata.is_discretized` is
rejected. Physical and discretized analysis designs stay separate in exports.
Only the six implemented linear segment kinds are accepted: branch and Helmholtz
kinds in the internal model do not make them supported file inputs.
See [FIXED workflow](FIXED_DESIGN_EVALUATION_WORKFLOW_DRAFT.md) and
[design_input.py](../didgeridoo_optimizer/pipeline/design_input.py) for profile
parameters, annotation and analysis checks. No broader interchange format is promised.

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
| The material loader checks required identity, family/subtype, practical and acoustic-model fields; this is a structural check, not experimental material validation. | sourced | `didgeridoo_optimizer/materials/database.py` |
| Acoustic parameters include nominal/min/max/status fields for `beta`, `porosity_leak`, and `wall_loss`. | sourced | `didgeridoo_optimizer/materials/database.py`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Material parameter status distinguishes `sourced`, `inferred`, and `to_calibrate`. | sourced | `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Exact material loss coefficients are initial plausible values unless sourced or calibrated; they must not be presented as globally established by default. | sourced | `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md`, `AGENTS.md` |
| Wood variants can be generated from base material plus encoded variant fields when variant rules are available. | sourced | `didgeridoo_optimizer/materials/database.py`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Calibration patches can clone a material database with modified acoustic values for validation/replay without editing the source material DB. | sourced | `didgeridoo_optimizer/materials/database.py`, `didgeridoo_optimizer/pipeline/run_calibration.py` |

## 8. Linear acoustic model

| Claim | Status | Sources |
|---|---|---|
| The implemented linear model uses a one-dimensional transfer-matrix style propagation through discretized segments. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/transfer_matrix.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Legacy nominal characteristic impedance uses air density, sound speed and bore area; its complex correction is supplied by the legacy loss model. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/transfer_matrix.py` |
| Optimizer/fixed-design losses remain legacy beta losses, using frequency, diameter, wall loss and porosity leak. Optional ZK in lower-level APIs/diagnostics does not change that default. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/losses.py` |
| Optimizer/fixed-design radiation remains the simplified legacy open-end load. Its reactance already represents end correction; no duplicate propagation-length extension is added. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/radiation.py` |
| Linear validity is summarized by a model-confidence score based on transverse cutoff relative to the analysis maximum frequency. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/features.py` |
| The linear model is the main decision surface for current optimization; nonlinear modeling is a later/top-candidate refinement. | sourced | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |

The physical outlet radius is derived from the validated physical design before
discretization and shared by the solver load and radiation metrics. The last mesh
midpoint and arbitrary metadata do not define the outlet. A marked mesh passed
directly to `input_impedance` requires explicit `exit_radius_m`.
Source: [evaluate_linear.py](../didgeridoo_optimizer/pipeline/evaluate_linear.py),
[transfer_matrix.py](../didgeridoo_optimizer/acoustics/transfer_matrix.py) and
[PHYS_REF_01](PHYS_REF_01_BOUNDARY_REFERENCE.md).

Separate reference choices are implemented, with bounded numerical contracts:

| Option | Current scope and interpretation |
|---|---|
| ZK losses | `input_impedance(..., loss_model=...)` and forced-response APIs accept an explicit `ZwikkerKostenLossModel`. The diagnostic `--loss-model zk/both` requires explicit dry nominal CK20/CK25 air; original config and effective air remain separate. Rigid, smooth, sealed-wall thermoviscous reference; material beta/wall/porosity effects are omitted, not measured zero. |
| Silva radiation | `input_impedance(..., radiation_model=...)` and `loaded_transfer` accept a model; forced-response CLI selects `legacy`, `silva_unflanged` or `silva_flanged`. Silva is a published numerical cylindrical fit (thin sharp unflanged edge or infinite planar flange), not a validated bell model. |
| Radiation status | Silva reports extrapolation beyond its reference band `abs(ka) <= 2`, without substituting legacy. Model-domain status is separate from numerical availability. Legacy remains a low-frequency asymptote with no invented universal accuracy cutoff. |
| Activation boundary | `loss_model=None` and `radiation_model=None` retain legacy behavior in the low-level solver. `LinearEvaluationPipeline` supplies neither option: neither optimizer nor fixed-design config activates these diagnostic models. No global default is mutated. |

The `component_v2_experimental` loss adapter in
[losses.py](../didgeridoo_optimizer/acoustics/losses.py) remains an internal
placeholder, not a calibrated component split or a public config option.

Detailed assumptions/equations belong in
[THERMO_01_ZK_REFERENCE](THERMO_01_ZK_REFERENCE.md) and
[RADIATION_01_REFERENCE](RADIATION_01_REFERENCE.md).
The radiation note's initial blocked-run account is historical; the later
[IO_N2_REFERENCE](IO_N2_REFERENCE.md) describes the integrated roundoff correction.
Those notes' recorded executions are not new tests of this documentation change.

## 9. Metrics and confidence

| Metric/output | Current contract | Status | Sources |
|---|---|---|---|
| `f0_hz` | First detected impedance peak in the configured analysis; not a measured playing fundamental. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Peak list and peak count | Extracted from `Z_in` magnitude and passed through reports. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/acoustics/features.py` |
| Fundamental magnitude and Q | Derived from the first detected peak. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Harmonicity and odd-only score | Computed from peak ratios relative to f0. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Backpressure proxy | Currently uses the fundamental peak magnitude. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Brightness/radiation proxy | Uses radiation or band statistics depending on available data. `exit_hf_radiation_proxy` uses the mean high-frequency real radiation admittance in the current pipeline; it is not a loaded whole-instrument transfer, played brightness or measured acoustic efficiency. | sourced | `didgeridoo_optimizer/acoustics/features.py`, `didgeridoo_optimizer/acoustics/radiation.py`, `didgeridoo_optimizer/reporting/summaries.py` |
| Toot ratio and quality | Uses the ratio between the first two detected peaks. | sourced | `didgeridoo_optimizer/acoustics/features.py` |
| Vocal-control and transient proxies | Returned as `None` in the current feature extractor. | experimental / MVP placeholder | `didgeridoo_optimizer/acoustics/features.py` |
| Confidence | Current `model_confidence` is a 1D-validity proxy, not an empirical guarantee that the design will perform as predicted. | sourced | `didgeridoo_optimizer/acoustics/features.py`, `AGENTS.md` |

The separate forced-response diagnostic supplies loaded `Zin/Yin/Hu/Yt/Zt/Hp`,
peak `p1/U1/p2/U2` and per-frequency `Pin/Pload/Pdiss/eta` for an explicit
pressure or volume-flow source. These are not added to optimizer objectives.
`eta` is an acoustic power ratio, not player efficiency; no played FFT, static
blowing pressure, distant SPL or broadband power sum is inferred.
Null values have aligned statuses/reasons; export success does not imply every
observable is numerically resolved. IO-N2 roundoff bounds apply at fixed mesh,
separately from spatial error and experimental/material uncertainty.
See [FORCED_RESPONSE_01](FORCED_RESPONSE_01.md),
[IO_N2_REFERENCE](IO_N2_REFERENCE.md) and
[forced-response exporter](../didgeridoo_optimizer/reporting/forced_response.py).

## 10. Optimization objectives

| Claim | Status | Sources |
|---|---|---|
| Enabled supported linear objectives are computed from features/design properties. Aggregate and selector participation additionally require a positive weight (default 1); an emitted score alone does not activate an objective. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Implemented objective names include `drone_f0`, `impedance_peaks`, `peak_quality_Q`, `harmonicity`, `backpressure`, `radiation_brightness`, `exit_hf_radiation`, `toot`, `fabrication_simplicity`, `material_simplicity`, `beginner_robustness`, and `expert_robustness`. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Unknown enabled names are omitted from scoring and selector dimensions; the optimizer emits `unknown_enabled_objective:<name>` in dry-run/final warnings. They do not contribute a zero or denominator weight. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Hard constraints are implemented for `drone_f0` and `impedance_peaks` when both `enabled` and `hard_constraint` are true, independently of the objective weight. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Penalties include segment count, material changes, unsupported topology, low confidence, and geometry soft penalties. | sourced | `didgeridoo_optimizer/optimization/objectives.py`, `didgeridoo_optimizer/geometry/constraints.py` |
| Aggregate score is the weighted mean over active scores actually present, minus total penalty. Missing scores contribute no denominator weight; with none active/present, only the negative penalty remains. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| Pareto and final selection behavior exists in code, but this document does not restate it as a public stability guarantee until selector semantics are explicitly versioned. | inferred | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/DETAILED_IMPLEMENTATION_PLAYBOOK_V1.md` |

Activation is shared by aggregate scoring, Pareto candidate construction and the
final selector; selector methods include `weighted_sum`, `minimax` and the
default knee path. This describes implemented behavior, not a new compatibility
guarantee for nested ranking algorithms.
Sources: [objectives.py](../didgeridoo_optimizer/optimization/objectives.py),
[pareto.py](../didgeridoo_optimizer/optimization/pareto.py),
[selector.py](../didgeridoo_optimizer/optimization/selector.py),
[test_objective_activation.py](../didgeridoo_optimizer/tests/test_objective_activation.py).
Fixed-design preserves the same linear scores, but currently does not append the
optimizer's unknown-objective config warnings.

## 11. Robustness phase

| Claim | Status | Sources |
|---|---|---|
| Robustness is run after linear ranking on a configurable top-N candidate set. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/evaluate_robustness.py` |
| Current robustness scenarios include beginner and expert player profiles, tongue-high and tongue-low vocal tract presets, and material scenarios such as humid, dry, and epoxy-lined-if-wood when material variants are available. | sourced | `didgeridoo_optimizer/player/robustness.py` |
| Robustness output includes score mean/std, valid fraction, probability of meeting targets, sensitivity summary, worst/best scenario, and scenario results. | sourced | `didgeridoo_optimizer/player/robustness.py` |
| The robustness phase emits beginner/expert robustness scores before re-ranking; absent, disabled or nonpositive-weight objectives remain diagnostic and do not enter aggregation or selector dimensions. | sourced | `didgeridoo_optimizer/pipeline/evaluate_robustness.py`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| The exact user-facing meaning of `probability_meeting_targets` is not yet specified beyond the internal scenario aggregation. | open decision | No dedicated product definition found. |

## 12. Nonlinear refinement scope

| Claim | Status | Sources |
|---|---|---|
| Nonlinear simulation is a top-candidate refinement after linear and robustness phases when enabled. | sourced | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| The current nonlinear MVP uses a time-domain resonator derived from the linear model, default lip parameters, pressure threshold scanning, and regime analysis. | sourced | `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py`, `didgeridoo_optimizer/nonlinear/*` |
| Nonlinear outputs include threshold pressure, onset status, scan results, simulation pressure, regime metrics, RMS pressure/flow, impulse kernel length, and reference f0. | sourced | `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| The nonlinear phase emits supported `nonlinear_threshold` and `nonlinear_stability` scores when it runs. They affect aggregation/selector dimensions only when configured enabled with positive weight; they are not populated by the linear-only fixed-design route. | sourced | `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
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

Numerical boundary, analytic reference, convergence, strict-input and availability
tests establish contracts within their stated domains. They do not establish
player performance or experimentally calibrated coefficients. A shape-validated
material field, `geometry_valid`, model `valid`, numerical availability and
physical validation are distinct statements.

## 14. Output and report contract

| Output | Current contract | Status | Sources |
|---|---|---|---|
| Python return payload | `run_optimizer.run()` returns optimizer report schema metadata, config schema metadata, config, runtime estimate, actual runtime, linear results, robust results, nonlinear results, best design, top 20, warnings, and export paths. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| JSON/YAML summary | When enabled, final summaries are written as `optimizer_summary.json` and `optimizer_summary.yaml` with `schema_version: dcalc.optimizer.report.v1`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| CSV scores | When enabled, ranked top candidates are written to `top20_scores.csv` with score, validity, core features, objective scores, and penalties. | sourced | `didgeridoo_optimizer/reporting/export.py` |
| Pareto plot | When `reporting.save_plots` is enabled, the exporter writes `pareto_overview.png`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/plots.py` |
| Best-design bundle | When a best candidate exists, the exporter writes `best_design/best_design_summary.txt`, `best_design/best_design_result.json`, `best_design/best_design_result.yaml` and, when `reporting.save_best_design_plots` is true, `best_design/best_design_impedance.png` and `best_design/best_design_radiation.png`. The best-design impedance/radiation plots are currently part of the bundle and are not controlled by `reporting.save_plots`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/reporting/summaries.py` |
| Best-design plot control | `reporting.save_best_design_plots` defaults to `true` and controls only `best_design_impedance.png` and `best_design_radiation.png`. It does not change `reporting.save_plots`, and it does not disable the full `best_design/` bundle. A broader `save_best_design_bundle` control is not recommended for now. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Full frequency arrays | Full `freq_hz`, `zin`, and `zin_mag` arrays exist in in-memory linear results but are removed from lightened final summary payloads. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Nested report details | Fine structure of linear, robustness, nonlinear, `best_design`, and `top_20` payloads remains advisory/internal unless stabilized by a future schema decision. | inferred / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/USER_IO_CONTRACT_CURRENT.md` |
| Report language | Optimizer summaries/interpretation and forced-response summaries use French; the fixed-design summary currently uses English. No general report localization API is promised. | sourced | `didgeridoo_optimizer/reporting/summaries.py`, `didgeridoo_optimizer/reporting/fixed_design.py`, `didgeridoo_optimizer/reporting/forced_response.py` |
| Public file naming/versioning | Export file names exist and optimizer summaries include `dcalc.optimizer.report.v1` metadata. A minimal report v1 compatibility policy is documented, while broader compatibility and evolution rules remain open. | sourced / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/USER_IO_CONTRACT_CURRENT.md` |

Additional outputs and separate schemas:

| Workflow | Schema / outputs / controls |
|---|---|
| Optimizer interpretation / timing | Successful finalization always writes `post_run_interpretation.txt` (`exports.interpretation_txt`). `runtime_actual_seconds` covers estimation/calculation phases; optional `runtime_wall_seconds` is sampled during export before interpretation/summary writes, not at process exit. |
| Optimizer CLI | `schema_version: dcalc.optimizer.cli.v1`, `payload_type: dry_run` or `run_summary`; distinct from the full `dcalc.optimizer.report.v1` payload. |
| Fixed-design | CLI/bundle `schema_version: dcalc.fixed_design.result.v1`, `workflow: linear_fixed_design`. Exactly `evaluated_design_result.json`, `evaluated_design_result.yaml`, `evaluated_design_summary.txt`, independently of optimizer reporting flags. |
| Fixed-design content | Full linear `result` with physical/analysis designs, aligned curves and complex `{real, imag}` samples; config/schema metadata, effective parameters, material records/statuses, input fingerprints and software provenance, warnings, `not_executed`, `unavailable_values`. Compact CLI response is separate from this full bundle. |
| Fixed-design validity | `ok` means calculation/export completion; `valid` is the linear API's constraint result and can be false in a successfully exported bundle. Dry-run returns `geometry_valid` without evaluating acoustic constraints. Nonfinite/misaligned curves fail; unavailable ancillary diagnostics are null with reasons. |
| Forced-response | `schema: dcalc.forced_response.v1`; `forced_response.json`, `forced_response.csv`, `forced_response.txt`. Source/load/transfers/ports/powers and numerical statuses, plus separate radiation metadata/statuses. Dry-run stdout currently has no schema marker. No optimizer scoring or peak extraction. |
| THERMO diagnostic | `schema: dcalc.thermo.comparison.v1`; `thermo_comparison.json`, `thermo_comparison.csv`, `thermo_comparison.txt`. Impedance comparison with separate spatial/modal controls; no dry-run or radiation-selection CLI option. Analytical closed-cylinder diagnostic is not a new optimizer termination. |
| Write behavior / provenance | Fixed/diagnostic bundles refuse existing filenames and may leave partial new files on late I/O failure. Optimizer writers can overwrite. Fixed input fingerprints and verified-source Git HEAD/dirty status preserve traceability; unavailable revision is null with reason and dirty HEAD is not exact modified-code identity. |

Sources: [run_optimizer.py](../didgeridoo_optimizer/pipeline/run_optimizer.py),
[fixed-design pipeline](../didgeridoo_optimizer/pipeline/fixed_design.py),
[fixed-design exporter](../didgeridoo_optimizer/reporting/fixed_design.py),
[forced-response CLI](../tools/forced_response_compare.py),
[THERMO CLI](../tools/thermo_reference_compare.py).
See [USER_IO_CONTRACT_CURRENT](USER_IO_CONTRACT_CURRENT.md) for input/path/export
details. A version marker does not freeze every nested field or confer physical validity.

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
| Optimizer and fixed-design CLI | Config-path optimization and strict `--design` linear evaluation are implemented, with output-dir override and distinct dry-run validation. Broader compatibility promises remain open. | sourced / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/USER_IO_CONTRACT_CURRENT.md` |
| Vocal-control feature | Feature key exists but value is currently `None`. | experimental / MVP placeholder | `didgeridoo_optimizer/acoustics/features.py` |
| Transient/noise feature | Feature key exists but value is currently `None`. | experimental / MVP placeholder | `didgeridoo_optimizer/acoustics/features.py` |
| Branch topology | Segment kind exists, but search/config do not currently enable it as a generated topology. | experimental / MVP placeholder | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/geometry/models.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Helmholtz topology | Segment kind exists, but search/config do not currently enable it as a generated topology. | experimental / MVP placeholder | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/geometry/models.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Nonlinear model | Implemented as MVP refinement; not documented as decision-grade physical validation. | experimental / MVP placeholder | `project_specs/PROGRAM_SPEC_V1.md`, `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Material coefficients | Some values are plausible initial values and remain subject to calibration. | sourced | `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |
| Report schema versioning | Optimizer summaries include `schema_version: dcalc.optimizer.report.v1`; a minimal compatibility policy is documented, but nested result details remain advisory/internal and broader compatibility remains open. | sourced / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/USER_IO_CONTRACT_CURRENT.md` |

## 17. Limitations

| Limitation | Status | Sources |
|---|---|---|
| The main acoustic model is one-dimensional and exposes a confidence proxy when large diameters push against the 1D validity assumption. | sourced | `project_specs/PHYSICS_AND_METRICS.md`, `didgeridoo_optimizer/acoustics/features.py` |
| Current metrics are proxies derived from impedance, radiation, and MVP nonlinear simulation; they are not direct perceptual guarantees. | inferred | `didgeridoo_optimizer/acoustics/features.py`, `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Material uncertainty remains central; calibration is required before treating sensitive coefficients as established. | sourced | `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md`, `AGENTS.md` |
| Results in `results/` can support traceability but must be interpreted with code and replay/validation context. | sourced | `README_REPO_SEED.md`, `AGENTS.md` |
| Unknown enabled objectives are ignored with optimizer warnings rather than rejected. Fixed-design does not currently surface these config warnings; this does not make unknown objectives active. | sourced | `didgeridoo_optimizer/optimization/objectives.py` |
| The product does not currently define user-facing acceptance thresholds for all metrics. | open decision | No dedicated acceptance spec found. |

## 18. Open decisions

| Decision | Why it matters | Status | Candidate sources to reconcile |
|---|---|---|---|
| Define the intended primary user and output language. | Affects report text, terminology, and default workflow. | open decision | `README.md`, `didgeridoo_optimizer/reporting/summaries.py` |
| Define broader config compatibility beyond the minimal `dcalc.optimizer.config.v1` policy. | Minimal config v1 compatibility is documented, but some template fields remain advisory/partly implemented or experimental/MVP placeholders. | open decision | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `project_specs/USER_IO_CONTRACT_CURRENT.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Define compatibility beyond the implemented CLI routes. | Optimizer and fixed-design execution/preflight already exist; broader schema evolution guarantees remain open. | open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/USER_IO_CONTRACT_CURRENT.md` |
| Define broader fixed-design/diagnostic compatibility. | Physical DESIGN input and distinct result schemas are implemented; this does not stabilize every lower-level API or make an evaluated design A-E validation, calibration approval or physical proof. | open decision | `didgeridoo_optimizer/pipeline/design_input.py`, `didgeridoo_optimizer/pipeline/fixed_design.py`, `project_specs/USER_IO_CONTRACT_CURRENT.md` |
| Define whether nonlinear outputs are advisory or gate-worthy. | Prevents overclaiming MVP nonlinear predictions. | open decision | `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py`, `project_specs/PHYSICS_AND_METRICS.md` |
| Define evidence required for material database promotion. | Prevents local calibration wins from becoming global coefficients without validation. | open decision | `AGENTS.md`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md`, `project_specs/CALIBRATION_PATCH_EXPORT_STATES.md` |
| Decide future status of branch and Helmholtz topologies. | They exist in the data model but are disabled/ungenerated. | open decision | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/geometry/models.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Define broader report schema compatibility beyond the minimal `dcalc.optimizer.report.v1` policy. | Minimal top-level/file/export-control compatibility is documented; nested result details remain advisory/internal until a future decision stabilizes them. | open decision | `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/USER_IO_CONTRACT_CURRENT.md` |
| Decide whether any broader best-design bundle control is needed. | `reporting.save_best_design_plots` now controls only best-design PNGs; disabling the whole `best_design/` bundle remains not recommended for now. | open decision | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
