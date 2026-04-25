# USER_IO_CONTRACT_CURRENT

## 1. Document status

This document describes the current user-facing input/output contract for D-Calc.
It is a current-state contract for documentation and planning, not a CLI or schema implementation.

| Claim | Status | Sources |
|---|---|---|
| The implemented full optimizer entry point is Python-facing, not a stable user CLI. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| The repo does not yet define a versioned public config schema or report schema. | sourced | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md`, `didgeridoo_optimizer/reporting/export.py` |
| Generated artifacts and calibration results are not product truth by themselves. | sourced | `AGENTS.md`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

## 2. Current user input contract

| Input | Current contract | Required now | Status | Sources |
|---|---|---|---|---|
| Config path | `OptimizerRunner.run(config_path)` and module-level `run(config_path)` read a YAML config from the supplied path. | Yes for full optimizer runs. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Config file format | YAML mapping matching the broad shape of `CONFIG_TEMPLATE_V1.yaml`. | Practically yes, though not enforced by a formal schema. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Material DB path | `materials.database_file` is resolved from the config; missing value defaults to `materials_base_v1.yaml`. | Yes: a loadable material DB must exist after resolution. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Variant rules path | `materials.variant_rules_file` is resolved from the config and passed to `MaterialDatabase.from_yaml`. | Optional when variants are not needed; expected by the template. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Output directory | `project.output_dir` is resolved; relative paths are resolved against the config file parent and created if needed. | Optional in config because the runner defaults to `./results`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Candidate/design input | The full optimizer generates candidates internally from `SearchSpace`; fixed designs can be evaluated only through lower-level Python APIs. | Not a public full-optimizer input. | sourced | `didgeridoo_optimizer/optimization/search_space.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |

Path resolution details:

| Behavior | Status | Sources |
|---|---|---|
| Material and variant paths are checked as given, then relative to the config file parent, then by filename under `/mnt/data`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Relative output directories are resolved relative to the config file parent, not necessarily the process working directory. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `project.overwrite_output_dir` exists in the template but is not currently used by `run_optimizer.py` to delete or replace an output directory. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |

## 3. Config field stability

Current config fields are best read in three groups.

| Group | Fields / sections | Contract | Status | Sources |
|---|---|---|---|---|
| Currently consumed | `project.random_seed`, `project.output_dir`, `materials.database_file`, `materials.variant_rules_file`, `materials.allowed_materials`, `materials.max_distinct_materials_per_design`, `geometry_constraints`, `topology.allow_bell`, `topology.allow_bell_types`, `bell.geometry_constraints`, `frequency_analysis`, `objectives`, `optimization`, `nonlinear_simulation.enabled`, `nonlinear_simulation.run_only_for_top_n`, `reporting.save_*`. | Used by current optimizer, search, evaluation, nonlinear, or export code. | sourced | `project_specs/CONFIG_TEMPLATE_V1.yaml`, `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Present but partly advisory | `units`, `player_model`, `uncertainty_management`, detailed reporting text settings, some objective metadata such as `direction` and `normalization`. | Present in the template, but not all fields are fully enforced as public behavior. | inferred | Template fields exceed direct consumers in inspected code. |
| Experimental / MVP placeholder | `vocal_control`, `transients_noise`, branch/Helmholtz topology flags, broad nonlinear controls beyond enable/top-N, and full report schema versioning. | Do not treat as stable user contract yet. | experimental / MVP placeholder | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md`, `project_specs/CONFIG_TEMPLATE_V1.yaml` |

Open decision: the repo should eventually define a schema version and decide which config fields are public compatibility guarantees.

## 4. Current execution interface

