# AGENTS.md - D-Calc / Didgeridoo Optimizer

## Role

You help develop D-Calc, a didgeridoo optimizer.

The project goal is a system that remains:
- physically coherent;
- stable;
- reproducible;
- useful for real optimization work.

Main technical goals:
- linear acoustic simulation;
- nonlinear acoustic simulation;
- multi-objective optimization;
- material calibration;
- physical validation through the A-E validation bench.

## Source of truth

The source of truth for code is the real Git repository checked out in this working tree, cross-checked with origin/main.

Do not treat these as source of truth for code:
- previous ChatGPT conversations;
- temporary workspaces;
- old prompts;
- generated reports;
- files under results/ by themselves;
- materials_patch_suggestions.yaml by itself.

At the start of any non-trivial block, check:
- current branch;
- local HEAD;
- origin/main if relevant;
- git status -sb;
- whether the local branch is ahead, behind, or divergent.

If REPO_SEED_MANIFEST.json exists:
- use it as a bootstrap navigation guide;
- do not treat it as an exact inventory of main;
- the real repository wins if they differ.

Never conclude that a file does not exist only because indexed search did not find it.
Prefer direct path inspection and real file reads.

## Active priority

Default active priority: post-Sprint 8 stabilization.

Useful next blocks usually include:
- patch export consistency / reproducibility audits;
- targeted unit tests and import checks;
- targeted replay checks;
- promotion-readiness analysis without automatic promotion.

Do not promote any material automatically.

## Critical files

For calibration, validation, material, patch export, or promotion tasks, inspect relevant files from:

- REPO_SEED_MANIFEST.json
- didgeridoo_optimizer/pipeline/run_calibration.py
- didgeridoo_optimizer/tests/validation_runner.py
- didgeridoo_optimizer/materials/database.py
- didgeridoo_optimizer/reporting/patch_exports.py
- didgeridoo_optimizer/reporting/__init__.py
- didgeridoo_optimizer/tests/test_patch_exports.py
- project_specs/CALIBRATION_PATCH_EXPORT_STATES.md
- project_specs/
- results/calibration_material_*

## Patch-state model

Patch export states:

- patch_proposal
- patch_replayed
- patch_accepted
- patch_to_calibrate

Expected YAML exports when relevant:

- materials_patch_suggestions.yaml
- materials_patch_replayed.yaml
- materials_patch_accepted.yaml
- materials_patch_to_calibrate.yaml
- materials_patch_status.yaml

Critical rule:
materials_patch_suggestions.yaml means proposal/suggestion only.
It must never be read as an accepted patch.

Expected accepted decisions, to verify in code:
- accept_local_only -> patch_accepted
- accept_family -> patch_accepted
- accept_weighted -> patch_accepted
- keep_as_to_calibrate -> patch_to_calibrate

Known replayed patch source keys include:
- directed_patch
- semidirected_patch
- family_patch
- family_multiseed_patch
- weighted_patch

If a report uses a new patch source key, verify whether patch_exports.py recognizes it before trusting regenerated artifacts.

## Known calibration notes

These are reprise notes, not independent truth.
Always re-check code, reports, and artifacts before relying on them.

directed:
- decision: accept_local_only
- replayed/accepted: plywood_varnished

semidirected:
- decision: accept_local_only
- validation_preserved: true
- replayed/accepted: plywood_varnished

family:
- decision: accept_family
- family_patch: plywood_varnished only
- accepted: plywood_varnished

family_multiseed:
- decision: accept_local_only
- family_multiseed_patch: plywood_varnished
- accepted: plywood_varnished

family_weighted:
- decision: accept_local_only
- weighted_patch: plywood_varnished only
- accepted: plywood_varnished

A suggestions file can contain multiple materials while the replayed/accepted patch is only one material.
For the flows above, replayed/accepted is expected to be local to plywood_varnished.

## Scientific and validation rules

Never promote a patch without A-E validation.

Never generalize a local gain into a global validation.

Always compare baseline vs patched when promotion or validation claims are involved.

Always distinguish:
- proposal;
- replayed;
- accepted;
- to_calibrate;
- locally useful;
- globally promotable.

Never present a provisional material coefficient as precisely established.

When the status of information matters, label it as:
- sourced;
- inferred;
- to_calibrate.

results/ contains useful artifacts, reports, exports, and replay traces.
But results/ is not by itself validation truth.
Artifacts must be interpreted with the code and, when needed, replayed.

## Work block protocol

For any complex task, start with a light active block:

Block:
- Objective:
- Scope:
- Files allowed to change:
- Files to inspect:
- Repo state checked:
- Validation expected:
- Commit target:
- Status:

Keep the block lightweight.
Do not over-formalize small checks.

The active block must guide reasoning.
Reading the repo must not reset the active objective.

A coherent block should ideally map to one coherent commit or a small set of related commits.

## Codex autonomy

Codex should work autonomously inside the current task scope.

For normal bounded development blocks, Codex may:
- inspect files and Git state;
- create and switch feature branches;
- edit files inside the approved scope;
- run targeted tests and validation commands;
- regenerate scoped artifacts when the block allows it;
- revert no-op or line-ending-only churn;
- stage, commit, and push feature branches;
- create or update pull requests.

Codex should not stop for routine Git or GitHub operations unless:
- a command is blocked by policy;
- credentials fail;
- the requested action is outside the current block scope;
- an unexpected semantic diff appears.

Codex must stop before:
- merging a pull request;
- pushing directly to main;
- force-pushing;
- destructive history or cleanup operations such as hard reset, rebase, or git clean;
- changing material coefficients or the material database;
- promoting any material;
- changing physics or acoustic formulas;
- changing validation policy;
- installing dependencies;
- running long or global replays;
- modifying or exposing secrets, .env, credentials, API keys, or authentication settings.

## Working rules

Before modifying code:
1. read this AGENTS.md;
2. check Git state;
3. inspect the real files involved;
4. understand the expected validation.

During implementation:
- make small, reversible changes;
- do not rewrite broad architecture unless the block explicitly asks for it;
- do not install dependencies unless the block explicitly allows it;
- do not change formulas or material policy silently;
- do not change .env, secrets, credentials, API keys, or private files.

After implementation:
- summarize modified files;
- summarize logic changes;
- run relevant tests or explain why not;
- summarize risks;
- propose the next step.

## Windows / Codex sandbox notes

This project is developed on Windows with Codex CLI.

Known issue:
Python tests that use tempfile.TemporaryDirectory or create temporary directories may fail inside the Codex Windows sandbox because of filesystem permission issues.

If a test fails only because Python cannot write to or clean a temporary directory:
- treat it as an environment issue, not a code failure;
- rerun the exact same test outside the sandbox or in normal local PowerShell if needed;
- clean leftover tmp* directories before continuing;
- do not count the test as failed unless the same command also fails outside the sandbox.

If sandbox-created temp directories remain in the repo and cannot be deleted normally:
- report their names;
- ask the user to clean them from elevated PowerShell;
- do not commit them;
- do not let them affect the PR or merge decision.

## GitHub authentication notes

GitHub CLI (`gh`) is used for pull request operations.

Expected checks:

```powershell
gh auth status
gh api user --jq .login
gh pr status