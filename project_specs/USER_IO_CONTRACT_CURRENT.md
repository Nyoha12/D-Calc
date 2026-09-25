# USER_IO_CONTRACT_CURRENT

## 1. Document status

This document describes the current user-facing input/output contract for D-Calc.
It is a current-state contract for documentation and planning, not a full schema framework.
Optimizer, fixed-design and diagnostic interfaces below have separate contracts;
their version markers do not add a broad public API stability promise.

| Claim | Status | Sources |
|---|---|---|
| The full optimizer now has a first stable user-facing CLI entry point around the existing Python pipeline. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| The optimizer config, CLI payload, and optimizer report payload now carry minimal schema/version metadata. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| The repo does not yet define a full schema framework or broad backward-compatibility policy. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Generated artifacts and calibration results are not product truth by themselves. | sourced | `AGENTS.md`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

## 2. Current user input contract

| Input | Current contract | Required now | Status | Sources |
|---|---|---|---|---|
| Config path | `python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path>`, `OptimizerRunner.run(config_path)`, and module-level `run(config_path)` read a YAML config from the supplied path. | Yes for full optimizer runs. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Config file format | YAML mapping matching the broad shape of `CONFIG_TEMPLATE_V1.yaml`. | Practically yes, though not enforced by a formal schema. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Config schema version | `schema_version: "dcalc.optimizer.config.v1"` is the current explicit config schema marker. Missing `schema_version` is accepted as legacy/current v1 and reported as `missing_assumed_v1`. Unsupported schema versions fail early. | Optional for backward compatibility; recommended for new configs. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Material DB path | `materials.database_file` is resolved from the config; missing value defaults to `materials_base_v1.yaml`. | Yes: a loadable material DB must exist after resolution. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Variant rules path | `materials.variant_rules_file` is resolved from the config and passed to `MaterialDatabase.from_yaml`. | Optional when variants are not needed; expected by the template. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Output directory | `project.output_dir` is resolved; relative paths are resolved against the config file parent and created if needed. The CLI can override it in memory with `--output-dir <path>`. | Optional in config because the runner defaults to `./results`; CLI override is optional. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Candidate/design input | Without `--design`, the optimizer generates candidates from `SearchSpace`. With `--design <path>`, the same CLI routes to one linear fixed-design evaluation (section 8), bypassing optimizer phases. | Required only for fixed-design mode. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/fixed_design.py` |

Path resolution details:

| Behavior | Status | Sources |
|---|---|---|
| Relative material and variant paths are checked as given, then from the config file parent, then by filename under `/mnt/data`; absolute paths are checked directly. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Relative output directories are resolved relative to the config file parent, not necessarily the process working directory. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| CLI `--output-dir` overrides `project.output_dir` in memory and does not modify the config file. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| `project.overwrite_output_dir` exists in the template but is not currently used by `run_optimizer.py` to delete or replace an output directory. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |

## 3. Config field stability

Current optimizer config fields are best read in three groups. The fixed-design
route reuses the analysis context, not the configured optimizer phases.

| Group | Fields / sections | Contract | Status | Sources |
|---|---|---|---|---|
| Currently consumed | `project.random_seed`, `project.output_dir`, `materials.database_file`, `materials.variant_rules_file`, `materials.allowed_materials`, `materials.max_distinct_materials_per_design`, `geometry_constraints`, `topology.allow_bell`, `topology.allow_bell_types`, `bell.geometry_constraints`, `frequency_analysis`, `objectives`, `optimization`, `nonlinear_simulation.enabled`, `nonlinear_simulation.run_only_for_top_n`, `reporting.save_*`. | Used by current optimizer, search, evaluation, nonlinear, or export code. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Present but partly advisory | `units`, `player_model`, `uncertainty_management`, detailed reporting text settings, some objective metadata such as `direction` and `normalization`. | Present in the template, but not all fields are fully enforced as public behavior. | inferred | Template fields exceed direct consumers in inspected code. |
| Experimental / MVP placeholder | `vocal_control`, `transients_noise`, branch/Helmholtz topology flags, broad nonlinear controls beyond enable/top-N, and broader report/config compatibility beyond the minimal documented policies. | Do not treat as stable user contract yet. | experimental / MVP placeholder | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md`, `project_specs/CONFIG_TEMPLATE_V1.yaml` |

