"""Synthetic numerical oracles: no measurements or material calibration."""
from dataclasses import replace
import math

import numpy as np
import pytest

from didgeridoo_optimizer.acoustics.air import AirProperties
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters, LipModelV2
from didgeridoo_optimizer.nonlinear.onset_stability import (
    SearchBudget, continuous_characteristic, discrete_characteristic, equilibria,
    fir_audit, free_step, free_tangent, mechanics, modal_crossing, modal_roots,
    search_marginals, short_fir_matrix, validate_parameters, verify_discrete_crossing,
)
from didgeridoo_optimizer.nonlinear.regimes import analyze
from didgeridoo_optimizer.nonlinear.resonator_td import TimeDomainResonator
from didgeridoo_optimizer.nonlinear.thresholds import OscillationThresholdEstimator, onset_detected


P = DimensionedLipParameters(mouth_pressure_kpa=1.5, resonance_hz=80., effective_area_m2=3e-6,
    mass_kg=1e-4, rest_opening_m=.0008, lip_width_m=.012, damping_ratio=.20,
    contact_stiffness_n_per_m=1e4, contact_damping_n_s_per_m=.04, min_opening_m=1e-6,
    flow_coefficient=.72, pressure_force_sign=-1.)
RHO = 1.204
WA = 2*np.pi*70
GAMMA = WA/8
A = 1e7*GAMMA
PH = 3996.6718916070563
FH = 64.87124624686057


@pytest.fixture(scope="module")
def modal_fir():
    freq = np.linspace(40., 3000., 8192)
    s = 2j*np.pi*freq
    z = A*s/(s*s+GAMMA*s+WA*WA)
    result = {"freq_hz": freq, "zin": z, "features": {"f0_hz":70.}, "peaks":[{"frequency_hz":70.}]}
    cfg = {"nonlinear_simulation": {"sample_rate_hz":12000, "resonator_model_type":"fir_long_logfit", "resonator_kernel_duration_s":1.}}
    return TimeDomainResonator.from_linear_result(result, cfg), freq, z


def test_reference_oracle_and_original_laws():
    eq = equilibria(P, RHO, 1500., closure="reference_pd_zero")["branches"][0]
    k,r,au,ad = mechanics(P)
    assert k == pytest.approx(25.26618726678876, rel=1e-14)
    assert r == pytest.approx(2*.20*1e-4*2*np.pi*80)
    assert (au,ad) == (-3e-6,3e-6)
    assert eq["h_m"] == pytest.approx(.0006218963568787032, rel=1e-13)
    assert eq["flow_m3_s"] == pytest.approx(.0002682125771076499, rel=1e-13)
    assert eq["residuals"]["scaled_max"] < 1e-13
    assert eq["status"] == "equilibrium_solved" and eq["stability"] == "not_resolved"


@pytest.mark.parametrize("pressure,flow,downstream,h", [
    (1500., None, -456.69958043, .00056766978415439),
    (0., .000122061795864883, -199.35953724, None),
])
def test_fir_equilibria_and_multiplicity(modal_fir, pressure, flow, downstream, h):
    resonator,_,_ = modal_fir
    answer = equilibria(P,RHO,pressure,closure="fir_dc",dc=float(resonator.impulse_kernel.sum()))
    assert answer["status"] == "equilibrium_solved"
    assert answer["branch_count"] == (2 if pressure == 0 else 1)
    eq = answer["branches"][-1]
    assert eq["downstream_pa"] == pytest.approx(downstream,abs=1e-7)
    if flow is not None:
        assert eq["flow_m3_s"] == pytest.approx(flow,rel=1e-10)
        assert answer["branches"][0]["flow_m3_s"] == 0
        assert answer["branches"][0]["non_regular_reasons"] == ["bernoulli_delta_zero"]
    if h is not None:
        assert eq["h_m"] == pytest.approx(h,abs=1e-14)
    # Original force/flow and the actual FIR step at a constant history.
    active = replace(P,mouth_pressure_kpa=pressure/1000)
    model = LipModelV2(active)
    assert np.max(np.abs(model.derivatives(0,[eq["x_m"],0],active,eq["downstream_pa"]))) < 1e-9
    assert model.flow([eq["x_m"],0],active,eq["downstream_pa"],AirProperties(RHO,343)) == pytest.approx(eq["flow_m3_s"],rel=1e-12)
    witness = TimeDomainResonator(12000,resonator.impulse_kernel.copy(),{})
    witness._flow_state[:] = eq["flow_m3_s"]
    assert witness.step(eq["flow_m3_s"]) == pytest.approx(eq["downstream_pa"],abs=1e-9)


