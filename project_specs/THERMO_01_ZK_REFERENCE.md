# THERMO-01: opt-in cylindrical Zwikker–Kosten reference

Implementation and local evidence, 2026-09-23. This is an internal linear
reference and diagnostic, not a default-model change, experimental validation,
material calibration or adoption. Base: `13d3801be4cb2baf2578b24e4aeddf981da37d9a`,
tree `524c7c948e1429ce7977e16ce39c23d75ba4966e`.

The complete code/test revision executed for the final checks and comparisons
is `d5adb9ed81071403bed91fdee8efe5d9f67c5359`. This note is added afterwards;
the publication report/PR identifies its documentary descendant. No acoustics
test is claimed to have run on a later documentary SHA merely because its code
and test blobs are identical.

## Scope and use

`acoustics/thermoviscous.py` supplies immutable explicit air states,
`zk_coefficients(omega, diameter_m, state)`, the continued fraction, and
`ZwikkerKostenLossModel`. `input_impedance` has one additional keyword-only
argument, `loss_model=None`. The existing `DEFAULT_LOSS_MODEL` is selected
exactly when this argument is None. No global object is replaced. Only the
loss-model invocation in its segment loop changes; radius validation, physical
radiation radius, propagation sections/lengths and radiation formula are intact.
The public linear pipeline and fixed-design workflow still use legacy losses.
The component-v2 skeleton is unchanged.

```python
from didgeridoo_optimizer.acoustics.thermoviscous import (
    CK_DRY_20C, ZwikkerKostenLossModel,
)
from didgeridoo_optimizer.acoustics.transfer_matrix import input_impedance

state = CK_DRY_20C
# physical_design and mesh are distinct; materials is the existing database.
zin = input_impedance(
    frequency_hz, mesh, materials, state.as_air_properties(),
    exit_radius_m=physical_design.segments[-1].d_out_cm / 200,
    loss_model=ZwikkerKostenLossModel(state),
)
```

The adapter returns actual alpha/k/Zc and a single `air_thermoviscous`
component. It does not inspect beta, wall_loss or porosity_leak, and never calls
legacy loss helpers. A beta=0 fixture is lossless only in legacy. Warnings say
material effects are **omitted**, not measured zero. Formula and nominal CK
equations are sourced; applying a rigid, smooth, sealed-wall reference to real
walls remains inferred/to_calibrate. No parameter status promotes a material.

## Equations, units and assumptions

Convention: exp(+j omega t), forward wave exp(-j k x), volume flow U towards the
outlet. p is Pa; U is m³/s; Z=p/U is Pa.s/m³. omega=2 pi f in rad/s,
a=d/2 in metres, S=pi a². rho [kg/m³], c [m/s], mu [Pa.s],
kappa [W/(m.K)], cp [J/(kg.K)], gamma dimensionless.

```
qv = a sqrt(-j omega rho/mu)
qt = a sqrt(-j omega rho cp/kappa)
F(q) = 2 J1(q)/(q J0(q)); G(q) = 1-F(q)
zv = 1/G(qv); yt = 1+(gamma-1)F(qt)
Q = sqrt(zv yt)
k = (omega/c) Q
Zc = (rho c/S) zv/Q
alpha = -Im(k)
Zprime = j omega rho zv/S
Yprime = j omega S yt/(rho c²)
```

The **same** square root Q is used in k and Zc. No conjugation/sign repair is
applied. On the supported physical inputs Re(k)>0, Im(k)<0 and Re(Zc)>0;
j k Zc=Zprime and j k/Zc=Yprime. The model assumes resting air, circular
sections, quasi-uniform pressure, dominant radial diffusion, rigid smooth sealed
walls, zero wall velocity and acoustic temperature. It adds no mean flow,
molecular relaxation, bulk viscosity, axial conduction, effective wall loss,
WKB correction or end-length duplication.

The thermal denominator is **qt**. The section average of
J0(q r/a)/J0(q) is (2/a²) integral(r J0(q r/a)/J0(q),r=0..a)=F(q).
The mean thermal profile relative to the adiabatic solution is 1-F(qt), giving
yt above. T01 HTML eq.24 displays kvR in that denominator; its PDF was not
visually inspected here or by the preparation. This is an explicit derivation,
not a claim that a printed typo has been certified.

### Numerical method and domain

