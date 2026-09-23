# PHYS-REF-01 — physical boundary and numerical references

Local execution, 23 September 2026 (Europe/Paris). Base:
`9546a7ba0d949d36360e26e1dae56661f3dcf80e`.
Branch: `codex/phys-ref-01-boundary-reference`.
This note documents numerical checks, not experimental validation or material promotion.

## Block and decision

- Objective: make the physical outlet explicit and identical for solver load and radiation metrics.
- Scope: `acoustics/transfer_matrix.py`, `pipeline/evaluate_linear.py`, the new
  `tests/test_physical_termination.py`, and this document only.
- Inspected: AGENTS, real Git state, geometry models/builder/discretizer/constraints,
  radiation/losses/air/peaks, material database and invariants, YAML materials/rules,
  canonical and internal fixed-design tests, temporal resonator tests, A–E runner/cases/config.
- Repo state: dedicated initially clean worktree at the approved base; main unchanged.
- Validation: red/green real-pipeline spy, direct contract, independent references,
  spatial/frequency convergence, existing targeted suites, paired A–E.
- Commit target: one local coherent PHYS commit; no publication or promotion.
- Status: local implementation and numerical evidence complete; combination is reported separately by the lot runner.

`input_impedance(freq_hz, design, materials, air=None, *, exit_radius_m=None)`
retains all four existing arguments. A supplied radius must be a finite, positive
real scalar, excluding Python/NumPy booleans, complex values, strings and arrays.
It changes only the load. The propagation segments, lengths, areas, alpha, k and
Zc are unchanged. Existing helper numerical protections remain intact.

An unmarked physical design retains the historical last-diameter fallback,
including its numerical protection. A design with truthy `metadata['is_discretized']`
requires an explicit radius. This also applies after re-discretization. If provenance
is removed, the kernel cannot recognize the mesh: supplying the physical radius is
the caller's responsibility. It does not infer an outlet from `local_d_end_cm`.

The real pipeline derives the radius once from the validated built physical design,
before discretization, passes it by keyword to the solver, and reuses that exact value
for `zr`. User metadata such as `exit_radius_m` is an annotation, not authority.
No geometry schema, radiation equation, loss model, score or lip model changed.

AST inspection of **all 61 tracked Python files at the base** (60 package files and
`tools/experimental_nonlinear_sweep.py`) confirms one direct production call,
`pipeline/evaluate_linear.py:62`, plus four direct calls in three test files:
canonical benchmarks, reality/order-of-magnitude, and material loss invariants.
This is a direct-call inventory, not a guarantee about arbitrary dynamic call graphs.

## Conventions and independent references

Convention: exp(+i omega t), forward wave exp(-ikx), U toward the outlet.
Pressure is Pa, volumetric flow m³/s and Zin=p/U is Pa.s/m³. L=length_cm/100,
r=diameter_cm/200, omega=2 pi f. Synthetic air: rho=1.204 kg/m³, c=343 m/s,
20 °C, 50% RH. Synthetic test losses: beta=0 or 2.4, wall_loss=porosity_leak=0;
these numbers do not calibrate a database material.

Frozen load: S=pi r², k0=omega/c,
Zrad=(rho c/S)[(k0 r)²/4 + i k0 0.613 r]. The 0.613 r reactance already
represents the end correction: no duplicate addition to propagation length.
Frozen losses: alpha=1e-5 beta sqrt(omega)/diameter_m times the existing
(1+wall_loss+porosity_leak), k=k0-i alpha, Zc=(rho c/S)(1+i alpha/k0)
for positive omega. This low-frequency radiation approximation is not validated
over the full analysis band by a regression test.

The new analytic cylinder reference directly evaluates the ABCD matrix with
A=D=cos(kL), B=i Zc sin(kL), C=i sin(kL)/Zc and Zin=(A Zrad+B)/(C Zrad+D).
It does not call production loss, radiation or propagation helpers. Both lossless
and beta=2.4 cases are checked at fixed frequencies and three meshes.

