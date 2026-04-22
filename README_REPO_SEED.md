# README_REPO_SEED

This repository was initially bootstrapped from a reconstructed seed package.

Bootstrap provenance:
1. project compact snapshot reconstructed into real package files
2. newer workspace overlays applied on top
3. project specs and YAML copied alongside the code

Important status clarification:
- This file documents the bootstrap history of the repository.
- It does **not** imply that every artifact from the original seed package is necessarily versioned in `main`.
- The current canonical code is whatever is actually present in the repository tree.

Repository structure currently intended in `main`:
- `didgeridoo_optimizer/`    operational code
- `project_specs/`    specs, YAML, constraints, material policy
- `results/`   structured calibration / validation artifacts useful for replay and traceability

Not treated as canonical code:
- historical compact snapshots
- temporary workspace state
- artifacts not actually committed in the repository

Notes:
- `REPO_SEED_MANIFEST.json` documents the bootstrap seed and its relation to the current repository contents.
- Validation artifacts in `results/` are useful evidence and replay aids, but they are not a substitute for replaying or rechecking the relevant logic in code.

## Current repo status note

This file describes the bootstrap seed used to initialize the repository.

It does not imply that every path listed in the seed package was versioned in `main` at bootstrap time.

Current `main` is the source of truth for what is actually versioned in the repository tree.