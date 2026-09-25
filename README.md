# D-Calc

Didgeridoo optimizer project.

## Project scope

The project develops a didgeridoo optimization engine based on:
- linear acoustics,
- nonlinear MVP modeling,
- multi-objective optimization,
- material calibration,
- physical validation through the A-E validation bench.

## Repository map

- `didgeridoo_optimizer/`: canonical code.
- `project_specs/`: current product, workflow, model, and validation specifications.
- `results/`: structured validation and calibration artifacts.

## Current implementation status

Implemented:
- linear MVP
- A-E validation bench
- optimization core
- reporting
- robustness
- nonlinear MVP
- `run_optimizer` CLI for optimization or one supplied physical design
- separate forced-response and thermoviscous reference diagnostics
- targeted calibration (post-Sprint 8 work continues)

## Current CLI / optimizer status

Three workflows are implemented. The optimizer selects candidates, fixed-design
evaluates one supplied profile, and the separate diagnostics study linear responses
under explicit assumptions. Their result schemas differ; see the
[current I/O contract](project_specs/USER_IO_CONTRACT_CURRENT.md).
Schema markers do not imply a broad public API compatibility guarantee.

Run the following Bash examples from the repository root with the project Python
environment active. Choose a new output directory outside the checkout:

```bash
dcalc_run="$(pwd)/../dcalc-example-run"
mkdir -p "$dcalc_run"
```

**1. Existing optimizer.** The tracked
[starter config](project_specs/CONFIG_STARTER_EXAMPLE.yaml) supplies a small search
budget and disables nonlinear refinement. Its preflight does not calculate acoustics
or create output; the second command runs optimization, including its configured
runtime estimation and robustness phase.

```bash
python -B -m didgeridoo_optimizer.pipeline.run_optimizer --config project_specs/CONFIG_STARTER_EXAMPLE.yaml --output-dir "$dcalc_run/optimizer" --dry-run
python -B -m didgeridoo_optimizer.pipeline.run_optimizer --config project_specs/CONFIG_STARTER_EXAMPLE.yaml --output-dir "$dcalc_run/optimizer"
```

The optimizer writes summaries, scores and plots according to reporting flags,
a best-design bundle when a candidate exists, and `post_run_interpretation.txt`.
Only supported, enabled objectives with positive weight participate in aggregate
scoring and selector dimensions; unknown enabled names are ignored with a warning.
Diagnostic scores emitted by later phases do not activate objectives by themselves.

**2. Fixed-design linear evaluation.** Add `--design` to the same CLI.
The [FIXED workflow](project_specs/FIXED_DESIGN_EVALUATION_WORKFLOW_DRAFT.md)
contains the minimal physical-profile example. There is no tracked standalone
DESIGN example file; this command creates that profile as JSON in the chosen
external directory, refusing to overwrite an existing file:

```bash
python -B -c 'import json, pathlib, sys; design = {"id": "fixed_linear_smoke", "segments": [{"kind": "cylinder", "length_cm": 100.0, "d_in_cm": 3.0, "d_out_cm": 3.0, "material_id": "pvc_pressure"}], "metadata": {"note": "physical profile supplied by caller"}}; p = pathlib.Path(sys.argv[1]); json.dump(design, p.open("x", encoding="utf-8"), indent=2)' "$dcalc_run/design.json"
python -B -m didgeridoo_optimizer.pipeline.run_optimizer --config project_specs/CONFIG_STARTER_EXAMPLE.yaml --design "$dcalc_run/design.json" --output-dir "$dcalc_run/fixed" --dry-run
python -B -m didgeridoo_optimizer.pipeline.run_optimizer --config project_specs/CONFIG_STARTER_EXAMPLE.yaml --design "$dcalc_run/design.json" --output-dir "$dcalc_run/fixed"
```

Fixed-design preflight loads materials and validates the physical design and analysis
parameters without acoustics or output. A run evaluates once, preserving the linear
API's features and scores. Optimization, ranking, Pareto, robustness, nonlinear
simulation and runtime estimation are not executed, even if configured.
The three outputs are `evaluated_design_result.json`, `evaluated_design_result.yaml`
and `evaluated_design_summary.txt`, with `dcalc.fixed_design.result.v1` metadata.
`ok` means completion; `valid` means model constraints, not physical validation.
DESIGN resolves from the caller's directory; a relative optimizer/fixed output path
resolves from the config directory. The absolute paths above avoid that distinction.