The independent lossless cone reference solves Webster with q=x p,
q''+k0²q=0, U=-S p'/(i omega rho), x1=r_in/m, x2=r_out/m,
m=(r_out-r_in)/L. Set U2=1, p2=Zrad, q2=x2 p2,
q2'=p2-i omega rho x2/S2; propagate q,q' analytically to x1 and form p1/U1.
U2=1 only normalizes this linear problem. The zero-slope limit uses the analytic
cylinder separately. The body+pavilion reference composes this exact cone load
with the analytic 1.2 m uniform body, not the discretized production recursion.

Fixtures: cylinder 100 cm/3 cm; cone 140 cm/3.8→12 cm; body 120 cm/3.8 cm plus
20 cm conical or exponential flare 3.8→12 cm. The exponential case uses the actual
GeometryDiscretizer profile with flare_parameter=3:
shape=(exp(3t)-1)/(exp(3)-1), d(t)=3.8(12/3.8)^shape. It is not a Webster cone oracle.

## Reproduction and before/after

Before touching production, the new spy ran the actual pipeline and recorded solver
radiation and metric radiation separately, for all four fixtures and h=1,0.5,0.25 cm.
The same control then passed after the two-file correction. On the conical bell at
h=1 cm, solver radius was 0.058975 m, while metrics used 0.06 m; both now use 0.06 m.
Old reactance excess was **1.738024587%**. The exponential h=1 midpoint was
0.0549763411227 m. The cylinder load remained 0.015 m throughout.

The red run reports `12 failed, 1 passed`: pytest counts the parent unittest method
separately from its failed subtests. Nine tapered subtests reproduce the incorrect
load; three cylinder subtests only flag the previously absent explicit keyword.
This is a successful acoustic execution followed by assertions, not an import error.
The identical green control reports `1 passed, 12 subtests passed`.

The external `phys_diagnostics.py` loads the original transfer matrix and pipeline
sources with `git show` at the base, binds the original pipeline to its original
solver, and compares identical inputs against the corrected modules. Dependencies
are unchanged. It stores all complex fixed-frequency values, API features, validity,
warnings and tracked first-two-mode frequency/amplitude/phase/width/Q for 24 cases
(4 shapes × 2 test losses × 3 meshes). No legacy source is copied into Git.

At h=1 cm, beta=2.4, measured **in this local numerical run**:

| Shape | First mode Hz, before → after | Second mode Hz, before → after | Max fixed-frequency abs delta Zin, Pa.s/m³ |
|---|---|---|---|
| Cylinder | 84.962752320 → same | 254.900972553 → same | 0 |
| Cone | 88.507618606 → 88.512332991 | 194.463067859 → 194.474649839 | 566.3115 |
| Conical bell | 67.654106088 → 67.657656595 | 202.387520194 → 202.401883185 | 9616.4616 |
| Exponential bell | 64.627491422 → 64.644164026 | 193.553543836 → 193.609820120 | 9836.1502 |

For the conical bell's first mode, peak amplitude changes 21868635.734→21869255.313
Pa.s/m³, phase at the peak 0.015960627→0.015960096 rad and half-power Q
47.198180→47.199335. The exponential values are amplitude
21398869.853→21401673.393, phase 0.016298697→0.016296439 rad,
Q 46.173746→46.179435. Tiny phase differences at broad peaks are sensitive to
peak localization roundoff; their last digits are not physical precision claims.

The full API is separately compared on 40..600 Hz/5601 points (0.1 Hz). Its first
mode bins remain respectively 85.0,88.5,67.7,64.6 Hz for both versions, while
sampled fundamental magnitudes change for tapered cases. For example, conical bell
21823945.635→21831196.202; exponential 21382381.182→21359203.979. Thus a rising
true peak can accompany a falling sampled peak. Radiation metrics remain unchanged
because they already used the physical outlet. The cylinder's entire Zin arrays
are bitwise identical before/after. No API score is recalculated in the diagnostic.

## Spatial and frequency convergence

Fixed frequencies are [40,55,70,100,135,180,240,350,500,600] Hz. Complex error is
normalized by max(abs(Zref), inlet rho c/S), avoiding singular relative error at zeros.

| Lossless fixture | h=1 cm | h=0.5 cm | h=0.25 cm |
|---|---|---|---|
| Cone, max normalized complex error | 3.741716661e-4 | 9.351060788e-5 | 2.337563355e-5 |
| Body+conical bell, same | 4.497943723e-3 | 1.127617157e-3 | 2.821005714e-4 |