Independently implemented NIST DLMF10.10.1, not copied third-party code:
J1(q)/J0(q)=q/[2-q²/(4-q²/(6-...))]. For depth N, D_N=2N;
descend D_n=2n-q²/D_(n+1) to n=2; t=-q²/D2;
F=2/(2+t), **G=t/(2+t)**. Thus small G is not obtained by subtracting F from1.
q²=-j St² on this ray. Depths16,32,...8192 require relative successive
convergence of **both** F and G at2e-13. Failure is explicit; no fallback,
nan_to_num or unconverged coefficients are returned.

Tested/supported St interval: [1e-5,1e4], for each viscous and thermal argument.
It is not a universal Bessel implementation. Both arguments must lie in that
interval for the pair. Angular frequency must be strictly positive: k and Zc
separately are singular at zero. Scalar/1D shapes are preserved; booleans,
complex inputs, multidimensional/empty inputs, nonfinite values and nonpositive
physical coefficients are rejected. gamma>1, T>0K, RH in[0,100].
The production solver's existing frequency coercion and legacy zero-frequency
behavior are not changed by this adapter.

Matching AirProperties rho/c/T/RH and nominal rho*c/S are checked at relative
1e-12 (absolute1e-12 only for air fields). This admits binary64 arithmetic and
decimal round trips, not an alternative air state. The original AirProperties
defaults are neither recalculated nor overwritten.

### Explicit nominal air

CK_DRY_20C and CK_DRY_25C use T=273.15+temperature_c:

```
cp=1004.16; gamma=1.402
rho=1.2929*273.15/T; c=331.45*sqrt(T/273.15)
mu=1.708e-5*(1+0.0029*temperature_c)
kappa=0.00577*(1+0.0033*temperature_c)*4.184; RH=0
```

These are named dry nominal references, not new global defaults or humidity
laws. An explicit `ThermoviscousAir` constructor also accepts all properties,
an identifier and provenance. CK equations in T03 ignore humidity/CO2; no
RH50 simulation or invented uncertainty is claimed. T03 is documentary source
code attributed there to Chaigne–Kergomard2016, chapter5 p.241, without a fixed
Openwind SHA. No GPL implementation was copied, installed or executed.

## Diagnostic contract and reproduction

Run from the checkout. `EVIDENCE` below denotes a new caller-selected local
directory outside the checkout; these are normalized commands, not personal
machine paths. Outputs refuse overwrite and use their own
`dcalc.thermo.comparison.v1` schema: JSON, aligned CSV and text summary.

```
python -B -m tools.thermo_reference_compare --air-reference ck_dry20 --output-dir EVIDENCE/synthetic-01
python -B -m tools.thermo_reference_compare --config CONFIG --design DESIGN --air-reference ck_dry25 --spatial-steps 1 .5 .25 --output-dir EVIDENCE/design-comparison
python -B -m tools.thermo_reference_compare --acquire-external --output-dir EVIDENCE/external-01
```

`--config/--design` uses load_fixed_context, the original builder/discretizer and
input_impedance; it does not run the linear pipeline's scoring phases or the
optimizer. Original validated config, effective parameters, annotations and
input fingerprints remain in a separate context. The explicit diagnostic air
substitution reports all six effective coefficients plus T/RH. User inputs are
not modified; real geometry/metadata outputs remain local by default.

Built-ins: cylinder1.210m/30mm; body1.2m/38mm plus conical flare0.2m/38→120mm;
closed cylinder0.180m/14mm. Both models share geometry, air, frequency grid and
physical outlet. Synthetic legacy beta=2.4, wall/porosity=0 is a test material,
never a database change. For the closed tube the diagnostic uses analytic A/C
(Uout=0), without invoking/expanding the production radiation API. Repeated h
levels there are identical analytic evaluations, **not spatial convergence**.

The default survey grid is40..1200Hz,1161 points. Spatial levels1,.5,.25cm use
that same grid. At each fixed spatial level, first three bracketed modes are
sampled independently at1025 then2049 points in their local windows. Internal
frequency means maximum |Zin| (quadratic log-magnitude vertex); phase and
half-power Q are separate observables. At least11 intervals across the
half-power width are required for Q, otherwise null plus a reason. This gate
is a numerical resolution rule, not empirical accuracy. The changes between
refinements are retained, not treated as exact error bars.

The external benchmark uses a different definition: a linear phase-zero fit
and quadratic log-magnitude fit within±5cents, with at least11 observations.
No interpolation manufactures missing observations. Closed-tube DC compliance
is excluded from mode brackets. Matching is by frequency order/bracket, not a
guarantee that an arbitrary user's initial grid found every mode. Budgets are
100000 frequencies,10000 slices, at most4 spatial levels and10 modes; fine
meshes can be expensive. No runtime prediction launches hidden evaluations.