Scoring activation (sourced from `optimization/objectives.py`, `optimization/pareto.py`,
`optimization/selector.py` and `tests/test_objective_activation.py`, under
`didgeridoo_optimizer/`):

- An objective participates in aggregation and selector dimensions only if its name
  is supported, `enabled` is true and its weight is positive (default weight: 1).
  Linear `objective_scores` can still contain an enabled objective with zero weight.
- The aggregate normalizes over active scores actually present, then subtracts
  `total_penalty`. A missing score contributes neither a zero nor denominator weight.
  If none participate, the objective part is zero; penalties still apply.
- Unknown enabled names are omitted, not counted as zero. The optimizer dry-run
  and final warnings include `unknown_enabled_objective:<name>`; fixed-design does
  not currently add those config warnings to its payload.
- Robustness/nonlinear phases may emit diagnostic scores, but presence alone does
  not activate them. Hard constraints are separate: implemented `drone_f0` and
  `impedance_peaks` constraints use `enabled` plus `hard_constraint`, regardless of weight.

Optimizer and fixed-design both call the legacy linear pipeline. No config switch
in these routes activates ZK losses or Silva radiation. The separate diagnostic
options are described in section 8 and [PRODUCT_MODEL_SPEC_CURRENT](PRODUCT_MODEL_SPEC_CURRENT.md).

The minimal `dcalc.optimizer.config.v1` compatibility policy is documented below. Broader compatibility for the full template remains open.

## 4. Config v1 compatibility policy

This is a minimal compatibility policy for `dcalc.optimizer.config.v1`, not a full schema framework and not a promise to stabilize every field in `CONFIG_TEMPLATE_V1.yaml`.

