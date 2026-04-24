# AGENTS.md - D-Calc / Didgeridoo Optimizer

## Role

You are helping develop D-Calc, a didgeridoo optimizer.

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

## Current source of truth

The source of truth for code is the real Git repository checked out in this working tree, cross-checked with origin/main.

Do not treat any of the following as source of truth for code:
- previous ChatGPT conversations;
- temporary workspaces;
- old prompts;
- generated reports;
- files under results/ by themselves;
- materials_patch_suggestions.yaml by itself.

The handoff and project notes are context, not code truth.

At the start of any non-trivial block, check:
- current branch;
- local HEAD;
- origin/main HEAD if needed;
- git status -sb;
- whether the local branch is ahead, behind, or divergent.

If local state and origin/main differ, say so clearly before reasoning from the code.

## Current local onboarding state

Known from the latest user diagnostic:
- working branch: codex-onboarding;
- main and origin/main were aligned at 07098ab before the onboarding commit;
- current onboarding commit observed: 86b92b1 chore: add Codex project instructions;
- Codex CLI observed: 0.124.0;
- Codex model configured: gpt-5.5;
- reasoning effort configured: xhigh;
- Windows sandbox configured: elevated.

Treat this section as a useful checkpoint, not as permanent truth.
Always re-check Git before important work.

## REPO_SEED_MANIFEST.json

If REPO_SEED_MANIFEST.json exists:
- read it as a bootstrap navigation guide;
- use it to find likely files and provenance;
- do not treat it as an exact inventory of main.

If the manifest and the real repository differ, the real repository wins.

Never conclude that a file does not exist only because indexed search did not find it.
Prefer direct paths and real file reads.

## Active priority

Default active priority: post-Sprint 8 stabilization.

The traceability/backfill block is mostly stabilized.
The next useful block is probably not more simple backfill.

Likely next blocks:
A. final consistency audit of patch export files;
B. run unit tests and import checks;
C. complete replay using current code and compare with backfilled artifacts;
D. prepare possible material promotion analysis, without automatic promotion.

Do not promote any material automatically.

## Critical files to inspect first

For calibration, validation, material, patch export, or promotion tasks, inspect these first if they exist:

- REPO_SEED_MANIFEST.json
- didgeridoo_optimizer/pipeline/run_calibration.py
- didgeridoo_optimizer/tests/validation_runner.py
- didgeridoo_optimizer/materials/database.py
- didgeridoo_optimizer/reporting/patch_exports.py
- didgeridoo_optimizer/reporting/__init__.py
- project_specs/CALIBRATION_PATCH_EXPORT_STATES.md

Also inspect these directories when relevant:
- project_specs/
- results/calibration_material_directed
- results/calibration_material_semidirected
- results/calibration_material_family
- results/calibration_material_family_weighted

Important:
The latest diagnostic showed that tests/test_patch_exports.py was not present at that path.
Do not assume it exists. Search for the real test location before claiming tests are available or missing.

## Expected post-Sprint 8 patch-state model

The code is expected to use these patch states, but always verify in the real files:

- patch_proposal
- patch_replayed
- patch_accepted
- patch_to_calibrate

Expected YAML exports, when relevant:

- materials_patch_suggestions.yaml
- materials_patch_replayed.yaml
- materials_patch_accepted.yaml
- materials_patch_to_calibrate.yaml
- materials_patch_status.yaml

Critical rule:
materials_patch_suggestions.yaml means proposal/suggestion.
It must never be read as an accepted patch.

Expected convention, to verify in code:
- accept_local_only -> patch_accepted
- accept_family -> patch_accepted
- accept_weighted -> patch_accepted
- keep_as_to_calibrate -> patch_to_calibrate

Expected patch export helpers, to verify in code:

- derive_patch_state
- export_patch_state_files
- backfill_patch_state_exports
- backfill_patch_state_exports_in_directory

Expected CLI, to verify in code:

python -m didgeridoo_optimizer.reporting.patch_exports <path>

## Known calibration decisions to re-check

These are reprise notes, not independent truth.
Always re-check code, reports, and artifacts before relying on them.

directed:
- decision: accept_local_only
- mean_delta: 0.00015249850881401036
- improved_count: 6
- worsened_count: 0
- replayed/accepted: plywood_varnished
- to_calibrate: empty

semidirected:
- decision: accept_local_only
- validation_preserved: true
- replayed/accepted: plywood_varnished
- to_calibrate: empty

family:
- decision: accept_family
- mean_delta: 0.006534394176022455
- improved_count: 9
- worsened_count: 0
- family_patch: plywood_varnished only
- accepted: plywood_varnished
- to_calibrate: empty

family_weighted:
- decision: accept_local_only
- mean_delta: 4.788216258471639e-05
- improved_count: 8
- worsened_count: 1
- weighted_patch: plywood_varnished only
- accepted: plywood_varnished
- to_calibrate: empty

Important:
A suggestions file can contain multiple materials while the replayed/accepted patch is only for one material.
For the flows above, the replayed/accepted patch is expected to be local to plywood_varnished.

## Critical scientific and validation rules

Never promote a patch without A-E validation.

Never generalize a local gain into a global validation.

Always compare baseline vs patched.

Always check reproducibility on a relevant pool before promotion.

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

For any complex task, start with a light active block.

Use this format:

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

A change does not exist in the system until it is integrated in code and committed.

## Working rules

Before modifying code:
1. read this AGENTS.md;
2. check Git state;
3. inspect the real files involved;
4. explain the planned change;
5. identify the validation to run.

During implementation:
- make small, reversible changes;
- do not rewrite broad architecture without explicit approval;
- do not install dependencies without explaining why;
- do not change formulas or material policy silently;
- do not change .env, secrets, credentials, API keys, or private files.

After implementation:
- summarize modified files;
- summarize logic changes;
- run relevant tests or explain why not;
- summarize risks;
- propose the next step.

## Windows / Codex sandbox notes

This project is currently developed on Windows with Codex CLI.

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

With autonomous mode enabled, Codex may proceed without asking for routine actions:
- read files;
- run git status, git diff, git log, git grep;
- run targeted unit tests;
- rerun known safe tests when the only issue is the Windows tempfile sandbox problem;
- edit files inside an explicitly approved block scope;
- commit approved block changes;
- push the current working branch;
- create or update pull requests.

Codex must still stop before:
- merging a pull request;
- pushing to main;
- force-pushing;
- rebasing or resetting destructive history;
- changing material coefficients or material database values;
- promoting any material;
- changing physics or acoustic formulas;
- changing validation policy;
- installing dependencies;
- running long or global replays;
- modifying secrets, .env, credentials, API keys, or private files.

## Git rules

Do not commit unless explicitly asked.

Do not push unless explicitly asked.

Do not force-push.

Do not delete branches without explicit approval.

If the repo is dirty at the start of a task, report it before doing anything else.

If main/origin/main has moved, report it and propose a clean update path.

## When to ask for local command output

Prefer direct repo access and targeted file reads.

Ask the user for PowerShell outputs when:
- indexed search is incomplete or misleading;
- a file read is truncated;
- local, GitHub, and visible-chat states may differ;
- executable behavior must be verified;
- a replay or export must be confirmed from the real clone.

Ask for minimal, explicit commands and request raw output, not a free summary.

## If no explicit objective is given

Infer the next useful block from:
- post-Sprint 8 stabilization;
- real repo state;
- critical files;
- available validations;
- latest known work state.

Default first action:
perform a targeted audit without modifying code.

Recommended next block if nothing else is specified:
audit patch export consistency across calibration_material_* results directories and identify the real test command/location before running tests.
