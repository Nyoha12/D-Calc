# FIXED-01 — fixed-design linear evaluation contract

Implemented locally for R5 (23 September 2026), replacing the earlier workflow draft.
R7 tightens DESIGN ambiguity/annotation checks and software-source attribution;
the acoustic calculation and generic exporters are unchanged.
The code and tests are authoritative. This workflow supplies no experimental validation
or material promotion.

## Invocation and routing

```powershell
python -m didgeridoo_optimizer.pipeline.run_optimizer --config CONFIG --design DESIGN --dry-run
python -m didgeridoo_optimizer.pipeline.run_optimizer --config CONFIG --design DESIGN --output-dir SORTIE
```

Without `--design`, the existing optimizer route and its `dcalc.optimizer.*` contracts
are unchanged. With `--design`, only `linear_fixed_design` runs. Configured optimization,
Pareto selection, ranking, robustness, nonlinear simulation and runtime estimation are
explicitly listed as `not_executed`. No estimator performs hidden evaluations.

`--design` is resolved directly from the caller's current directory to an absolute path,
which is exposed in the CLI response and provenance. No same-name fallback applies.
Config loading, schema version checking, material-path resolution and output-path rules
reuse existing `OptimizerRunner` read helpers through a thin adapter; `load_context`,
`run` and `finalize` are never called. Relative output paths remain relative to the
config file, including the CLI output override, as in the optimizer.

## Physical design file

DESIGN is safe YAML (`.yaml`/`.yml`) or standard JSON (`.json`), containing one mapping:

```yaml
id: fixed_linear_smoke   # optional; deterministic default: fixed_design
segments:
  - kind: cylinder
    length_cm: 100.0
    d_in_cm: 3.0
    d_out_cm: 3.0
    material_id: pvc_pressure
metadata:
  note: physical profile supplied by caller
```

`segments` must be a nonempty list. Every segment requires `kind`, `length_cm`,
`d_in_cm`, `d_out_cm`, `material_id`. Optional `profile_params` is a mapping;
optional `position_start_cm` and `position_end_cm` are checked for finite numeric
values and then recomputed by `DesignBuilder`. IDs must be nonempty strings.
Lengths and diameters must be finite, strictly positive real numbers; numeric strings,
booleans, null, NaN and Infinity are rejected with field paths.

Supported kinds are mouthpiece, cylinder, cone, flare_conical, flare_exponential and
flare_powerlaw. Branches and Helmholtz necks are explicitly rejected because the
linear discretizer does not implement them. Profile keys follow existing consumers:
`throat_diameter_cm` for mouthpiece (positive); `flare_parameter` for flares, plus
`power` for flare_powerlaw (finite; existing discretizer and validator retain their
own policy). Other profile keys, segment keys and top-level keys are rejected.
Extra annotations belong in `metadata`. Every nested mapping, including mappings
inside lists, must have textual keys. Values are mappings, lists, strings, booleans,
integers, finite floats or null; tuples, dates, sets, bytes and nonfinite values are
rejected with field paths. Keys are never coerced to strings: YAML keys `1` and
`"1"` cannot silently lose an annotation during export. Lists, nulls and valid
annotations are preserved. Cycles are rejected; shared acyclic aliases are allowed.
Metadata is preserved as annotation, not trusted as an acoustic outlet.
The builder still computes its derived total length.

R7 explicitly adds rejection of duplicate keys at every DESIGN level in both
YAML and JSON (this was not an explicit R5 rule). YAML node inspection occurs
before constructing dictionaries, so nontext keys such as `1` and `true` cannot
collapse first. YAML merge directives (`<<`) are rejected even when a particular
merge would not collide: write explicit unique keys instead. An ordinary quoted
textual `"<<"` annotation key is allowed. Parsing retains `SafeLoader` semantics;
Python object tags are never executed. JSON object-pair checks reject duplicates
inside nested objects and lists. Errors identify the source file and key or field;
YAML key diagnostics also give a line. Config/material readers keep their existing
policies; these stricter rules apply only to DESIGN.

`metadata.is_discretized` truthy is rejected: supply the physical profile, not an
`analysis_design`. A mesh whose provenance was removed cannot be recognized reliably.
Optimizer bundles, arbitrary x/radius imports and manufacturing formats are out of scope.

Every material ID is resolved through the real `MaterialDatabase.get`, including
available generated variants. The existing optional variant-rules policy is retained:
a missing resolved rules file produces a warning and no fallback rules generator;
base materials can still resolve, whereas an unavailable generated variant fails.
The materials database itself must load successfully. No coefficients are changed.
After schema validation, the existing `DesignBuilder` and `GeometryValidator` apply
geometry policy. Invalid geometry fails before calculation and export.

The analysis boundary also checks positive finite f_min, f_max > f_min, integer
n_points >= 2 (not bool), positive finite spatial step, air density and sound speed.
Temperature and humidity must be finite. Omitted parameters retain the linear API's
defaults; this is not a rewrite of global config validation.

## Dry-run, calculation and failure semantics