## Executed numerical evidence

Windows, Python3.13.1, NumPy2.2.2, PyYAML6.0.2, pytest9.0.3,
Matplotlib3.10.0. SciPy absent; neither SciPy nor mpmath is required/installed.
Local PowerShell harness: checked base/branch, explicit paths, process-local
TEMP/TMP/PYTHONDONTWRITEBYTECODE/PYTHONPYCACHEPREFIX/MPLCONFIGDIR, pytest cache
disabled, short fixture paths, bounded child processes, exit codes and source
hashes before/after. Final suite exit0, stable sources, clean tested HEAD.

At `d5adb9ed81071403bed91fdee8efe5d9f67c5359`: **375 tests passed and129
subtests passed, no failures/skips**,56.98s. Counts are not added together and
overlapping earlier runs are not summed. Exact normalized command:

```
python -B -m pytest -q didgeridoo_optimizer/tests/test_thermoviscous.py didgeridoo_optimizer/tests/test_thermo_reference_compare.py didgeridoo_optimizer/tests/test_physical_termination.py didgeridoo_optimizer/tests/test_material_losses_invariants.py didgeridoo_optimizer/tests/test_linear_acoustics_canonical_benchmarks.py didgeridoo_optimizer/tests/test_linear_acoustics_reality_order_of_magnitude.py didgeridoo_optimizer/tests/test_fixed_design_internal.py didgeridoo_optimizer/tests/test_fixed_design_input.py didgeridoo_optimizer/tests/test_fixed_design_cli.py didgeridoo_optimizer/tests/test_run_optimizer_cli.py
```

The supplied fixture contains24 k/Zc pairs and11 F/G points from independent
70-digit mpmath calculations, plus9 cone ODE values, read without those
dependencies. Maximum observed relative pair error4.014e-16; maximum F/G
real/imag component relative error1.589e-13. Acceptance remains1e-10; none was
relaxed to fix an unexplained failure. Tests also cover low-diffusion/asymptotic
limits, Poiseuille8mu/(pi a⁴), isothermal gamma/(rho c²), telegraphist identities,
positive Re(Zprime)/Re(Yprime) and integrated Pin−Pout dissipation in six
air/diameter states. A determinant-only test would be insufficient.

Loaded-cylinder analytic equivalence/subdivision uses rtol3e-12 plus absolute
1e-7Pa.s/m³ for accumulated roundoff. The supplied independent DOP853 cone
reference used rtol2e-12/atol1e-13, stability3.2395e-12 relativeL2; it is a 1D
ODE reference, not a3D measurement. ZK complex-Zin relativeL2 errors at
h=1,.5,.25cm are0.00278831436,0.000696463415,0.000174078394: consistent with
second-order midpoint spatial error. The2e-4 finest-mesh threshold is a
numerical convergence bound, not experimental tolerance. Amplitude monotonicity
is not asserted universally. Legacy default versus explicit LegacyBetaLossModel
is bit-identical, including interleaved ZK calls, dictionary/real database and
unchanged mesh/radius checks; existing goldens were not changed.

The runner A–E and its real configuration were read. A–E was not replayed:
the pipeline/default path remains legacy and the targeted regressions cover
the touched interface. Historical A–E/PHYS/FIXED counts are not relabelled as
THERMO results. No full nonlinear run, optimization, calibration or sweep.

Early evidence is retained locally: a PowerShell quoting/version probe was
fixed; fixtures were corrected for identical binary64 diameter conversion and
Windows ZIP-name normalization; untracked loaded modules initially caused the
existing FIXED provenance guard to return null. Tracking the new files resolved
that guard without modifying FIXED. Review also strengthened ZIP inspection of
original names and excluded closed-tube DC compliance from peak brackets. Final
green logs coexist with earlier failures; one early clean-run harness omitted
an empty diff file, corrected for subsequent runs. No old evidence was replaced.

### Synthetic comparison on the tested commit

CK20, fixed physical radius, h=.25cm for open profiles,2049-point local mode
refinement. First-mode values below are numerical diagnostics, not targets or
measured frequencies. |Z| units are Pa.s/m³; phase is radians at max|Z|.

