# USER_IO_CONTRACT_CURRENT

## 1. Document status

This document describes the current user-facing input/output contract for D-Calc.
It is a current-state contract for documentation and planning, not a full schema framework.

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
| Candidate/design input | The full optimizer generates candidates internally from `SearchSpace`; fixed designs can be evaluated only through lower-level Python APIs. | Not a public full-optimizer input. | sourced | `didgeridoo_optimizer/optimization/search_space.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |

Path resolution details:

| Behavior | Status | Sources |
|---|---|---|
| Material and variant paths are checked as given, then relative to the config file parent, then by filename under `/mnt/data`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Relative output directories are resolved relative to the config file parent, not necessarily the process working directory. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| CLI `--output-dir` overrides `project.output_dir` in memory and does not modify the config file. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| `project.overwrite_output_dir` exists in the template but is not currently used by `run_optimizer.py` to delete or replace an output directory. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |

## 3. Config field stability

Current config fields are best read in three groups.

| Group | Fields / sections | Contract | Status | Sources |
|---|---|---|---|---|
| Currently consumed | `project.random_seed`, `project.output_dir`, `materials.database_file`, `materials.variant_rules_file`, `materials.allowed_materials`, `materials.max_distinct_materials_per_design`, `geometry_constraints`, `topology.allow_bell`, `topology.allow_bell_types`, `bell.geometry_constraints`, `frequency_analysis`, `objectives`, `optimization`, `nonlinear_simulation.enabled`, `nonlinear_simulation.run_only_for_top_n`, `reporting.save_*`. | Used by current optimizer, search, evaluation, nonlinear, or export code. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Present but partly advisory | `units`, `player_model`, `uncertainty_management`, detailed reporting text settings, some objective metadata such as `direction` and `normalization`. | Present in the template, but not all fields are fully enforced as public behavior. | inferred | Template fields exceed direct consumers in inspected code. |
| Experimental / MVP placeholder | `vocal_control`, `transients_noise`, branch/Helmholtz topology flags, broad nonlinear controls beyond enable/top-N, and broader report/config compatibility beyond the minimal documented policies. | Do not treat as stable user contract yet. | experimental / MVP placeholder | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md`, `project_specs/CONFIG_TEMPLATE_V1.yaml` |

The minimal `dcalc.optimizer.config.v1` compatibility policy is documented below. Broader compatibility for the full template remains open.

## 4. Config v1 compatibility policy

This is a minimal compatibility policy for `dcalc.optimizer.config.v1`, not a full schema framework and not a promise to stabilize every field in `CONFIG_TEMPLATE_V1.yaml`.

