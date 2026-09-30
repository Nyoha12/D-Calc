# TD-PASS-01 — experimental passive reference

Related scientific decision: [issue #67](https://github.com/Nyoha12/D-Calc/issues/67).
This opt-in numerical workflow adds no default backend, material identification,
ranking or nonlinear score. Existing physics, lips, RK4 schedule, FIR factory,
parsers and validation policy remain unchanged.

## Representation and ports

In SI, `Z(s)=R0+sum(a*s/(s²+gamma*s+omega²))`, with `R0>=0`,
`a>=0`, `gamma>0`, `omega>0`. The coefficients describe an impedance
approximation, not identified materials or lips. Continuous coefficients and
`discrete_prewarped` approximation coefficients have distinct labels.

States obey `q'=v`, `v'=-omega²*q-gamma*v+u`. Here `u` is m³/s,
`p` Pa, `a` Pa/m³, `q` m³·s, `v` m³, `R0` Pa·s/m³.
For `h=1/(2fs)` and `den=1+h*gamma+h²*omega²`:

```
v_mid = (v-h*omega²*q+h*u)/den
q_next = q+2h*v_mid
v_next = 2*v_mid-v
p = R0*u+sum(a*v_mid)
E = sum(a*(v²+omega²*q²))/2
E_next-E = (p*u-R0*u²-sum(a*gamma*v_mid²))/fs
```

The returned pressure is the **midpoint port**, not the end-state pressure.
The discrete response is `Z(2j*fs*tan(pi*f/fs))`, for `0<=f<fs/2`.
The continuous response of prewarped coefficients is only the underlying
rational approximation; its poles are not inferred physical continuous modes.

`PassiveResonator` provides `reset`, `step`, `energy`, `state`,
`continuous_response`, `discrete_response`, `pressure_from_flow`, and an
explicitly sized `impulse_response(n)`. Prescribed-flow and impulse experiments
start from rest and do not mutate the caller's state. There is no
`impulse_kernel` attribute or implicitly allocated one-second kernel.

An analytical reference needs no TMM or configuration file:

```python
import numpy as np
from didgeridoo_optimizer.nonlinear.passive_resonator import PassiveResonator
w = 2*np.pi*70
m = PassiveResonator([1e7*w/8], [w/8], [w], R0=0., sample_rate_hz=12000,
    dc_origin={'kind': 'imposed_reference',
               'description': 'Exact single-mode witness: zero DC'},
    domain='continuous')
p = m.pressure_from_flow([1e-6, 0., 0.])
m.save('modal.json')  # exclusive creation
loaded = PassiveResonator.load('modal.json')
```

Model JSON is a versioned parameter/provenance document with a content hash,
strict keys, units, port, coefficient domains and finite numeric types. It is
never Python, pickle or an external factory. Duplicate keys/poles, negative
residues, unstable/unrepresentable poles and inconsistent units are refused.
File hashes are checked during loading and before/after batch/frequency
processing. The V2 wrapper also checks around the native simulation. Manual
single-step callers can use `verify_file_unchanged()` around their transaction.
The hash detects changes; it is not an authentication signature.

## Fit and acceptance

`fit_passive(f, complex_z, R0=..., dc_origin=..., sample_rate_hz=...,
gates=..., guard_frequency_hz=..., guard_impedance=...,
basis_completion="observed-only")` performs real NNLS on
stacked real/imaginary equations with weights
`1/max(abs(Z),0.01*max_fit(abs(Z)))`. R0 is fixed, never silently estimated or
set universally to zero. Seeds come from local maxima of the supplied spectrum
minus fixed R0, quadratic log-magnitude interpolation and resolved half-power
widths. Each observed peak supplies centers `f+{-0.2,0,0.2}*width` and damping
multipliers `{0.6,1,1.6}`. Discrete fits prewarp the centers and widths.
Unresolved widths are recorded and skipped; missing guard data is not invented.
The optional single-mode reciprocal fit is an explicit structural assumption.

`basis_completion="observed-only"` preserves that dictionary and remains the
explicitly reported default. `basis_completion="r29"` adds exactly two numerical
basis terms at `1.15*fmax` and `1.65*fmax`. At the default fmax=3000 Hz these
centers are 3450 and 4950 Hz. For `discrete_prewarped`,
`omega=2*fs*tan(pi*f/fs)` rad/s and `gamma=0.08*omega` 1/s; in the
`continuous` domain `omega=2*pi*f`. These represent the out-of-band contribution
to the in-band approximation. They are **not observed peaks, acquired data,
physical losses or material coefficients**. They are kept separate from the
fit/guard seed table. In particular, no measurements or computed target spectra
between the guard band and Nyquist are implied by these terms.

The choice, units, multipliers, centers, damping and transformation are present
in the plan, model provenance/quality, full-dictionary certificate, partial
checkpoints, JSON/CSV exports and French summary. All centers must be strictly
below Nyquist, including in continuous-domain fits; invalid types, unknown modes
and incompatible fs/fmax are refused before acoustics in the workflow. There is
no automatically selected fallback. Completion terms count toward the same
256-candidate ceiling.

The full inventory contains at most 256 candidates, including zero coefficients.
Each Lawson–Hanson active-set solve uses at most 192 columns. Deterministic
column generation checks all candidates after each solve and can restore
previously inactive columns. At most ten passes are allowed. Certification
requires both nonnegative coefficients and global KKT <=5e-10. The gradient
uses unit-norm weighted columns and is divided by `max(norm(b),1)`. Local
convergence alone, timeout and pass exhaustion are not global certification.
Completed passes are checkpointed with their exact coefficients; external
termination can lose only the in-progress pass.

Quality gates are declared before fitting. Defaults for a new design are
complex NRMSE <=0.005, maximum pointwise relative error <=0.08 and circular
phase RMS <=0.3 degrees. These are explicit numerical choices, not universal
empirical tolerances or optimization scores. Audit frequencies are withheld
from fitting; parameters are frozen. RMS/p95/max relative errors, circular
phase, local grid peaks and grid resolution are exported. Zero response has
undefined phase, not zero phase error. No bound between grid points is claimed.

Five independent statuses are retained in JSON, CSV and the summary:

| Status | Meaning |
| --- | --- |
| passivity | constructive nonnegative-coefficient subsystem identity |
| fitter | global candidate KKT convergence |
| fidelity | withheld complex-response gates |
| mesh | fixed-design h versus h/2 NRMSE; declared default gate 0.005 |
| physical | always `not_validated`; no A–E claim |

Numerical acceptance requires the first four checks. Failure of any numerical
gate returns exit 1 and preserves artifacts; physical validation is not inferred.
Timeout/error exports keep explicit partial statuses, not fabricated success.

## CONFIG/DESIGN workflow

Run from the repository root in an existing environment containing NumPy,
pytest and PyYAML; SciPy is not used:

```sh
python -m tools.time_domain_reference \
  --config project_specs/examples/design_pitch/config.yaml \
  --design project_specs/examples/design_pitch/cylinder.json \
  --output-dir results/td_pass_example \
  --loss-model zk --air-reference ck_dry20 --basis-completion r29 --dry-run
```

Remove `--dry-run` to calculate. Dry-run reads/validates CONFIG, DESIGN and real
DB/provenance and performs geometry preflight, without acoustics or writes.
The output directory must be new. `--model-in previous/model.json` reloads a
compatible model, regenerates the deterministic candidate inventory, recomputes
its global KKT certificate against the actual fit spectrum and independently
re-audits it. Context, effective fs, R0, fit and guard identities must agree.
The requested completion mode and its complete deterministic specification are
part of the context identity. Reload requires that same choice (including
`--basis-completion r29` for an R29 model), regenerates every candidate from the
actual fit/guard inputs, checks source attribution, inactive poles and completion
metadata, and recomputes global KKT. Changing the completion metadata or inventory
is refused even when the JSON checksum is recomputed. The plain resonator JSON
loader checks serialization integrity; this input-dependent recertification is
the dedicated workflow's responsibility. Models predating explicit completion
metadata require a new fit for this workflow; their basis is not silently inferred.
Reusing coefficients from another design or acoustic context is refused.

`load_fixed_context`, `design_pitch.models`, `GeometryDiscretizer` and
`input_impedance` supply the existing CONFIG/DESIGN/DB and acoustic behavior.
ZK requires explicit CK air; legacy requires `--air-reference` omitted and an
explicit `--r0 VALUE --dc-origin TEXT`. The derived ZK R0 is the circular rigid
limit summed over local one-dimensional slices: `sum(8*mu*L/(pi*a^4))`.
For the repository cylinder L=1.20 m, diameter 0.03 m, CK20 viscosity
1.807064e-5 Pa·s, R0=1090.76164487931 Pa·s/m³. This is linearized acoustic
resistance, not a finite mean-flow law. The separate L=1.210 m witness has
R0=1099.8513252533; the geometries are not interchangeable.

The defaults use 4096 fit points in 40..3000 Hz, 768 guard points in
3000..3500 Hz and 1536 reserved audit points
`40+(j+0.618033988749895)*2960/1536`. The default mesh is 0.5 cm, checked at
0.25 cm. Source spectra, coefficient inventories, effective air, DB identities,
units, fs and loaded source hashes are retained. The strict existing provenance
guard is reused without editing it; actual loaded tool files are also checked.

The FIR comparator actually calls `LinearEvaluationPipeline.evaluate` and
`TimeDomainResonator.from_linear_result` with native peak metadata. That
pipeline is **legacy**, even for a ZK target. The comparison therefore exports
both FIR-versus-legacy and passive-versus-legacy errors, with the model
mismatch explicit. The native linear evaluator internally computes its usual
features; no ranking/aggregate score is used or exported here. Its kernel is
not recentered and its factory is not replaced.

Artifacts: `plan.json`, `partial.json`, `fit_progress.json` (new fits),
`model.json`, `spectra.npz`, `forced.npz`, `reference.json`, `reference.csv`,
`summary.txt`. NPZs contain numerical arrays only (`allow_pickle=False` to
read). `forced.npz` includes SI prescribed sinusoidal flow, passive midpoint
pressure, native FIR pressure and a finite unit-sample impulse experiment.
70 Hz is a chosen forcing frequency, which can be outside a user's fit band;
its in-band flag is exported. The finite sinusoid, start/stop transients and
impulse are not bandlimited. Their full time signals have no validated
out-of-band fidelity claim.
Artifacts remain available after failed quality gates. The saved model records
its fit certificate; the separate immutable audit identifies the model it
actually evaluated. A model file's historical quality fields are not accepted
as a substitute for recertification and current audit.

Each run launches one sequential scientific child with <=180 s wall time,
768 MiB address space and BLAS=1, under a declared 600 s scientific ceiling.
No limits are applied to a surrounding agent or GitHub client. An external
multi-run orchestrator must account cumulatively for its own runs. No sweep,
installation or A–E replay is performed. The bounded CLI requires POSIX resource
limits; dry-run and the numerical APIs are portable.

## V2 experiment and limits

`--v2-pressure-pa 4000` enables a bounded native V2 simulation with both
backends. Backend fs and the simulator's effective fs are identical and <=12 kHz.
Actual supplied pressure, effective V2 parameters, actual duration and any
nonregular equilibrium branches from the existing diagnostic are recorded.
No surrogate, copied RK4, `NonlinearPipeline.evaluate`, fake kernel or score is
used. Native FIR and passive midpoint pressures keep distinct port labels.

The historical schedule is RK4 under previous pressure, native lip flow, then
resonator step. It is not an energy-coupled integrator. For the 70 Hz/Q8
analytical witness with the stated V2 parameters, R29 found continuous onset
3996.671891607 Pa at 64.871246247 Hz; the historical schedule gives
4062.676894137 Pa at 12 kHz and 4198.524055211 Pa at 4 kHz. Those are dated
reference results, not thresholds recomputed by this CLI. Subsystem passivity
alone proves neither coupled stability nor a played periodic state.

## Dated fixtures and separately tested new fits

Both sanitized R29 fixtures and their manifest are under
`didgeridoo_optimizer/tests/fixtures/td_pass_01/`. They preserve all final
coefficients, fs, fit/guard/audit spectra, model identities, source revision,
physical inputs, effective air, units and mesh sizes. Tests reconstruct the
models and recompute both entire reserved target grids using the current native
TMM; file reloads and response identity are also checked.

| R29 final coefficients | Active terms | Complex NRMSE | Maximum relative | Phase RMS degrees |
| --- | ---: | ---: | ---: | ---: |
| Cylinder ZK | 154 | 0.0036897129423862 | 0.0675665997483450 | 0.181254072664681 |
| Exponential ZK | 161 | 0.0036309410765017 | 0.0675071201022980 | 0.169672976365590 |

These fixture regressions reconstruct dated final models. Separate tests now
fit new nonnegative residues from only the traced TMM fit spectrum and guard
seeds, for **both** cylinder and exponential cases; the reserved audit arrays
are read only after fitting. The binary fixtures and their expected numbers
remain unchanged. The R29 completion option implements the two numerical terms
already used in that study, with no new acoustic law or tolerance.

On the correction baseline, the observed-only fits both converged globally but
were correctly refused: cylinder NRMSE 0.0152359271, maximum relative 0.1148801459,
phase RMS 1.0015398272 degrees; exponential NRMSE 0.0158267901, maximum relative
0.1202010129, phase RMS 0.9771595935 degrees. These remain tested nonaccepted
comparisons. Passivity and mesh acceptance cannot override a failed fidelity
gate. The new R29-completed fits must pass the same .005/.08/.3 degree gates;
reconstructing the old coefficient fixtures does not establish that result.

Separate fresh-fit regression results (40..3000 Hz fit, 3000..3500 Hz guard,
1536 withheld audit points; all 227 candidates retained for certification):

| New fit with explicit R29 completion | Active terms | Complex NRMSE | Maximum relative | Phase RMS degrees | Fidelity |
| --- | ---: | ---: | ---: | ---: | --- |
| Cylinder ZK | 154 | 0.0036897136 | 0.0675666162 | 0.1812540875 | accepted |
| Exponential ZK | 161 | 0.0036309423 | 0.0675070692 | 0.1696731727 | accepted |

Both converge in six column-generation passes with global KKT below 1e-15.
These grid-specific numerical results do not validate out-of-band fidelity,
coupled V2 stability, physical oscillation or a new ranking score.

Targeted native suites remain unmodified: onset stability/CLI, V2 lips, FIR
scaling, thermoviscous and fixed-design input/internal/CLI. New tests add exact
modal and independent matrix/DTFT/energy checks, exhaustive small NNLS oracles,
full-dictionary column generation, both R29 regressions, strict model/input
refusals, native V2 schedule identity, source guards, dry-run and partial exports.