| Case/model | f max Hz | magnitude | phase | half-power Q |
| --- | ---: | ---: | ---: | ---: |
| Cylinder legacy |70.403210|2.8403724e7|0.0196876|38.1136|
| Cylinder ZK |69.514540|2.9142591e7|0.00179089|38.6883|
| Body+bell legacy |67.726473|2.1893156e7|0.0159351|47.1641|
| Body+bell ZK |67.049430|2.2868730e7|0.00150769|48.8390|
| Closed cylinder legacy |953.777719|1.1251604e8|0.0114607|65.7638|
| Closed cylinder ZK |946.678100|1.1435879e8|0.000983747|66.4157|

ZK-minus-legacy shifts:−21.9917,−17.3938,−12.9350cents, respectively.
These are this execution's results, not copied prototype goldens. At fixed
h=.25cm,1025→2049 local samples changed ZK first frequencies by
+0.0000981,−0.0001689,+0.0026119Hz and Q by+0.01350,+0.01984,+0.04749.
Spatial refinement at fixed frequency procedure changes the bell ZK first
frequency67.053636→67.050273→67.049430Hz; the uniform cylinder is invariant
within roundoff. Local phase-zero interpolation is separately exported; for
example the cylinder ZK gives69.516152Hz, distinct from its magnitude maximum.

One sequential timing observation,1161-point curves at finest h (seconds):
cylinder legacy0.0311/ZK5.004; body+bell0.1002/8.745; closed analytic0.000157/
0.00353. Mode-extraction times were respectively0.225/32.73,0.710/29.59,
0.000653/0.00891. This implementation recomputes constitutive factors per slice;
it is materially slower than legacy, with no equal-performance claim or timing
confidence interval. Full bounded three-case comparison completed successfully;
no global sweep was used.

## External acquisition and held-out comparison

Actual public access, not merely announced availability: Zenodo20024938 v2,
4May2026, API and README received; Raw_data.zip received in **one** attempt,
126468888bytes, MD5`392ff6e6e5c29985513c5dfd5d26998b` matched.
SHA256`724b96be96e210e52e939ecceb78d919df46b9001af89defc5912118f8584fcd`.
Metadata explicitly declares dataset licence`cc-by-4.0`, independently of the
article's licence. Metadata and original checksums are retained locally.

Acquisition permits only the cited HTTPS Zenodo host, rejects foreign redirects,
limits archive200MB, one attempt per invocation,180s download budget and a
bounded enclosing process. No second archive attempt was needed. Inventory is
checked before extraction: maximum10000 entries/800MB expanded/50MB per member,
no traversal, ambiguous Windows paths, case collisions, symlinks or encrypted
members. Four impedance files were selectively extracted, never executed. No
downloaded script, Openwind installation or authentication was used. Gitlab
returned an explicit Anubis access denial; no bypass was attempted. No raw
archive, measurements, original tables or logs are included in Git.

Common fiche source: DOI10.5281/zenodo.20024938, v2; CC BY4.0 from metadata;
closed termination; held-out/no coefficient or geometry adjustment. Each file
has three frequency/ReZ/ImZ columns and **no header**. Relative received paths
and SHA256:

| Relative archive path | Nature | SHA256 |
| --- | --- | --- |
|Raw_data/Simulated_Impedance/Cylinder_closed/Cylinder-closed_Operator-A_TMM_ZK.txt|simulation|30bcb622ba9ae8ce864ab0a4eeec3c52f418047dda21096b3d2950d9a90d506f|
|Raw_data/Simulated_Impedance/Cylinder_closed/Cylinder-closed_Operator-C_TMM_ZK.txt|simulation|3a4463c22ea22e49550e74d00bc68f5a601ca357ea5a7eb9f530c282cfbba33b|
|Raw_data/Measured_Impedance/Experimenter_O1/Brass_C/O1_25dC_dry_Brass_C_1-1.txt|measurement/repetition1|9fdf4ac8be0cb2c45f5a28bfe7d4c745c9ee6a756d6d20fa57e1cf42b7dcaf10|
|Raw_data/Measured_Impedance/Experimenter_O1/Brass_C/O1_25dC_dry_Brass_C_1-2.txt|measurement/repetition2|f58f28b2f5687037a5478b97f962e1243659461a0cc3ad28427c142cdca36a5b|

Simulations:100000 points,.2..20000Hz,.2Hz spacing, dimensional Pa.s/m³;
nominal L=.180m/ID=.014m, dry CK25 (published constants rounded to eight
significant figures). OperatorC TMM is the article's analytic ZK reference.
The time sign is inferred from its passive Eq9/negative low-frequency closed
reactance and retained data signs; an explicit author's exp(±jwt) statement
was not found. No conjugation was applied. Comparison is conditional on this
documented convention interpretation, not a newly certified transcription.