@pytest.mark.parametrize("pressure", [0.,1500.,7000.,11000.])
def test_zero_dc_agrees_with_reference_including_contact_and_closed_flow(pressure):
    a = equilibria(P,RHO,pressure,closure="reference_pd_zero")["branches"][0]
    b = equilibria(P,RHO,pressure,closure="fir_dc",dc=0.)
    assert b["branch_count"] == 1
    for field in ("h_m","x_m","flow_m3_s"):
        assert b["branches"][0][field] == pytest.approx(a[field],abs=1e-15)
    if pressure == 7000.:
        assert 0 < a["h_m"] < P.min_opening_m
        assert a["non_regular_reasons"] == ["contact_velocity_one_sided"]
    if pressure == 11000.:
        assert a["h_m"] < 0 and a["flow_m3_s"] == 0


def test_distinct_boundaries_and_no_finite_bernoulli_jacobian():
    k = P.mass_kg*(2*np.pi*P.resonance_hz)**2
    contact_pressure = k*(P.rest_opening_m-P.min_opening_m)/P.effective_area_m2
    closed_pressure = (k*P.rest_opening_m+P.contact_stiffness_n_per_m*P.min_opening_m)/P.effective_area_m2
    assert closed_pressure > contact_pressure
    for pressure,reason in ((0.,"bernoulli_delta_zero"),(contact_pressure,"contact_boundary"),(closed_pressure,"flow_closure_boundary")):
        eq = equilibria(P,RHO,pressure,closure="reference_pd_zero")["branches"][0]
        assert reason in eq["non_regular_reasons"]
        with pytest.raises(ValueError,match="regular free"):
            free_tangent(P,RHO,eq)
    air = AirProperties(RHO,343.)
    model = LipModelV2(replace(P,mouth_pressure_kpa=0.))
    slopes = [model.flow([0,0],None,-eps,air)/eps for eps in (1e-2,1e-4,1e-6)]
    assert slopes[1]/slopes[0] == pytest.approx(10.)
    assert slopes[2]/slopes[1] == pytest.approx(10.)


def test_contact_one_sided_derivatives_and_fixed_pressure_energy_only():
    active = replace(P,mouth_pressure_kpa=7.)
    eq = equilibria(active,RHO,7000.,closure="reference_pd_zero")["branches"][0]
    model = LipModelV2(active)
    x = eq["x_m"]
    origin = model.contact_force([x,0])
    for eps in (1e-3,1e-5,1e-7):
        assert (model.contact_force([x,-eps])-origin)/(-eps) == pytest.approx(-.04,abs=1e-10)
        assert (model.contact_force([x,eps])-origin)/eps == 0
    k = active.mass_kg*(2*np.pi*active.resonance_hz)**2
    r = 2*active.damping_ratio*active.mass_kg*2*np.pi*active.resonance_hz
    for v in (-.001,.001):
        state = [x-1e-8,v]
        deriv = model.derivatives(0,state,active,0.)
        dv = active.mass_kg*v*deriv[1]+(k+active.contact_stiffness_n_per_m)*(state[0]-x)*v
        assert dv == pytest.approx(-r*v*v-active.contact_damping_n_s_per_m*v*v*(v<0),abs=1e-16)
    assert eq["stability"] == "not_resolved"


def test_positive_sign_negative_dc_can_have_no_equilibrium():
    answer = equilibria(replace(P,pressure_force_sign=1.),RHO,1500.,closure="fir_dc",dc=-1e9)
    assert answer["branch_count"] == 0
    assert answer["status"] == "not_resolved" and answer["absence_in_algebraic_domain"]


