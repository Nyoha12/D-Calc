# didgeridoo_optimizer_repo_seed_latest

This is the safest repo seed assembled from:
1. current compact Project code files (reconstructed into real package files),
2. then overlaid with newer real workspace files when present.

Use this as the initial code import into GitHub.

Precedence used:
- project compact code snapshot
- current real workspace overlays
- project specs/YAML alongside code

Current overlays applied:
- didgeridoo_optimizer/__init__.py
- didgeridoo_optimizer/materials/database.py
- didgeridoo_optimizer/optimization/__init__.py
- didgeridoo_optimizer/pipeline/run_calibration.py
- didgeridoo_optimizer/tests/validation_runner.py

Important:
- The workspace had shown non-persistent behavior. This seed is intended to be the deterministic merge point.
- The compact snapshot is included in `project_compact_snapshot/` for auditability.
- Specs/YAML are included in `project_specs/`.