Actual relative complexL2 against simulations, no fitting:

| Received reference | Band Hz | Legacy beta2.4 | ZK |
| --- | --- | ---: | ---: |
|A TMM ZK|.2..20000|0.778765|0.0114063|
|A TMM ZK|40..3000|0.635993|0.00183868|
|C TMM ZK|.2..20000|0.788003|1.06718e-7|
|C TMM ZK|40..3000|0.636733|1.18051e-7|

No1e-10 fixture tolerance is imposed on externally rounded files. A's larger
deviation is retained, not adjusted away. With the benchmark phase-fit protocol,
our CK25 ZK first phase-zero frequency is954.644248Hz and Q65.6804; these use
different air and a different frequency definition from the CK20 internal
closed-cylinder maximum above. They must not be conflated.

O1 measurements:9951 points each,50.32526..10065.05Hz, dimensionless Z/(rho*c/S).
Names declare25C/dry correction; README describes that correction, but per-file
original T/RH and exact correction coefficients are absent. Article table2
reports brass length180.0±0.3mm and ID13.92±0.03mm, a measured mean, not an
identified specimen-specific geometry. The normalization diameter (14mm nominal
versus measured diameter) is not established. Author time convention is also
unknown. Thus **no absolute measured-amplitude/model comparison or experimental
validation is claimed**. Repetitions remain separate. First phase-fit windows
have only6/5 observations, so the benchmark first phase frequency is unresolved;
no interpolation creates the required11 observations. Raw normalized exploratory
metrics remain local with dimensionless amplitude labels. Open unflanged files
were not used as experimental evidence.

To reproduce selective extraction, use `inspect_archive` then
`extract_selected(archive_path, output_directory, [exact_relative_member])` in
the tool; both enforce the same inventory/path rules. To compare a received
simulation, prepare a local JSON fiche with source/doi/version/relative_path,
nature=`simulation`, the SHA above, units=`Hz, Pa.s/m3 ReZ/ImZ`,
normalization=`dimensional Pa.s/m3`, convention=`exp(+j*omega*t)` plus the
inference stated above, geometry={length_m:.18,diameter_m:.014,status:nominal},
environment=the eight CK_DRY_25C fields, termination=`closed`, licence,
method and role=`held-out; no adjustment`. Then:

```
python -B -m tools.thermo_reference_compare --case closed_cylinder --air-reference ck_dry25 --points 64 --spatial-steps 1 --max-modes 0 --external-data RECEIVED_FILE --external-fiche FICHE.json --output-dir EVIDENCE/external-comparison
```

The reader verifies file fingerprint, units, shape, monotone positive frequency,
normalization, matching declared air and nonzero finite curve before comparison.
Incomplete conditions fail explicitly; it performs no guessed temperature
correction. Detailed fiches/results, commands, source snapshots, all red/green
logs and raw data remain in the new local evidence directory, not in the PR.

## Sources and evidence boundaries

- T01: [Ernoult & Kergomard2020, section3.1](https://doi.org/10.1051/aacus/2020005),
  telegraphist and cylindrical ZK equations; no WKB adoption, PDF not visually checked.
- T02: [NIST DLMF10.10.1](https://dlmf.nist.gov/10.10), continued fraction.
- T03: [Openwind Physics documentation](https://files.inria.fr/openwind/docs/_modules/openwind/continuous/physics.html),
  nominal CK equations and humidity/CO2 limitation, no pinned source SHA.
- T04: [benchmark2026 DOI](https://doi.org/10.1051/aacus/2026048),
  conditions and observable definitions read in accessible publisher text reproduced
  on ResearchGate; PDF/tables not newly verified visually.
- T05: [Zenodo v2](https://zenodo.org/records/20024938),
  [API metadata](https://zenodo.org/api/records/20024938),
  [README](https://zenodo.org/records/20024938/files/README.md?download=1).
- [Announced source scripts](https://gitlab.inria.fr/aernoult/acoustic-impedance-benchmark)
  were blocked and were not acquired/executed.

R08 PVC1210mm/30mm is a sourced geometry, not received R08 measurements.
Fletcher2006 eq4 supports the scalar beta attenuation form, not the legacy k/Zc
pair. R10 supplementary data remain unreceived. R13's standalone prototype and
historical R5/R7 suites are contextual evidence, not this repository execution.
All current coefficient/convergence agreement is numerical; it does not establish
real-wall material properties, played sound, toot threshold or playability.