def test_double_root_is_one_conditioned_cluster_not_two_silent_branches():
    # Independently factored cubic -(T²/5)*(t/T-1)²*(3*t/T+1).
    params=replace(P,pressure_force_sign=1.)
    b=.0008; c=3e-6/(1e-4*(2*np.pi*80)**2); d=.72*.012*np.sqrt(2/1.204)
    t=np.sqrt(3*b/c); pressure=t*t/5; dc=-t/(5*d*b)
    answer=equilibria(params,RHO,pressure,closure="fir_dc",dc=dc)
    assert answer["branch_count"]==1
    assert answer["branches"][0]["algebraic_multiplicity_t"]==2
    assert answer["branches"][0]["delta_pa"]==pytest.approx(t*t,rel=1e-7)
    assert answer["ambiguous_roots"] and answer["status"]=="not_resolved"
    assert answer["branches"][0]["residuals"]["scaled_max"]<1e-8
    zero=equilibria(P,RHO,0.,closure="fir_dc",dc=0.)
    assert zero["branch_count"]==1 and zero["branches"][0]["algebraic_multiplicity_t"]==2


def test_flow_derivatives_against_original_model():
    eq = equilibria(P,RHO,1500.,closure="reference_pd_zero")["branches"][0]
    b,c = free_tangent(P,RHO,eq)
    model = LipModelV2(P)
    air = AirProperties(RHO,343.)
    dx,dp = 1e-9,1e-3
    actual_b = (model.flow([eq["x_m"]+dx,0],None,0.,air)-model.flow([eq["x_m"]-dx,0],None,0.,air))/(2*dx)
    actual_c = (model.flow([eq["x_m"],0],None,-dp,air)-model.flow([eq["x_m"],0],None,dp,air))/(2*dp)
    assert b == pytest.approx(actual_b,rel=1e-9)
    assert c == pytest.approx(actual_c,rel=1e-8)
    s=23+400j; zd=2e6-1e6j; zu=3e5+4e5j
    k,r,au,ad = mechanics(P)
    expected=(P.mass_kg*s*s+r*s+k)*(1+c*(zu+zd))-b*(ad*zd-au*zu)
    assert continuous_characteristic(s,P,b,c,zd,zu) == expected


def independent_modal_matrix(pressure):
    # Literal independent mechanics/flow, no call into the diagnostic tangent.
    omega=2*np.pi*80.; k=1e-4*omega**2; r=2*.2*1e-4*omega
    opening=.0008-3e-6*pressure/k
    b=.72*.012*np.sqrt(2*pressure/1.204)
    flow=b*opening; c=flow/(2*pressure)
    return np.array([[0,1,0,0],[-k/1e-4,-r/1e-4,0,3e-6*A/1e-4],
                     [0,0,0,1],[b,0,-WA**2,-(GAMMA+A*c)]])


@pytest.mark.parametrize("pressure,expected",[(PH-10,-.0596518928),(PH,0.),(PH+10,.0595101696)])
def test_modal_polynomial_against_independent_matrix(pressure,expected):
    actual=modal_roots(P,RHO,pressure,a=A,gamma=GAMMA,omega_a=WA)
    independent=np.linalg.eigvals(independent_modal_matrix(pressure))
    assert np.sort_complex(actual) == pytest.approx(np.sort_complex(independent),abs=1e-8)
    assert max(actual.real) == pytest.approx(expected,abs=1e-9)
    for root in actual:
        eq=equilibria(P,RHO,pressure,closure="reference_pd_zero")["branches"][0]
        b,c=free_tangent(P,RHO,eq)
        zd=A*root/(root**2+GAMMA*root+WA**2)
        assert abs(continuous_characteristic(root,P,b,c,zd)) < 1e-8


