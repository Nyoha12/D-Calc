# D-Calc

Didgeridoo optimizer project.

## Repository roles

### Canonical code
- `didgeridoo_optimizer/`

### Project specifications
- `project_specs/`

### Structured validation / calibration artifacts
- `results/`

## Project scope

The project develops a didgeridoo optimization engine based on:
- linear acoustics,
- nonlinear MVP modeling,
- multi-objective optimization,
- material calibration,
- physical validation through the A–E validation bench.

## Current status

Implemented:
- linear MVP
- A–E validation bench
- optimization core
- reporting
- robustness
- nonlinear MVP
- `run_optimizer`
- targeted calibration (post-Sprint 8 work continues)

## Important workflow note

- The GitHub repo is the canonical source of code.
- The OpenAI Project is used for handoff, context, and decisions.
- Repository search/indexing may be delayed or incomplete in some chat environments.
- When needed, code should be consulted by direct file path and manifest, not only by indexed search.

## Key entry points

- `didgeridoo_optimizer/pipeline/run_calibration.py`
- `didgeridoo_optimizer/tests/validation_runner.py`
- `didgeridoo_optimizer/materials/database.py`

## Validation artifacts

The `results/` directory contains structured calibration / validation artifacts.
These artifacts are useful for traceability and replay guidance, but they do not replace replay and code-level validation.

## Environment

Use a Python environment with dependencies listed in `requirements.txt`.