| Area | Current v1 policy | Status | Sources |
|---|---|---|---|
| Schema marker | `schema_version: dcalc.optimizer.config.v1` is the current explicit marker. Explicit v1 is accepted, missing schema is accepted as `missing_assumed_v1`, and unknown schema versions fail early. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Config file shape | The config file must parse as a YAML mapping. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Path resolution | Material and variant-rule paths are resolved from the config value, then relative to the config file parent, then by filename under `/mnt/data`. Relative output directories are resolved from the config file parent. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| CLI output override | `--output-dir <path>` overrides `project.output_dir` in memory and must not mutate the config file. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Current run sections | The current optimizer run consumes `project`, `environment`, `materials`, `geometry_constraints`, `topology`, `bell`, `frequency_analysis`, `objectives`, `optimization`, `runtime_estimation`, `nonlinear_simulation`, and `reporting`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/optimization/search_space.py`, `didgeridoo_optimizer/optimization/objectives.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/pipeline/evaluate_nonlinear.py` |
| Stable minimal fields | Stable minimal fields are `project.output_dir`, `project.random_seed`, `materials.database_file`, `materials.variant_rules_file`, `materials.allowed_materials`, `materials.max_distinct_materials_per_design`, consumed air/environment properties, `geometry_constraints`, `topology.allow_bell`, `topology.allow_bell_types`, `bell.geometry_constraints`, `frequency_analysis`, `peak_detection`, `objectives.<name>.enabled`, `objectives.<name>.weight`, `objectives.<name>.hard_constraint`, consumed per-objective tuning fields, optimization budgets/counts/top-N/final selector, basic consumed nonlinear fields, and the reporting flags listed below. | sourced / inferred | Same code sources as current run sections. |
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
| `--dry-run` | Performs preflight validation and path resolution without running optimization or writing optimizer result artifacts. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Full optimizer execution via CLI | Implemented through the CLI wrapper and exercised by a tiny full CLI smoke test that writes artifacts only in a temporary output directory. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Fixed-design CLI | Not part of the stable CLI. Fixed designs remain lower-level/internal. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| CLI payload metadata | CLI payloads include `schema_version: dcalc.optimizer.cli.v1` and `payload_type` as `dry_run` or `run_summary`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Report schema versioning | Optimizer summary payloads include `schema_version: dcalc.optimizer.report.v1` plus config schema metadata. A minimal compatibility policy exists for top-level fields, standard output files, and export-control semantics; broader compatibility remains open. | sourced / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

Current CLI validation coverage:

| Validation command | Result | Status |
|---|---|---|
| `python -m unittest discover -s didgeridoo_optimizer/tests` | Current local discovery suite; exact count may evolve. | sourced |

Remaining CLI and schema decisions:

| Remaining promise / decision | Status |
|---|---|
| Define broader config compatibility beyond the minimal `dcalc.optimizer.config.v1` policy. | open decision |
| Define payload evolution and compatibility expectations beyond the current CLI/report schema metadata. | open decision |
| Decide whether full CLI runs should print resolved config/material/output paths before running, not only in `--dry-run`. | open decision |
| Decide whether to expose fixed-design evaluation as a public CLI/API. | open decision |
| Decide whether broader best-design bundle controls are needed beyond the implemented plot toggle. | open decision |

## 6. Output and report contract

| Output | Current contract | Controlled by | Status | Sources |
|---|---|---|---|---|
| Python return payload | Contains `schema_version: dcalc.optimizer.report.v1`, config schema metadata, `config`, `runtime_estimate`, `runtime_actual_seconds`, `linear_results`, `robust_results`, `nonlinear_results`, `best_design`, `top_20`, `warnings`, and `exports`. | Always returned by `run()`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `optimizer_summary.json` | Lightened final payload without full `freq_hz`, `zin`, or `zin_mag` arrays; includes optimizer report schema metadata. | `reporting.save_json_summary` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| `optimizer_summary.yaml` | YAML form of the same lightened final payload. | `reporting.save_yaml_summary` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| `top20_scores.csv` | Ranked candidates with score, validity, core features, objective scores, and penalties. | `reporting.save_csv_scores` | sourced | `didgeridoo_optimizer/reporting/export.py` |
| `pareto_overview.png` | Pareto plot for ranked candidates. In current code, `reporting.save_plots` controls this overview plot. | `reporting.save_plots` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `best_design/` bundle | Written when a best candidate exists. Current files are `best_design_summary.txt`, `best_design_result.json`, `best_design_result.yaml`, `best_design_impedance.png`, and `best_design_radiation.png`. The best-design impedance/radiation plots are currently part of this bundle, not controlled by `reporting.save_plots`. | Best candidate exists; bundle exporter runs. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/reporting/summaries.py` |
| Best-design plot control | `reporting.save_best_design_plots` defaults to `true` and controls only `best_design_impedance.png` and `best_design_radiation.png`. It does not change the meaning of `reporting.save_plots`, and it does not disable the full `best_design/` bundle. | `reporting.save_best_design_plots` | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Warnings | Runtime warnings plus best-candidate warnings are deduplicated into final `warnings`. | Internal pipeline behavior. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Schema version | Optimizer summary payloads emit `schema_version: dcalc.optimizer.report.v1`, plus `config_schema_version` and `config_schema_status`; a minimal report v1 compatibility policy is documented below. | Minimal metadata and compatibility policy implemented in docs; broader policy open. | sourced / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |

## 7. Report v1 compatibility policy

This is a minimal compatibility policy for `dcalc.optimizer.report.v1`, not a full schema framework and not a promise to freeze all nested optimizer internals.

| Area | Current v1 policy | Status | Sources |
|---|---|---|---|
| Stable minimal metadata | Reports keep `schema_version` with value `dcalc.optimizer.report.v1`, plus `config_schema_version` and `config_schema_status`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Stable top-level keys | Reports keep `config`, `runtime_estimate`, `runtime_actual_seconds`, `linear_results`, `robust_results`, `nonlinear_results`, `best_design`, `top_20`, `warnings`, and `exports`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Stable basic types | `warnings` is a list, `exports` is a mapping, and `runtime_actual_seconds` is numeric. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Stable standard file names | Current standard outputs are `optimizer_summary.json`, `optimizer_summary.yaml`, `top20_scores.csv`, `pareto_overview.png`, `best_design/best_design_summary.txt`, `best_design/best_design_result.json`, `best_design/best_design_result.yaml`, `best_design/best_design_impedance.png`, and `best_design/best_design_radiation.png`, when their controlling conditions are met. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Stable export controls | `reporting.save_json_summary`, `reporting.save_yaml_summary`, `reporting.save_csv_scores`, `reporting.save_plots`, and `reporting.save_best_design_plots` keep their documented meanings. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/tests/test_run_optimizer_cli.py` |
| Stable export truthfulness | `exports` should announce only files that were actually written. | inferred | Current export construction adds paths only when the corresponding writer runs. |
| Advisory / internal details | Fine structure of `linear_results`, `robust_results`, `nonlinear_results`, candidates in `best_design` and `top_20`, exact warning text, score/objective internals, runtime-estimate details, and plot pixel contents remain advisory/internal unless a future decision stabilizes them. | inferred / open decision | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Compatible evolution | It is compatible to add optional fields, optional output files, new `exports` keys, richer advisory results, additional warnings, or improved payloads as long as stable fields remain present and compatible. | open decision | Minimal v1 policy. |
| New report schema required | A new `schema_version` should be used for removing or renaming a stable field, changing the type or meaning of a stable field, changing standard file names incompatibly, making `exports` stop matching written files, or changing the documented meaning of `best_design`, `top_20`, or export controls. | open decision | Minimal v1 policy. |

Output interpretation rules:

| Rule | Status | Sources |
|---|---|---|
| `best_design` and `top_20` are optimizer selections under the current config and model, not physical guarantees. | inferred | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| `model_confidence` is a 1D validity proxy, not empirical proof of playability or build quality. | sourced | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| French text summaries are the only implemented natural-language report summaries. | sourced | `didgeridoo_optimizer/reporting/summaries.py` |

## 8. Fixed-design evaluation status

| Mode | Current status | Contract | Sources |
|---|---|---|---|
| Full optimizer fixed-design input | Unsupported as a documented user workflow. | Users should not expect `run_optimizer.run(config_path)` to accept a fixed design file today. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Lower-level linear evaluation | Supported internally through `evaluate(design, config, materials)` with a mapping or `Design`. | Useful for tests and internal tooling, but not yet a public product API. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/geometry/models.py` |
| Search candidate/genome input | Internal-only. `SearchSpace` samples, mutates, repairs, and decodes genomes. | Do not expose as stable user schema yet. | sourced | `didgeridoo_optimizer/optimization/search_space.py` |
| Public fixed-design schema | Not defined. | Needs a decision before a CLI/API should accept design files. | open decision | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