def test_modal_crossing_and_real_axis_candidate_are_distinct():
    crossing=modal_crossing(P,RHO,pressure_bounds=(3500.,4500.),a=A,gamma=GAMMA,omega_a=WA)
    assert crossing["status"] == "local_crossing_verified"
    assert crossing["pressure_pa"] == pytest.approx(PH,abs=1e-7)
    assert crossing["frequency_hz"] == pytest.approx(FH,abs=1e-8)
    calls=[]
    def real_z(f):
        assert isinstance(f,float)
        calls.append(f)
        s=2j*np.pi*f
        return A*s/(s*s+GAMMA*s+WA*WA)
    result=search_marginals(P,RHO,closure="reference_pd_zero",dc=0.,pressure_bounds=(3500.,4500.),frequency_bounds=(60.,70.),
        pressure_seeds=2,frequency_seeds=2,max_iterations=16,budget=SearchBudget(1000,20),impedance=real_z)
    assert len(result["candidates"]) == 1
    candidate=result["candidates"][0]
    assert candidate["status"] == "marginal_candidate" and candidate["crossing"] == "not_resolved"
    assert candidate["pressure_pa"] == pytest.approx(PH,abs=1e-3)
    assert candidate["frequency_hz"] == pytest.approx(FH,abs=1e-5)
    assert not result["complete_root_census"] and calls
    unfinished=modal_crossing(P,RHO,pressure_bounds=(3500.,4500.),a=A,gamma=GAMMA,omega_a=WA,iterations=1)
    assert unfinished["status"]=="not_resolved"


@pytest.mark.parametrize("fs",[4000,12000])
def test_short_discrete_matrix_against_scaled_finite_differences_of_real_step(fs):
    kernel=np.array([2e5,1e5])
    eq=equilibria(P,RHO,1500.,closure="fir_dc",dc=3e5)["branches"][0]
    assert eq["downstream_pa"] == pytest.approx(79.4910906959,abs=1e-9)
    b,c=free_tangent(P,RHO,eq)
    phi,g,n=free_step(P,fs)
    assert n == LipModelV2(P).integration_substeps(1/fs)
    matrix=short_fir_matrix(phi,g,b,c,kernel)
    state=np.array([eq["x_m"],0,eq["flow_m3_s"],eq["flow_m3_s"]])
    scales=np.array([.0008,.0008*2*np.pi*80,.0003,.0003])
    estimator=OscillationThresholdEstimator(); model=LipModelV2(P); air=AirProperties(RHO,343.)
    def actual_step(s):
        resonator=TimeDomainResonator(fs,kernel,{})
        resonator._flow_state[:]=s[2:]
        old_pressure=float(kernel@s[2:])
        y=estimator._rk4_step(s[:2],1/fs,P,old_pressure,lip_model=model,clip_opening=False)
        flow=model.flow(y,P,old_pressure,air)
        new_pressure=resonator.step(flow)
        assert new_pressure == pytest.approx(float(kernel@resonator._flow_state))
        return np.r_[y,resonator._flow_state]
    assert actual_step(state) == pytest.approx(state,abs=1e-14)
    eps=1e-5
    fd=np.column_stack([(actual_step(state+eps*scales[j]*np.eye(4)[j])-actual_step(state-eps*scales[j]*np.eye(4)[j]))/(2*eps*scales[j]) for j in range(4)])
    scaled=lambda m: m*scales[np.newaxis,:]/scales[:,np.newaxis]
    assert scaled(matrix) == pytest.approx(scaled(fd),abs=2e-10)
    for z in np.linalg.eigvals(matrix):
        if abs(z)>1e-8:
            value,_,scale=discrete_characteristic(z,phi,g,b,c,kernel)
            assert abs(value)/scale < 1e-8
    z=.97+.04j
    value,deriv,_=discrete_characteristic(z,phi,g,b,c,kernel)
    dz=1e-7
    numeric=(discrete_characteristic(z+dz,phi,g,b,c,kernel)[0]-discrete_characteristic(z-dz,phi,g,b,c,kernel)[0])/(2*dz)
    assert deriv == pytest.approx(numeric,rel=2e-8)
    # Multiplying the Laurent form by z^(L-1) gives det(zI-M).
    assert z*value == pytest.approx(np.linalg.det(z*np.eye(4)-matrix),rel=1e-10)