| Area | Current v1 policy | Status | Sources |
|---|---|---|---|
| Schema marker | `schema_version: dcalc.optimizer.config.v1` is the current explicit marker. Explicit v1 is accepted, missing schema is accepted as `missing_assumed_v1`, and unknown schema versions fail early. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Config file shape | Nonempty YAML must be a mapping; an empty/null document is loaded as an empty config. This is not full validation of every template field. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Path resolution | Relative material and variant-rule paths are checked from cwd, then the config file parent, then by filename under `/mnt/data`; absolute paths are checked directly. Relative output directories are resolved from the config file parent. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| CLI output override | `--output-dir <path>` overrides `project.output_dir` in memory and must not mutate the config file. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Current run sections | The current optimizer run consumes `project`, `environment`, `materials`, `geometry_constraints`, `topology`, `bell`, `frequency_analysis`, `objectives`, `optimization`, `runtime_estimation`, `nonlinear_simulation`, and `reporting`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/optimization/search_space.py`, `didgeridoo_optimizer/optimization/objectives.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Stable minimal fields | Stable minimal fields are `project.output_dir`, `project.random_seed`, `materials.database_file`, `materials.variant_rules_file`, `materials.allowed_materials`, `materials.max_distinct_materials_per_design`, consumed air/environment properties, `geometry_constraints`, `topology.allow_bell`, `topology.allow_bell_types`, `bell.geometry_constraints`, `frequency_analysis`, `frequency_analysis.peak_detection`, `objectives.<name>.enabled`, `objectives.<name>.weight`, `objectives.<name>.hard_constraint`, consumed per-objective tuning fields, optimization budgets/counts/top-N/final selector, basic consumed nonlinear fields, and the reporting flags listed below. | sourced / inferred | Same code sources as current run sections. |
| Stable reporting flags | `reporting.save_yaml_summary`, `reporting.save_json_summary`, `reporting.save_csv_scores`, `reporting.save_plots`, and `reporting.save_best_design_plots` keep their documented meanings. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Advisory / partly implemented fields | `project.name`, `project.description`, `project.save_intermediate_results`, `project.overwrite_output_dir`, `units`, `materials.mode`, `materials.assignment_granularity`, material preference/override flags, some `complexity_penalty` fields, `player_model`, `uncertainty_management`, objective metadata such as `direction`, `normalization`, and some `preferred_*` fields, `optimization.strategy`, `optimization.optimizer`, `elite_fraction`, `mutation_rate`, `crossover_rate`, and runtime display flags are not fully stabilized as public behavior. | inferred | Template fields exceed direct consumers in inspected code. |
| Experimental / MVP placeholder fields | Branch topology, Helmholtz topology, mouthpiece generation controls beyond validation of existing segments, `vocal_control`, `transients_noise`, broader nonlinear/detection flags, and any interpretation that treats material coefficients as established truth remain experimental or placeholder. | experimental / MVP placeholder | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md`, `AGENTS.md` |
| Compatible evolution | Compatible v1 changes include adding optional fields or sections, enriching advisory/experimental fields, adding disabled-by-default objectives, adding optional reporting flags with conservative defaults, and improving internal algorithms without changing stable field meanings. | open decision | Minimal v1 policy. |
| New config schema required | A new `schema_version` should be used for removing or renaming a stable field, changing a stable field's type or meaning incompatibly, changing path-resolution semantics, changing the meaning of `reporting.save_*`, making a currently optional/defaulted stable field mandatory, changing budget/top-N/final-selector semantics incompatibly, or turning a placeholder into incompatible public behavior. | open decision | Minimal v1 policy. |
| Material caveat | Stabilizing config field shape does not validate material coefficients, promote material patches, or make configured material values globally established. | sourced | `AGENTS.md`, `project_specs/MATERIALS_POLICY_AND_UNCERTAINTY.md` |

## 5. Current execution interface

| Interface | Current contract | Status | Sources |
|---|---|---|---|
| `didgeridoo_optimizer.pipeline.run_optimizer.run(config_path)` | Main Python function for a complete optimizer run. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `OptimizerRunner` methods | Lower-level Python API for loading context, estimating runtime, running phases, finalizing, and exporting. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path>` | First stable user-facing CLI entry point for a full optimizer run from an explicit config path. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| `--output-dir <path>` | Optional CLI override for the resolved output directory. The override is applied in memory and does not modify the config file. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| `--dry-run` | Without DESIGN: reads config/schema, resolves paths, checks material/rules existence and warns on unknown objectives; it does not load/validate material records, generate geometry, calculate acoustics or create outputs. With DESIGN: additionally loads materials and validates design/analysis as described in section 8. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/fixed_design.py` |
| Full optimizer execution via CLI | Implemented through the CLI wrapper and exercised by a tiny full CLI smoke test that writes artifacts only in a temporary output directory. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Fixed-design CLI | Implemented via `--config <path> --design <path>`, with optional `--dry-run` and `--output-dir`; one linear evaluation only. Python adapter: `run_fixed_design(config_path, design_path, *, dry_run=False, output_dir_override=None)`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/fixed_design.py` |
| CLI payload metadata | Optimizer CLI emits `schema_version: dcalc.optimizer.cli.v1` with `payload_type: dry_run` or `run_summary`. Fixed-design emits `schema_version: dcalc.fixed_design.result.v1` and `workflow: linear_fixed_design`, without optimizer `payload_type`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/fixed_design.py` |
| Report schema versioning | Optimizer summary payloads include `schema_version: dcalc.optimizer.report.v1` plus config schema metadata. A minimal compatibility policy exists for top-level fields, standard output files, and export-control semantics; broader compatibility remains open. | sourced / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

Current contract coverage is specified by `test_run_optimizer_cli.py`,
`test_fixed_design_input.py`, `test_fixed_design_cli.py`,
`test_objective_activation.py`, `test_forced_response_cli.py` and
`test_radiation_response.py` in `didgeridoo_optimizer/tests/`.
This documentation update reads those tests; it does not report a new suite execution.

Remaining CLI and schema decisions:

| Remaining promise / decision | Status |
|---|---|
| Define broader config compatibility beyond the minimal `dcalc.optimizer.config.v1` policy. | open decision |
| Define payload evolution and compatibility expectations beyond the current CLI/report schema metadata. | open decision |
| Decide whether full CLI runs should print resolved config/material/output paths before running, not only in `--dry-run`. | open decision |
| Define broader compatibility for the implemented fixed-design boundary and diagnostic schemas. | open decision |
| Decide whether broader best-design bundle controls are needed beyond the implemented plot toggle. | open decision |

## 6. Output and report contract

The table below covers optimizer exports. Section 8 defines fixed-design and
diagnostic bundles separately.

| Output | Current contract | Controlled by | Status | Sources |
|---|---|---|---|---|
| Optimizer Python return payload | Contains `schema_version: dcalc.optimizer.report.v1`, config schema metadata, `config`, `runtime_estimate`, `runtime_actual_seconds`, `linear_results`, `robust_results`, `nonlinear_results`, `best_design`, `top_20`, `warnings`, and `exports`. | Always returned by `run()`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `optimizer_summary.json` | Lightened final payload without full `freq_hz`, `zin`, or `zin_mag` arrays; includes optimizer report schema metadata. | `reporting.save_json_summary` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| `optimizer_summary.yaml` | YAML form of the same lightened final payload. | `reporting.save_yaml_summary` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| `top20_scores.csv` | Ranked candidates with score, validity, core features, objective scores, and penalties. | `reporting.save_csv_scores` | sourced | `didgeridoo_optimizer/reporting/export.py` |
| `pareto_overview.png` | Pareto plot for ranked candidates. In current code, `reporting.save_plots` controls this overview plot. | `reporting.save_plots` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `best_design/` bundle | Written when a best candidate exists. Files are `best_design_summary.txt`, `best_design_result.json`, `best_design_result.yaml` and, when `reporting.save_best_design_plots` is true, `best_design_impedance.png` and `best_design_radiation.png`. The best-design impedance/radiation plots are currently part of this bundle, not controlled by `reporting.save_plots`. | Best candidate exists; bundle exporter runs. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/reporting/summaries.py` |
| Best-design plot control | `reporting.save_best_design_plots` defaults to `true` and controls only `best_design_impedance.png` and `best_design_radiation.png`. It does not change the meaning of `reporting.save_plots`, and it does not disable the full `best_design/` bundle. | `reporting.save_best_design_plots` | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Warnings | Unknown-objective config warnings, runtime warnings and best-candidate warnings are deduplicated into final optimizer `warnings`. | Internal pipeline behavior. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Schema version | Optimizer summary payloads emit `schema_version: dcalc.optimizer.report.v1`, plus `config_schema_version` and `config_schema_status`; a minimal report v1 compatibility policy is documented below. | Minimal metadata and compatibility policy implemented in docs; broader policy open. | sourced / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |

Additional current optimizer outputs (sourced from `pipeline/run_optimizer.py` and
`reporting/summaries.py`, under `didgeridoo_optimizer/`):

- `post_run_interpretation.txt` is always written on successful finalization and
  announced as `exports.interpretation_txt`, independently of the summary/plot flags.
- `runtime_actual_seconds` measures estimation and calculation phases before
  finalization. Optional `runtime_wall_seconds` starts before context loading and
  is sampled during export, before interpretation and JSON/YAML writes; it is not
  a complete process wall time.
- Optimizer writers can overwrite same-named artifacts; `project.overwrite_output_dir`
  is not an enforced overwrite switch. Fixed-design/diagnostic refusal rules differ.

## 7. Report v1 compatibility policy

This is a minimal compatibility policy for `dcalc.optimizer.report.v1`, not a full schema framework and not a promise to freeze all nested optimizer internals.

| Area | Current v1 policy | Status | Sources |
|---|---|---|---|
| Stable minimal metadata | Reports keep `schema_version` with value `dcalc.optimizer.report.v1`, plus `config_schema_version` and `config_schema_status`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Stable top-level keys | Reports keep `config`, `runtime_estimate`, `runtime_actual_seconds`, `linear_results`, `robust_results`, `nonlinear_results`, `best_design`, `top_20`, `warnings`, and `exports`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Stable basic types | `warnings` is a list, `exports` is a mapping, and `runtime_actual_seconds` is numeric. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Stable standard file names | Current standard outputs are `post_run_interpretation.txt`, `optimizer_summary.json`, `optimizer_summary.yaml`, `top20_scores.csv`, `pareto_overview.png`, `best_design/best_design_summary.txt`, `best_design/best_design_result.json`, `best_design/best_design_result.yaml`, `best_design/best_design_impedance.png`, and `best_design/best_design_radiation.png`, when their controlling conditions are met. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Stable export controls | `reporting.save_json_summary`, `reporting.save_yaml_summary`, `reporting.save_csv_scores`, `reporting.save_plots`, and `reporting.save_best_design_plots` keep their documented meanings. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Stable export truthfulness | `exports` should announce only files that were actually written. | inferred | Successful finalization returns the announced paths after their writers complete. |
| Advisory / internal details | Fine structure of `linear_results`, `robust_results`, `nonlinear_results`, candidates in `best_design` and `top_20`, exact warning text, score/objective internals, runtime-estimate details, and plot pixel contents remain advisory/internal unless a future decision stabilizes them. | inferred / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Compatible evolution | It is compatible to add optional fields, optional output files, new `exports` keys, richer advisory results, additional warnings, or improved payloads as long as stable fields remain present and compatible. | open decision | Minimal v1 policy. |
| New report schema required | A new `schema_version` should be used for removing or renaming a stable field, changing the type or meaning of a stable field, changing standard file names incompatibly, making `exports` stop matching written files, or changing the documented meaning of `best_design`, `top_20`, or export controls. | open decision | Minimal v1 policy. |