Dry-run loads the design, config and real material database, resolves all material IDs
and validates geometry and analysis parameters. It makes zero acoustic calls and
creates no output directory or artifact, including on failure. Its `geometry_valid`
flag does not pretend that acoustic hard constraints were evaluated.

A normal successful run calls `LinearEvaluationPipeline.evaluate` exactly once.
The physical design and discretized `analysis_design` remain distinct. Scores,
features, penalties, peaks and API warnings are exported without rescoring.
`ok=true` means the calculation and bundle export completed. `valid` is the API's
model-constraint result; completed acoustics with `valid=false` is exportable.
An invalid input never calculates or creates output.

Load, calculation, serialization or I/O errors produce CLI exit code 1 and an
explicit `error:` message on stderr, with no successful bundle announcement.
All curves must be finite, one-dimensional, aligned with n_points. A nonfinite or
misaligned Zin/frequency/magnitude curve is a calculation failure. Other unavailable
API diagnostics are represented by null and a path-specific reason under
`unavailable_values`; list indices are preserved. Existing null diagnostics also
have a reason. This conversion does not modify the in-memory API result.

JSON and YAML serialization and summary preparation are validated before creating
outputs. JSON contains no NaN or Infinity. An existing file with a bundle name is
not overwritten. A later I/O failure can leave a partial new bundle, but returns a
failure and never announces success; inspect that directory before retrying.

## Outputs and provenance

Exactly three neutral files are written:

- `evaluated_design_result.json`
- `evaluated_design_result.yaml`
- `evaluated_design_summary.txt` (including interpretation)

Result contract: `schema_version: dcalc.fixed_design.result.v1`,
`workflow: linear_fixed_design`. The payload includes the complete linear `result`,
physical/analysis designs, frequency grid, complex Zin as aligned `{real, imag}`
objects, valid/errors/warnings, effective frequency/discretization/air parameters,
config, material definitions actually used with unchanged statuses, unavailable-value
reasons and explicit unexecuted phases. Generic dataclass/NumPy/complex conversion
and JSON/YAML exporters are reused without modifying their optimizer contracts.

Provenance records resolved config, design, material database and optional variant
rules paths, SHA-256 fingerprints and whether the files were read. Fingerprints are
checked before/after loading; a changing input is rejected. Missing optional rules
have `sha256: null`, `read: false` and a reason. Software SHA comes from bounded
Git queries only after the resolved package root matches the Git worktree root and
the actual loaded D-Calc Python source files are verified as tracked there. Git
worktrees with a `.git` file are supported. Ignored/untracked copies below a foreign
repository, partially tracked copies and mixed source roots do not inherit its HEAD.
The whole-worktree dirty flag accompanies an established HEAD; a dirty HEAD is not
an exact identity of uncommitted code. No remote identity or byte-for-byte identity
of in-memory code is claimed by this membership check. If Git fails, files are not
available as tracked Python sources or membership cannot be established, SHA and
dirty are null with an explicit origin. This does not prevent an otherwise valid
evaluation/export. No credentials or environment dump is exported.

The summary states that `valid` is not experimental validation, Zin = p/U in
Pa.s/m³ is input acoustic impedance (not static pressure, played FFT or input-output
transfer), and the first-two-resonance ratio is not a toot threshold or ease-of-toot
claim. Material statuses remain unchanged; no export establishes calibration.

## Verification

Tests in `test_fixed_design_input.py` and `test_fixed_design_cli.py` cover YAML/JSON
parity, field-path errors, adversarial values, safe parsing, exact design paths,
geometry-policy reuse, optional variant rules, dry-run no evaluation/no output,
phase sentinels, exactly one API call, complete API/export parity and strict
serialization. API parity includes the internal 100 cm / 3 cm PVC cylinder fixture
(rho=1.204, c=343, 40..600 Hz, 128 points, h=5 cm) and a tapered cone at the same
running source revision. There are no goldens tied to the old termination defect.
Real module CLI subprocesses run both YAML and JSON designs. Tests preserve input
bytes, exercise valid=false, partial/unavailable diagnostics, nonfinite curves,
misalignment, preflight failures and nonzero error exits. The unchanged historical
CLI suite is replayed alongside the new tests.

R7 regressions exercise nested nontext keys, exact annotation export, cycle versus
alias handling, duplicate keys at every schema level, YAML merge refusal and
dry-run/normal rejection before acoustics or output. Provenance fixtures create
temporary local Git repositories and a detached worktree, covering clean/dirty
sources, unrelated ignored/untracked copies, partially tracked packages and Git
absence/failure; all Git operations stay in the test temporaries. Git-unavailable
coverage also completes a real API evaluation and exports null software provenance.
Before correction, F1 was reproduced with the real `MaterialDatabase` and linear
API on the 128-point internal cylinder; accepted integer/textual keys collapsed
during payload conversion. Exact regression sources and red/green logs are kept in
`D-Calc-lab/batch-01/review-r7`, including any initial fixture setup failures.

Local run logs and exact source/diff fingerprints are kept outside the worktree in
`D-Calc-lab/batch-01/FIXED-01`; they are execution evidence, not physical validation.
The combined PHYS/FIXED parity replay belongs to the separate integration worktree.