def test_fir_dc_complex_phase_provenance_and_no_correction(modal_fir):
    resonator,f,z=modal_fir
    before=resonator.impulse_kernel.copy()
    report=fir_audit(before,12000,[0,70,210,6000],f,z)
    assert resonator.metadata["scaling_gain"] == pytest.approx(.9938128593443679,rel=1e-12)
    assert report["dc_pa_s_m3"] == pytest.approx(-1633267.279330331,rel=1e-12)
    # The pilot oracle is the exact DTFT / analytic modal impedance at 70 Hz.
    interpolated=TimeDomainResonator._kernel_frequency_response(before,12000,np.array([70.]))[0]
    exact=complex(report["samples"][1]["real_pa_s_m3"],report["samples"][1]["imag_pa_s_m3"])
    assert abs(exact)/1e7 == pytest.approx(.996085926383,rel=1e-10)
    assert abs(interpolated)/1e7 == pytest.approx(.9952304946643669,rel=1e-12)
    assert report["passivity"] == "refuted_at_sample"
    assert report["samples"][0]["documented"] is False
    assert report["samples"][-1]["documented"] is False
    assert report["samples"][1]["relative_complex_error"] is not None
    assert 210 in resonator.metadata["scaling_reference_points_hz"]
    assert len(report["sha256_float64_le"]) == 64
    assert np.array_equal(before,resonator.impulse_kernel)
    for row in report["samples"]:
        expected=sum(float(v)*complex(np.exp(-2j*np.pi*row["frequency_hz"]*j/12000)) for j,v in enumerate(before))
        assert complex(row["real_pa_s_m3"],row["imag_pa_s_m3"]) == pytest.approx(expected,abs=1e-7)


def test_short_fir_phase_and_positive_grid_not_passivity_certificate():
    report=fir_audit([2e5,1e5],4000,[0,1000,2000],[1,1999],[1j,2j])
    assert report["dc_pa_s_m3"] == 3e5
    middle=report["samples"][1]
    assert middle["real_pa_s_m3"] == pytest.approx(2e5)
    assert middle["imag_pa_s_m3"] == pytest.approx(-1e5)
    assert middle["phase_rad"] == pytest.approx(-math.atan(.5))
    assert report["samples"][2]["real_pa_s_m3"] == pytest.approx(1e5)
    assert report["passivity"] == "not_certified_by_grid"


def test_long_fir_local_crossing_with_independent_eliminated_residual(modal_fir):
    resonator,_,_=modal_fir; h=resonator.impulse_kernel
    budget=SearchBudget(2000,30)
    search=search_marginals(P,RHO,closure="fir_dc",dc=float(h.sum()),pressure_bounds=(2500.,4500.),
        frequency_bounds=(55.,80.),pressure_seeds=2,frequency_seeds=2,max_iterations=16,budget=budget,kernel=h,fs_hz=12000)
    assert len(search["candidates"])==1
    candidate=search["candidates"][0]
    crossing=verify_discrete_crossing(P,RHO,h,12000,candidate,pressure_step_pa=10.,budget=budget,frequency_bounds=(55.,80.))
    assert crossing["status"]=="local_crossing_verified" and crossing["direction"]=="outward"
    assert not search["complete_root_census"]
    # Independent feedback residual: eliminate y using a 2x2 linear solve;
    # avoid the characteristic determinant/adjugate implementation entirely.
    omega=2*np.pi*80; mat=np.array([[0.,1.,0.],[-omega**2,-2*.2*omega,3e-6/1e-4],[0,0,0]])
    n=math.ceil((1/12000)*math.sqrt((1e-4*omega**2+1e4)/1e-4)/.45)
    scaled=mat/(12000*n)
    rk=np.eye(3); power=np.eye(3)
    for j in range(1,5):
        power=power@scaled/j; rk+=power
    advance=np.linalg.matrix_power(rk,n); phi=advance[:2,:2]; g=advance[:2,2]
    for row in crossing["tracks"]:
        z=complex(row["z_real"],row["z_imag"])
        # Independent scalar bisection for this closing-sign free equilibrium.
        pressure=row["pressure_pa"]; lo=pressure; hi=1e-4*omega**2*.0008/3e-6
        dc=float(h.sum())
        def residual(delta):
            return delta+dc*.72*.012*(.0008-3e-6*delta/(1e-4*omega**2))*np.sqrt(2*delta/1.204)-pressure
        for _ in range(60):
            mid=(lo+hi)/2
            if residual(mid)>0: hi=mid
            else: lo=mid
        delta=(lo+hi)/2; opening=.0008-3e-6*delta/(1e-4*omega**2)
        b=.72*.012*np.sqrt(2*delta/1.204); c=b*opening/(2*delta)
        hz=np.sum(h*np.power(z,-np.arange(len(h))))
        independent=1-hz*(b*np.linalg.solve(z*np.eye(2)-phi,g)[0]-c/z)
        assert abs(independent)<1e-7
    bad={**candidate,"frequency_hz":candidate["frequency_hz"]+2.}
    assert verify_discrete_crossing(P,RHO,h,12000,bad,pressure_step_pa=10.,budget=SearchBudget(20,5))["status"]=="not_resolved"
    limited=verify_discrete_crossing(P,RHO,h,12000,candidate,pressure_step_pa=10.,budget=SearchBudget(1,5))
    assert limited["budget_exhausted"] and limited["status"]=="not_resolved"