Output interpretation rules:

| Rule | Status | Sources |
|---|---|---|
| `best_design` and `top_20` are optimizer selections under the current config and model, not physical guarantees. | inferred | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| `model_confidence` is a 1D validity proxy, not empirical proof of playability or build quality. | sourced | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Optimizer summaries/interpretation and forced-response summaries use French; the fixed-design summary currently uses English. | sourced | `didgeridoo_optimizer/reporting/summaries.py` |

## 8. Fixed-design evaluation and separate diagnostics

### Fixed-design boundary

The implemented route is `python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path> --design <path>`; `run_optimizer.run(config_path)` itself still
means a full optimizer run. The dedicated adapter is in
[pipeline/fixed_design.py](../didgeridoo_optimizer/pipeline/fixed_design.py).
See the [FIXED workflow](FIXED_DESIGN_EVALUATION_WORKFLOW_DRAFT.md) for the detailed
contract and [README examples](../README.md) for copyable commands using the tracked
`project_specs/CONFIG_STARTER_EXAMPLE.yaml`.

| Input / behavior | Implemented contract |
|---|---|
| Physical DESIGN | `.yaml`/`.yml` or `.json` mapping; nonempty `segments`; optional nonempty `id` (default `fixed_design`) and mapping `metadata`. No design-file schema/version field is accepted. |
| Segments | Required `kind`, `length_cm`, `d_in_cm`, `d_out_cm`, `material_id`. Dimensions are finite positive real numbers, not booleans or numeric strings. Material IDs must resolve through the loaded database. |
| Kinds / profiles | `mouthpiece`, `cylinder`, `cone`, `flare_conical`, `flare_exponential`, `flare_powerlaw`. Optional `profile_params` accepts `throat_diameter_cm` for mouthpiece, `flare_parameter` for flares and also `power` for power-law flares; existing geometry policy still applies. Branch/Helmholtz inputs are rejected. |
| Positions / mesh | Optional positions must be finite and are recomputed. Truthy `metadata.is_discretized` is rejected: supply a physical profile, not `analysis_design`. A mesh with removed provenance cannot be reliably recognized. |
| Ambiguity / annotations | Unknown structural fields, duplicate YAML/JSON keys, nontext mapping keys, YAML merge directives and cycles are rejected. Metadata must be finite JSON-compatible annotations; it cannot override the physical outlet. These stricter parsing rules apply to DESIGN, not globally to config/material readers. |
| Paths / materials | DESIGN resolves directly from caller cwd, with no filename fallback. Config/material/output resolution reuses optimizer helpers. Missing optional variant rules warn; unavailable material/variant IDs fail. |
| Analysis checks | Positive finite frequency bounds with max > min, integer `n_points >= 2`, positive finite spatial step, density and sound speed; finite temperature/humidity. Missing values retain the linear API defaults. |
| Dry-run | Loads real materials, resolves IDs and validates geometry/analysis; returns `geometry_valid`, effective parameters and provenance. No acoustics, output directory or artifact. It does not test acoustic hard constraints. |
| Normal run | Calls the linear pipeline once, including its feature extraction, scores, penalties and hard constraints. Exports those results without rescoring. No optimization, Pareto, ranking, robustness, nonlinear or runtime estimation, even if configured. |
| Completion / failure | `ok=true` means calculation/export completed; `valid=false` after acoustic hard constraints is exportable. Input/calculation/serialization/I/O errors give exit 1 and `error:` on stderr. |
| Internal interfaces | Direct `evaluate(design, config, materials)` and search genomes remain lower-level APIs; they are not the strict file boundary or a promised stable interchange format. |

