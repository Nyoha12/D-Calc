# Validation Commands Current

## 1. But

This document helps Codex, ChatGPT, and human reviewers choose the smallest useful validation command for a bounded D-Calc block.

It is an operational command matrix, not a validation policy and not a replay plan.

## 2. Quick Command Matrix

| Command | Usage | When to use | What it proves | What it does not prove |
|---|---|---|---|---|
| `python -m unittest didgeridoo_optimizer.tests.test_run_optimizer_cli` | Targeted optimizer CLI regression suite. | Use for CLI entry point, dry-run, schema payload, runpy-warning, and tiny full CLI smoke coverage changes. | The current CLI tests pass, including the tiny full subprocess smoke run and expected temporary exports. | Does not prove broad optimizer quality, calibration validity, material promotion readiness, or full report compatibility beyond covered assertions. |
| `python -m unittest discover -s didgeridoo_optimizer/tests` | Broader local unittest discovery. | Use before merging most green/orange code or test blocks when runtime remains reasonable. | The current deterministic unittest suite passes together. | Does not replace long replays, empirical validation, or a complete release gate. |
| `python -m didgeridoo_optimizer.pipeline.run_optimizer --config <path> --dry-run` | CLI preflight for one config path. | Use before full optimizer runs or when checking config path, material DB path, variant rules path, output directory resolution, and schema version handling. | The config exists, parses as YAML mapping, has a supported schema version, and resolves key paths without writing optimizer result artifacts. | Does not run optimization, does not validate acoustic quality, does not create or check final report artifacts, and does not prove a design is physically good. |
| `python -m didgeridoo_optimizer.tests.validation_runner` | A-E linear validation bench entry point. | Use when touching linear acoustics, geometry validation assumptions, transfer/radiation/loss behavior, or A-E validation expectations. | The implemented A-E trend checks pass for the current validation cases. | Does not establish material coefficients globally, does not validate every optimized design, and does not approve material promotion. |

## 3. Windows Temp Setup

For Python commands that may use temporary files, prefer a workspace-local temp directory in the same PowerShell session:

```powershell
New-Item -ItemType Directory -Force .codex_tmp | Out-Null
$env:TEMP = (Resolve-Path .\.codex_tmp).Path
$env:TMP = (Resolve-Path .\.codex_tmp).Path
```

Do not commit `.codex_tmp/`.

## 4. Guardrails

- Fast validation is not a calibration replay.
- These commands alone must not be used to promote a material.
- Do not present material coefficients as globally established from these commands.
- `results/` artifacts are traceability aids; they do not replace code, validation, and replay context.
- Red blocks from `project_specs/WORKFLOW_CONTROL_SPEC_V1.md` still require explicit approval before action.
- Use the smallest command that covers the changed surface, then broaden only when the risk justifies it.