Planned fixed-design evaluation workflow:

| Candidate workflow item | Planned status | Notes |
|---|---|---|
| Future CLI shape | open decision / not yet implemented | Candidate shape is `--config <path>` for evaluation context plus a separate `--design <path>` YAML/JSON design file. |
| First evaluation scope | open decision | Recommended first step is linear evaluation only. Robustness and nonlinear refinement should remain out of scope or advisory until a later decision. |
| Config relation | open decision | `dcalc.optimizer.config.v1` should keep its current meaning for materials, environment, frequency, constraints, objectives, and reporting; adding a fixed-design input should not immediately redefine config v1. |
| Design format | open decision | Do not treat the current internal mapping shape as a final public design schema yet. A minimal candidate would include `id`, `segments`, segment `kind`, `length_cm`, `d_in_cm`, `d_out_cm`, `material_id`, optional `profile_params`, and optional `metadata`. |
| Candidate outputs | open decision / not yet contract | Possible minimal outputs are stdout JSON with `ok`, `payload_type`, schema metadata, config schema metadata, `design_id`, `valid`, `errors`, `warnings`, `aggregate_score`, and a compact feature summary, plus optional result JSON/YAML files under the output directory. |
| Interpretation | sourced / inferred | A fixed-design linear evaluation result is an evaluated design under the configured model, not a physical validation, material promotion, or A-E validation result. |

## 9. Validation expectations

| Validation item | What it means | What it does not mean | Status | Sources |
|---|---|---|---|---|
| Config/path load | The config and referenced material files can be read and resolved. | It does not prove outputs are physically valid. | inferred | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| A-E validation bench | Checks minimum physical trends for the linear model across reference cases. | It does not establish material coefficients globally or validate every generated design. | sourced | `project_specs/04_validation_VALIDATION_BENCH_AE_V1.md`, `didgeridoo_optimizer/tests/validation_runner.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Runtime warnings | Warn about low confidence, few peaks, high-loss materials, large bells, and placeholder features. | Warnings are not a full validation policy. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Nonlinear results | Provide MVP top-candidate refinement when enabled. | They are not yet documented as gate-worthy validation. | sourced | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Calibration artifacts | Support traceability and patch-state review. | They are not material promotion or product truth by themselves. | sourced | `AGENTS.md`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

Open decision: define a broader output trust checklist for interpreting optimizer results.
The first CLI step now exposes `--dry-run` as a preflight command, but the full output trust checklist remains open.

## 10. Practical current contract summary

| Question | Current answer | Status |
|---|---|---|
| What does a user provide for the full optimizer? | A YAML config path, with referenced material DB and optional variant rules resolvable from that config. | sourced |
| What does the program write? | Optional JSON/YAML summaries, CSV scores, plots, and a best-design bundle under the resolved output directory. | sourced |
| Is there a stable CLI? | Yes, as a first step: `python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path>`, with optional `--output-dir <path>` and `--dry-run`. This does not yet imply a complete schema framework or full compatibility policy. | sourced |
| Is there a stable report schema? | A minimal `dcalc.optimizer.report.v1` compatibility policy exists for metadata, top-level keys, standard output files, and export-control semantics; nested results remain advisory/internal unless stabilized later. | sourced / open decision |
| Can a user pass a fixed design file? | Not through a documented full-optimizer interface. | open decision |
| What should happen next? | Define the remaining CLI/schema decisions, especially broader config/report compatibility and any public fixed-design input contract. | open decision |