The reduction is approximately four per halving, consistent with midpoint sections.
Tests allow successive error ratio <0.35 around the expected 0.25 and final error
<0.002. These are numerical budgets, not empirical tolerances. Cylinder matrix
agreement uses rtol=2e-11, atol=2e-6 Pa.s/m³ for accumulated roundoff across ≤400 slices.
Complex self-convergence at the same physical radius also reduces by about four for
the exponential profile and both beta values; it does not constitute an independent
lossy or exponential physical validation.

Tracking the same lossless conical-bell modes, after correction:

| h cm | Mode 1 Hz | Mode 1 amplitude | Mode 1 Q (half-power) | Mode 2 Hz | Mode 2 Q (half-power) |
|---|---|---|---|---|---|
| 1 | 67.661274113 | 2.529779668e9 | 5459.985962 | 202.406295799 | 1350.958813 |
| 0.5 | 67.657932691 | 2.530229683e9 | 5460.936467 | 202.399649620 | 1351.962938 |
| 0.25 | 67.657094084 | 2.530342520e9 | 5461.174800 | 202.397979848 | 1352.214471 |
| Independent reference | 67.656814255 | 2.530380163e9 | 5461.254298 | 202.397422530 | 1352.298361 |

Mode 2 peak phase converges 0.001008146→0.001006817→0.001006578 rad, reference
0.001006368 rad. Mode 1 peak phase is about 0.000190 rad; smaller changes are at
the localization/roundoff scale and are not claimed to converge monotonically.
At h=1 the old first frequency 67.657723270 is accidentally closer to the reference
than the corrected value: outlet error can compensate mesh error. It is not a reason
to retain an incorrect boundary.

Frequency resolution is then varied with h fixed at 0.25 cm. Bounded 17-point local
searches follow the same two modes; half-power crossings are bisected 36 times.
First-mode width is 0.0123887435 Hz, hence a 0.1 Hz API grid cannot resolve its Q.
At 16/32/64 bins per width, deliberately offset from the maximum, relative Q errors
are 0.00303138/0.00075942/0.00018950, amplitude errors
0.00106782/0.00026728/0.00006684, and peak-phase errors
0.0462171/0.0231209/0.0115620 rad. The second mode likewise converges.

Here Q=f_peak/(f_right-f_left) at absolute abs(Zpeak)/sqrt(2). The API's existing Q
uses half-prominence **magnitude** and bin crossings, so the two Q values are not
interchangeable. No peak detector golden is fitted to earlier pilot numbers.

An initial test refinement used nine local steps: its 1.490116e-7 Hz peak bracket
was wider than the declared 1.238874e-7 Hz budget. That failed trace is retained.
Increasing only the bounded test search to ten steps satisfied the original budget;
no production formula or tolerance was changed to hide the failure.

## Tests and evidence locations

Worktree: `<PHYS_WORKTREE>`.
Evidence root: sibling `..\batch-01`. Python:
`<PYTHON_EXECUTABLE>`;
machine `<LOCAL_TEST_MACHINE>`, Python 3.13.1, NumPy 2.2.2, PyYAML 6.0.2,
pytest 9.0.3. No SciPy needed or installed.
Every command is wrapped by `batch-01\Run-Checks.ps1` with explicit worktree/Python,
base verification, timeout, process-local TEMP/TMP/Python/pytest cache directories and exit capture.
Optional Matplotlib cache redirection was added by the lot runner after initial runs;
its earlier routing was not verified and no user cache was inspected or cleaned.
Each `report.json` records the exact argv, HEAD/base, dirty state and file hashes,
versions, test counts, log paths and timeout. UTC evidence timestamps fall on 22 Sep;
local Europe/Paris execution is 23 Sep. Logs and scripts are external to the commit.

