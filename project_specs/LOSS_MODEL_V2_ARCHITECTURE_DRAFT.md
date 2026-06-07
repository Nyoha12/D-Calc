# LOSS_MODEL_V2_ARCHITECTURE_DRAFT

## 1. Document status

This is a draft architecture note.

It is not implemented, not validated, and not a source of truth for current code behavior.
It frames a possible future experimental loss model. The real repository code remains
the source of truth for implemented behavior.

This note does not change the stable user contract, material database, calibration
policy, or validation policy.

## 2. Current model: `legacy_beta`

The current linear loss model is the legacy beta attenuation model:

```text
alpha = 1e-5 * beta * sqrt(omega) / diameter
alpha_eff = alpha * (1 + wall_loss + porosity_leak)
k = omega / c - j * alpha_eff
```

The transfer-matrix path also uses the same effective attenuation in lossy
characteristic impedance:

```text
Zc_lossy = Zc_nominal * (1 + j * alpha_eff / k0)
```

In this model, `wall_loss` and `porosity_leak` are multiplicative modifiers on
the same attenuation term. They are therefore interchangeable when their summed
contribution is the same. Existing material fields `beta`, `wall_loss`, and
`porosity_leak` carry `sourced`, `inferred`, or `to_calibrate` status.

`legacy_beta` must remain the strict default.

## 3. Goals for `loss_model_v2`

`loss_model_v2` should:

- separate loss components so material diagnostics are easier to read;
- avoid mixing dissipation, reactive wall compliance, surface roughness, and leak
  effects into one scalar attenuation path;
- preserve exact compatibility for the legacy default;
- support clearer reporting of component status and provenance;
- improve future Q, magnitude, and backpressure behavior without claiming
  immediate calibration or validation.

## 4. Conceptual architecture

A future implementation may introduce a `LossModel` interface that returns a
`LossResult` per segment and frequency grid.

Candidate `LossResult` fields:

- `k_complex`: complex wavenumber for propagation.
- `zc_complex`: complex characteristic impedance for propagation.
- `alpha_total`: total dissipative attenuation used by the model.
- `components`: per-component contributions and intermediate values.
- `provenance_status`: status summary for formulas, material properties, and
  mappings.
- `warnings`: model, data-quality, and calibration warnings.

Candidate components:

- `air_thermoviscous`
- `wall_damping`
- `wall_compliance_reactive`
- `surface_roughness`
- `porosity_leak`

`wall_compliance_reactive` should not be forced into `alpha_total` if its effect
is primarily reactive. The component split should make this distinction explicit.

## 5. Component status model

Each component should expose one of:

- `sourced`: supported directly by a reliable source, code formula, or measured
  data appropriate to the component.
- `inferred`: plausible mapping or estimate, but not directly validated for the
  material and geometry in use.
- `to_calibrate`: expected to require experiment, benchmark replay, or A-E
  validation before any promotion claim.

`air_thermoviscous` may become formula/sourced earlier than material-dependent
components if a standard formula is selected and tested.

Wall and material properties may be `sourced` when external measured data exists.
Mappings from existing material records to effective loss components remain
`inferred` or `to_calibrate` unless calibrated for this model.

## 6. Future config candidate

This is a candidate only, not an implemented contract:

```yaml
linear_acoustics:
  loss_model:
    name: legacy_beta
```

Possible future values:

```text
legacy_beta | component_v2_experimental
```

Naming, schema placement, defaults, and reporting behavior are still open
decisions.

## 7. Compatibility rules

- Absent config must mean exact `legacy_beta` behavior.
- `legacy_beta` must reproduce current `Zin`, peaks, Q, and warnings.
- `component_v2_experimental` must be opt-in only.
- No material database coefficient promotion follows from adding the option.
- No generated artifact alone may be treated as validation truth.
- The stable user-facing config contract must not change until explicitly
  versioned.

## 8. Future test plan

Before implementation can be considered usable, test coverage should include:

- strict legacy equivalence for `legacy_beta`;
- monotonic component trends for dissipative components;
- A-E validation, especially for high-loss or materially dissipative cases;
- reporting of components, provenance/status, and warnings;
- safe inheritance of the new linear `Zin` by nonlinear resonator paths.

## 9. Open decisions

- Exact formulas for air thermoviscous loss.
- Material property mapping from current records into components.
- Whether wall compliance changes `k_complex`, `zc_complex`, or both.
- Surface roughness and porosity leak model details.
- Config key name and schema placement.
- Reporting surface for component diagnostics.
- Validation and calibration evidence required before any broader claim.

## 10. Non-goals

- No immediate coefficient promotion.
- No user-facing claim of improvement yet.
- No replacement of `beta` in the stable default.
- No calibration from internet tables alone.
- No nonlinear, toot, or player-model behavior change.