@pytest.mark.parametrize("played,reference,expected",[(70,70,True),(210,70,False),(210,210,True)])
def test_existing_classifier_on_prescribed_signals(played,reference,expected):
    fs=4000; t=np.arange(fs)/fs
    prescribed={"sample_rate_hz":fs,"pressure_signal":np.sin(2*np.pi*played*t),
                "flow_signal":1e-4*np.sin(2*np.pi*played*t),"reference_freq_hz":reference}
    cfg={"nonlinear_simulation":{"warmup_duration_s":.2}}
    prescribed["regime"]=analyze(prescribed,cfg)
    assert prescribed["regime"]["is_stable"]
    assert onset_detected(prescribed,cfg) is expected


@pytest.mark.parametrize("field,value",[("mass_kg",0),("mass_kg",1e-15),("resonance_hz",float("nan")),("pressure_force_sign",0),("flow_coefficient",True),("damping_ratio",-1),("effective_area_m2","3e-6")])
def test_parameter_refusals(field,value):
    with pytest.raises(ValueError):
        validate_parameters(replace(P,**{field:value}),RHO)


def test_search_budget_partial_determinism_and_dense_guard():
    options=dict(closure="reference_pd_zero",dc=0.,pressure_bounds=(1000.,2000.),frequency_bounds=(50.,100.),
                 pressure_seeds=2,frequency_seeds=2,max_iterations=8,impedance=lambda f: 0j)
    outputs=[search_marginals(P,RHO,**options,budget=SearchBudget(2,20)) for _ in range(2)]
    assert outputs[0] == outputs[1]
    assert outputs[0]["status"] == "not_resolved" and outputs[0]["budget_exhausted"]
    result=search_marginals(P,RHO,**options,budget=SearchBudget(1000,20))
    assert result["status"] == "no_candidate_in_tested_domain" and not result["absence_is_proof"]
    phi,g,_=free_step(P,12000)
    with pytest.raises(ValueError,match="Dense"):
        short_fir_matrix(phi,g,.1,1e-7,np.ones(12000))
    with pytest.raises(ValueError,match="bounded FIR"):
        discrete_characteristic(.01,phi,g,.1,1e-7,np.ones(12000))
    with pytest.raises(ValueError):
        SearchBudget(100,181)
    with pytest.raises(ValueError,match="real"):
        short_fir_matrix(phi,g,.1,1e-7,[1+2j])
    contact=search_marginals(P,RHO,**{**options,"pressure_bounds":(7000.,8000.)},budget=SearchBudget(100,20))
    assert contact["status"]=="not_resolved" and contact["trace"]