| Command payload / label | Result | Evidence below batch-01 |
|---|---|---|
| `-m pytest -q -s didgeridoo_optimizer/tests/test_physical_termination.py -k pipeline_passes_physical_radius` / red-spy | exit 1, 12 failed subtests, parent counted passed | `PHYS-REF-01/20260922T234507.807825Z-red-spy/` |
| Same / green-spy | exit 0, 1 passed +12 subtests | `PHYS-REF-01/20260922T234554.327627Z-green-spy/` |
| New test file / boundary-reference-tests | exit 1, 1 failed +9 passed +27 subtests | `PHYS-REF-01/20260922T234819.961390Z-boundary-reference-tests/` |
| New test file / boundary-reference-refined | exit 0, 10 passed +27 subtests | `PHYS-REF-01/20260922T234836.286336Z-boundary-reference-refined/` |
| New test file / boundary-reference-complete | exit 0, 11 passed +27 subtests | `PHYS-REF-01/20260922T235126.637733Z-boundary-reference-complete/` |
| `-m pytest -q` with six existing targeted files listed below | exit 0, 58 passed +67 subtests | `PHYS-REF-01/20260922T235037.254692Z-targeted-existing/` |
| `phys_diagnostics.py --repo <worktree> --output <batch-01/phys_diagnostics.json>` | exit 0, 24 comparisons; no pytest count | `PHYS-REF-01/20260922T235013.926833Z-diagnostics/` and `phys_diagnostics.json` |
| `phys_inventory.py` | exit 0, AST assertions for all 61 Python files | `PHYS-REF-01/20260922T235251.292352Z-ast-inventory/` |

Existing files: `test_material_losses_invariants.py`,
`test_linear_acoustics_canonical_benchmarks.py`,
`test_linear_acoustics_reality_order_of_magnitude.py`,
`test_fixed_design_internal.py`, `test_objective_activation.py`,
`test_time_domain_resonator_scaling.py` (all in `didgeridoo_optimizer/tests`).
The strict original physical-fixture legacy equivalence tests, dictionary and real
MaterialDatabase paths, are unchanged and pass. There are no skips in these runs.

The lot runner reads A–E before invocation and calls the real function with explicit
repository `CONFIG_TEMPLATE_V1.yaml` and materials/rules paths (not its `/mnt/data`
CLI defaults). Baseline and corrected runs each evaluate 13 cases and pass all 20
implemented checks. Evidence: `BASELINE-AE/20260922T234559.917060Z-base-ae/` and
`PHYS-AE/20260922T234901.798911Z-corrected-ae/`. Geometry/validation policy is unchanged.
The binned A–E pass is a trend regression, not a resolved-Q or experimental proof.

## One external fiche — R08

**Sourced geometry:** Tarnopolsky et al., JASA 119 (2006), 1194–1204,
DOI [10.1121/1.2146089](https://doi.org/10.1121/1.2146089).
[Authors' publication PDF](https://www.phys.unsw.edu.au/~jw/reprints/Tarnopolskyetal.pdf),
section II.A, p.1196: the acoustic cylindrical PVC tube is **1210 mm long, 30 mm
internal diameter**. The optical apparatus is a separate square tube, 1220 mm long
and 38 mm internal width; it must not replace the acoustic geometry.

Figures are accessible in the publication/dossier, but no raw numerical series was
received. No redistribution licence was verified. Conditions and normalization
must be checked separately for each figure before numerical comparison. Current
use is a sourced test geometry, not agreement with a supposedly received measurement.
No figures were downloaded/copied into Git and no authors were contacted.

Context only: R07, Fletcher et al., JASA 119 (2006),1205–1213,
[DOI 10.1121/1.2146090](https://doi.org/10.1121/1.2146090),
[authors' PDF](https://www.phys.unsw.edu.au/jw/reprints/Fletcheretal.pdf), equation (4),
p.1208, supports scalar alpha≈1e-5 beta sqrt(omega)/d. It does not validate D-Calc's
material coefficients, wall/porosity multiplier or k/Zc pairing. R10
[DOI 10.1121/10.0004303](https://doi.org/10.1121/10.0004303) announces supplemental
material; that supplement was not retrieved and supplies no raw benchmark here.

Numerical reference conclusions are inferred from the stated equations. Provisional
material parameters remain `to_calibrate`/`inferred` according to their existing
database status. Zin p/U is neither static pressure, a played FFT nor an inlet/outlet
transfer. A resonance ratio does not prove a toot threshold or playing ease. Future
thermoviscous work, calibration, optimization sweeps and material promotion are outside
this change. These local files/results are not automatically published on GitHub.