Sources: [design_input.py](../didgeridoo_optimizer/pipeline/design_input.py),
[fixed_design.py](../didgeridoo_optimizer/pipeline/fixed_design.py),
[fixed-design exporter](../didgeridoo_optimizer/reporting/fixed_design.py),
[test_fixed_design_input.py](../didgeridoo_optimizer/tests/test_fixed_design_input.py),
[test_fixed_design_cli.py](../didgeridoo_optimizer/tests/test_fixed_design_cli.py).

The bundle always contains exactly `evaluated_design_result.json`,
`evaluated_design_result.yaml` and `evaluated_design_summary.txt`;
optimizer `reporting.save_*` flags do not control it. CLI and bundle use
`schema_version: dcalc.fixed_design.result.v1`, `workflow: linear_fixed_design`.
The compact CLI response is not the full bundle: the latter includes `result`,
config/schema metadata, effective parameters, `materials_used`, provenance,
warnings, `not_executed` and `unavailable_values`. Physical and analysis designs
remain separate under `result`, alongside full aligned `freq_hz`, `zin` and
`zin_mag`; complex samples use `{real, imag}`. Invalid/nonfinite curves fail.
Other unavailable diagnostics become null with a path-specific reason, not zero.

Provenance records resolved input paths, SHA-256 fingerprints checked across
loading, and verified tracked-source Git HEAD/dirty status when available; otherwise
revision fields are null with a reason. Dirty HEAD does not identify uncommitted
code exactly. Serialization precedes output creation, existing bundle filenames
are refused, and a late I/O failure may leave a partial new bundle.

### Forced-response and THERMO diagnostics

These are separate diagnostic tools, not optimizer phases or new scoring models.
The implemented forced-response command computes responses from a design or
built-in profiles; it is not an offline reader of previous bundles.

| Interface / output | Implemented contract |
|---|---|
| `python -B -m tools.forced_response_compare` | CONFIG+DESIGN together, or `--case cylinder expansion constriction body_bell` (one or more; default cylinder). Exactly one of `--flow-peak-m3-s` or `--pressure-peak-pa`, finite and positive, plus required `--output-dir`. |
| Model selection | `--loss-model legacy\|zk\|both` and `--radiation-model legacy\|silva_unflanged\|silva_flanged`; both defaults are legacy. ZK/both requires `--air-reference ck_dry20\|ck_dry25`. A supplied air reference replaces air in memory for both loss models, preserving original context. One radiation model per invocation. |
| Analysis / paths | `--f-min`, `--f-max`, `--points`, `--h-cm` override effective diagnostic settings only. Built-ins use synthetic materials. CONFIG/DESIGN reuses the strict fixed loader. Required output path resolves from caller cwd, not the config. |
| Dry-run | Validates context/source/grid/geometry and mesh budgets; describes radiation without evaluating it. No propagation, losses, acoustic load calculation or output creation. Its stdout object has `effective`, provenance, segment counts and `not_executed`; it currently has no schema marker. |
| Forced-response exports | `forced_response.json`, `forced_response.csv`, `forced_response.txt`, with `schema: dcalc.forced_response.v1` (not `schema_version`). JSON/CSV retain aligned source, load, transfers, ports, powers, units, logs, statuses and reasons; radiation metadata is separate from the loss `model`. |
| Completion / budgets | `ok` means export completion; `numerically_complete` concerns observable availability, separately from radiation model status. Limits per case/model: 20000 frequencies, 10000 slices, product 2000000. Existing filenames refused; controlled failures return exit 1 with stdout JSON. A late write failure may leave partial new files. |
| `python -B -m tools.thermo_reference_compare` | Separate legacy/ZK impedance diagnostic with explicit dry-air reference, built-ins or CONFIG+DESIGN, spatial/modal controls. No `--dry-run` or radiation-selection CLI option. CONFIG/DESIGN uses the config frequency grid; CLI frequency controls serve built-ins. |
| THERMO exports | `thermo_comparison.json`, `thermo_comparison.csv`, `thermo_comparison.txt`; `schema: dcalc.thermo.comparison.v1`. Caller-cwd output paths, no overwrite. Its analytical closed-cylinder case does not add a closed termination to optimizer/fixed-design. |