| Interface | Current contract | Status | Sources |
|---|---|---|---|
| `didgeridoo_optimizer.pipeline.run_optimizer.run(config_path)` | Main Python function for a complete optimizer run. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `OptimizerRunner` methods | Lower-level Python API for loading context, estimating runtime, running phases, finalizing, and exporting. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `python -m didgeridoo_optimizer.pipeline.run_optimizer` | Exists, but uses a hard-coded `/mnt/data/CONFIG_TEMPLATE_V1.yaml` default and prints only the best design id. | experimental / MVP placeholder | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| Stable CLI | No stable CLI contract exists for choosing config path, output directory, validation behavior, dry-run behavior, or report schema version. | open decision | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md`, `didgeridoo_optimizer/pipeline/run_optimizer.py` |

Recommended future CLI contract, not implemented yet:

| Future promise | Status |
|---|---|
| Accept an explicit config path. | open decision |
| Print resolved config/material/output paths before running. | open decision |
| Support a validation/dry-run mode that checks config and paths without starting optimization. | open decision |
| Emit a stable machine-readable run summary with schema version. | open decision |
| Avoid silently relying on `/mnt/data` defaults for normal user workflows. | open decision |

## 5. Output and report contract

| Output | Current contract | Controlled by | Status | Sources |
|---|---|---|---|---|
| Python return payload | Contains `config`, `runtime_estimate`, `runtime_actual_seconds`, `linear_results`, `robust_results`, `nonlinear_results`, `best_design`, `top_20`, `warnings`, and `exports`. | Always returned by `run()`. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `optimizer_summary.json` | Lightened final payload without full `freq_hz`, `zin`, or `zin_mag` arrays. | `reporting.save_json_summary` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| `optimizer_summary.yaml` | YAML form of the same lightened final payload. | `reporting.save_yaml_summary` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/reporting/export.py` |
| `top20_scores.csv` | Ranked candidates with score, validity, core features, objective scores, and penalties. | `reporting.save_csv_scores` | sourced | `didgeridoo_optimizer/reporting/export.py` |
| `pareto_overview.png` | Pareto plot for ranked candidates. | `reporting.save_plots` | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| `best_design/` bundle | French text summary, JSON/YAML best result, impedance plot, and radiation plot when a best candidate exists. | Best candidate exists; plot/report helpers run. | sourced | `didgeridoo_optimizer/reporting/export.py`, `didgeridoo_optimizer/reporting/summaries.py` |
| Warnings | Runtime warnings plus best-candidate warnings are deduplicated into final `warnings`. | Internal pipeline behavior. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Schema version | No explicit report schema version is currently emitted. | Not implemented. | open decision | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md`, `didgeridoo_optimizer/reporting/export.py` |

Output interpretation rules:

| Rule | Status | Sources |
|---|---|---|
| `best_design` and `top_20` are optimizer selections under the current config and model, not physical guarantees. | inferred | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| `model_confidence` is a 1D validity proxy, not empirical proof of playability or build quality. | sourced | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| French text summaries are the only implemented natural-language report summaries. | sourced | `didgeridoo_optimizer/reporting/summaries.py` |

## 6. Fixed-design evaluation status

| Mode | Current status | Contract | Sources |
|---|---|---|---|
| Full optimizer fixed-design input | Unsupported as a documented user workflow. | Users should not expect `run_optimizer.run(config_path)` to accept a fixed design file today. | sourced | `didgeridoo_optimizer/pipeline/run_optimizer.py`, `didgeridoo_optimizer/optimization/search_space.py` |
| Lower-level linear evaluation | Supported internally through `evaluate(design, config, materials)` with a mapping or `Design`. | Useful for tests and internal tooling, but not yet a public product API. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py`, `didgeridoo_optimizer/geometry/models.py` |
| Search candidate/genome input | Internal-only. `SearchSpace` samples, mutates, repairs, and decodes genomes. | Do not expose as stable user schema yet. | sourced | `didgeridoo_optimizer/optimization/search_space.py` |
| Public fixed-design schema | Not defined. | Needs a decision before a CLI/API should accept design files. | open decision | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

## 7. Validation expectations

| Validation item | What it means | What it does not mean | Status | Sources |
|---|---|---|---|---|
| Config/path load | The config and referenced material files can be read and resolved. | It does not prove outputs are physically valid. | inferred | `didgeridoo_optimizer/pipeline/run_optimizer.py` |
| A-E validation bench | Checks minimum physical trends for the linear model across reference cases. | It does not establish material coefficients globally or validate every generated design. | sourced | `project_specs/04_validation_VALIDATION_BENCH_AE_V1.md`, `didgeridoo_optimizer/tests/validation_runner.py`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Runtime warnings | Warn about low confidence, few peaks, high-loss materials, large bells, and placeholder features. | Warnings are not a full validation policy. | sourced | `didgeridoo_optimizer/pipeline/evaluate_linear.py` |
| Nonlinear results | Provide MVP top-candidate refinement when enabled. | They are not yet documented as gate-worthy validation. | sourced | `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |
| Calibration artifacts | Support traceability and patch-state review. | They are not material promotion or product truth by themselves. | sourced | `AGENTS.md`, `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md` |

Open decision: define a pre-run validation command and an output trust checklist before exposing a stable CLI.

## 8. Practical current contract summary

| Question | Current answer | Status |
|---|---|---|
| What does a user provide for the full optimizer? | A YAML config path, with referenced material DB and optional variant rules resolvable from that config. | sourced |
| What does the program write? | Optional JSON/YAML summaries, CSV scores, plots, and a best-design bundle under the resolved output directory. | sourced |
| Is there a stable CLI? | No. The module entry point exists but is MVP/placeholder. | experimental / MVP placeholder |
| Is there a stable report schema? | No explicit schema version or compatibility policy exists yet. | open decision |
| Can a user pass a fixed design file? | Not through a documented full-optimizer interface. | open decision |
| What should happen next? | Decide and implement a small stable CLI/config/report schema contract, starting with docs and then code. | open decision |
