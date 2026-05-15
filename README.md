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
- stable first-step `run_optimizer` CLI
- targeted calibration (post-Sprint 8 work continues)

## Current CLI / optimizer status

The current stable optimizer entry point is:

```powershell
python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path>
```

Supported CLI options include:
- `--output-dir <path>` for an in-memory output directory override;
- `--dry-run` for config/path preflight without optimizer result artifacts.

The test suite includes tiny full CLI smoke coverage that runs the real optimizer CLI on a temporary minimal config. Output/report files, schema metadata, and the `best_design/` bundle are documented in `project_specs/USER_IO_CONTRACT_CURRENT.md`.

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
- `didgeridoo_optimizer/pipeline/run_calibration.py`
- `didgeridoo_optimizer/tests/validation_runner.py`
- `didgeridoo_optimizer/materials/database.py`

## Validation artifacts

The `results/` directory contains structured calibration / validation artifacts.
These artifacts are useful for traceability and replay guidance, but they do not replace replay, validation, and code-level checks.
Material calibration artifacts do not by themselves establish global material coefficients or imply material promotion.

## Environment

Use a Python environment with dependencies listed in `requirements.txt`.