**3. Forced responses and reference diagnostics.** The existing
[forced-response tool](tools/forced_response_compare.py) accepts the same CONFIG/DESIGN
pair or built-in synthetic cases (`cylinder`, `expansion`, `constriction`, `body_bell`).
Exactly one positive peak source is required. These examples use the design created
above and a built-in profile, respectively:

```bash
python -B -m tools.forced_response_compare --config project_specs/CONFIG_STARTER_EXAMPLE.yaml --design "$dcalc_run/design.json" --pressure-peak-pa 1 --output-dir "$dcalc_run/forced-preflight" --dry-run
python -B -m tools.forced_response_compare --case cylinder --flow-peak-m3-s 1e-6 --loss-model both --air-reference ck_dry20 --radiation-model silva_unflanged --f-min 40 --f-max 1000 --points 97 --h-cm 1 --output-dir "$dcalc_run/forced"
```

This diagnostic calculates new responses and writes `forced_response.json`,
`forced_response.csv` and `forced_response.txt` (`schema: dcalc.forced_response.v1`).
It performs no scoring or peak extraction. See [FORCED_RESPONSE_01](project_specs/FORCED_RESPONSE_01.md)
for source/port units, availability statuses and power interpretation, and
[IO_N2_REFERENCE](project_specs/IO_N2_REFERENCE.md) for roundoff bounds at fixed mesh.

The separate [THERMO diagnostic](tools/thermo_reference_compare.py) compares legacy
and ZK impedances. It has no `--dry-run`; this bounded example disables modal extraction:

```bash
python -B -m tools.thermo_reference_compare --case cylinder --air-reference ck_dry20 --f-min 40 --f-max 1000 --points 97 --spatial-steps 1 --max-modes 0 --output-dir "$dcalc_run/thermo"
```

Fixed-design, forced-response and THERMO bundles refuse existing output filenames.
Diagnostic output paths resolve from the caller's directory. Their exports and
controls are separate from the optimizer's `reporting.save_*` flags.

Optimizer/fixed-design defaults remain legacy beta losses and legacy radiation.
ZK and Silva are explicit diagnostic or lower-level API options, not optimizer
config switches. ZK uses an explicit air state and omits material wall/porosity
effects; Silva's cylindrical mounting assumptions do not validate a bell.
See [PHYS boundary](project_specs/PHYS_REF_01_BOUNDARY_REFERENCE.md),
[THERMO reference](project_specs/THERMO_01_ZK_REFERENCE.md) and
[RADIATION reference](project_specs/RADIATION_01_REFERENCE.md).
Numerical contract tests do not establish player efficiency, played sound,
experimental agreement or calibrated material coefficients.

## Workflow snapshot

- The GitHub repo is the canonical source of code.
- ChatGPT is used for strategic framing, handoff, and sensitive decisions.
- Codex acts inside the repository for bounded implementation, tests, commits, pushes, and PRs.
- GitHub keeps canonical history through scoped branches and pull requests.
- Deterministic tests and small PRs bound changes.
- Work blocks are classified as green, orange, or red in `project_specs/WORKFLOW_CONTROL_SPEC_V1.md`.
- Repository search/indexing may be delayed or incomplete in some chat environments.
- When needed, code should be consulted by direct file path, not only by indexed search.

## Key specs and guardrails

- `AGENTS.md`: local Codex project guardrails.
- `project_specs/WORKFLOW_CONTROL_SPEC_V1.md`: green/orange/red workflow control policy.
- `project_specs/USER_IO_CONTRACT_CURRENT.md`: current user-facing input/output contract.
- `project_specs/PRODUCT_MODEL_SPEC_CURRENT.md`: current product/model contract and open decisions.

## Key code entry points

- `didgeridoo_optimizer/pipeline/run_optimizer.py`
- `didgeridoo_optimizer/pipeline/fixed_design.py`
- `tools/forced_response_compare.py`
- `tools/thermo_reference_compare.py`
- `didgeridoo_optimizer/pipeline/run_calibration.py`
- `didgeridoo_optimizer/tests/validation_runner.py`
- `didgeridoo_optimizer/materials/database.py`

## Validation artifacts

The `results/` directory contains structured calibration / validation artifacts.
These artifacts are useful for traceability and replay guidance, but they do not replace replay, validation, and code-level checks.
Material calibration artifacts do not by themselves establish global material coefficients or imply material promotion.

## Environment

Use a Python environment with dependencies listed in `requirements.txt`.