Forced-response executes no scoring, ranking, peak extraction, optimization,
robustness, nonlinear simulation or calibration. It exposes loaded transfers
(`Zin`, `Yin`, `Hu`, `Yt`, `Zt`, `Hp`), peak pressure/volume-flow amplitudes and
per-frequency acoustic powers. `eta` is a power ratio, not player efficiency;
there is no played FFT, distant SPL, static blowing pressure or broadband power sum.
Null/status/reason must be preserved when an observable is unresolved.

Sources and limits: [forced-response CLI](../tools/forced_response_compare.py),
[exporter](../didgeridoo_optimizer/reporting/forced_response.py),
[FORCED_RESPONSE_01](FORCED_RESPONSE_01.md),
[THERMO_01_ZK_REFERENCE](THERMO_01_ZK_REFERENCE.md),
[RADIATION_01_REFERENCE](RADIATION_01_REFERENCE.md),
[IO_N2_REFERENCE](IO_N2_REFERENCE.md).
ZK omits wall/porosity effects; Silva is a cylindrical reference load with explicit
mounting assumptions and extrapolation status, not a validated bell model.
None of these options changes the optimizer/fixed-design legacy defaults.

## 9. Validation expectations

| Validation item | What it means | What it does not mean | Status | Sources |
|---|---|---|---|---|
| Config/path load | Optimizer dry-run establishes config/schema/path preflight; fixed-design additionally validates material records, geometry and analysis input shape. | Neither validates acoustic outputs or material coefficients. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/design_input.py` |
| A-E validation bench | Checks minimum physical trends for the linear model across reference cases. | It does not establish material coefficients globally or validate every generated design. | sourced | `project_specs/04_validation_VALIDATION_BENCH_AE_V1.md`, `didgeridoo_optimizer/tests/validation_runner.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Runtime warnings | Warn about low confidence, few peaks, high-loss materials, large bells, and placeholder features. | Warnings are not a full validation policy. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Nonlinear results | Provide MVP top-candidate refinement when enabled. | They are not yet documented as gate-worthy validation. | sourced | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Calibration artifacts | Support traceability and patch-state review. | They are not material promotion or product truth by themselves. | sourced | `AGENTS.md`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

Numerical reference, convergence, availability and CLI tests establish bounded code
contracts, not player performance, material calibration or experimental validation.
See [PHYS_REF_01](PHYS_REF_01_BOUNDARY_REFERENCE.md) and the diagnostic notes above.

Open decision: define a broader output trust checklist for interpreting optimizer results.
The first CLI step now exposes `--dry-run` as a preflight command, but the full output trust checklist remains open.

## 10. Practical current contract summary

| Question | Current answer | Status |
|---|---|---|
| What does a user provide for the full optimizer? | A YAML config path, with referenced material DB and optional variant rules resolvable from that config. | sourced |
| What does the program write? | Optimizer summaries/scores/plots, best-design bundle and interpretation note; fixed-design and diagnostics write their separate neutral bundles described above. | sourced |
| Is there a stable CLI? | Yes, as a first step: `python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path>`, with optional `--output-dir <path>` and `--dry-run`. This does not yet imply a complete schema framework or full compatibility policy. | sourced |
| Is there a stable report schema? | A minimal `dcalc.optimizer.report.v1` compatibility policy exists for metadata, top-level keys, standard output files, and export-control semantics; nested results remain advisory/internal unless stabilized later. | sourced / open decision |
| Can a user pass a fixed design file? | Yes: `--config <path> --design <path>` invokes one linear evaluation with a strict physical-design file boundary. | sourced |
| What remains open? | Broader compatibility promises for config/report/fixed-design/diagnostic payloads; current schema markers do not freeze all internals. | open decision |
