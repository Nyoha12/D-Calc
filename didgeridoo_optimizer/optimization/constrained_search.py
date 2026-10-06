"""Deterministic bounded feasibility search. Local diagnostics, never proof of infeasibility."""
from __future__ import annotations

import time
import numpy as np


def preference_key(contract, row):
    """Strict lexicographic errors; priorities are unique, never weights."""
    by_id={r['id']:r for r in row['criteria']}
    prefs=sorted((c for c in contract.criteria if c['role']=='preference'),key=lambda c:c.get('priority',0))
    if not contract.preference_supported or any(by_id[c['id']]['residual_normalized'] is None or
            by_id[c['id']]['status'] not in ('satisfied','violated') for c in prefs):
        return None
    return tuple(abs(by_id[c['id']]['residual_normalized']) for c in prefs)


def search(contract, evaluate, checkpoint=None):
    budget=contract.budgets
    start=time.monotonic(); history=[]; accepted=[]; rank_diagnostics=[]
    low=np.array([v['low'] for v in contract.variables]); high=np.array([v['high'] for v in contract.variables])
    scale=high-low
    x=np.array(contract.initial)
    hard=[c for c in contract.criteria if c['role']=='hard' and not c['unsupported_reason']]

    def call(point,stage):
        if len(history)>=budget['evaluations'] or time.monotonic()-start>budget['seconds']:
            return None
        result=evaluate(point)
        result['evaluation']=len(history)+1; result['stage']=stage
        result['variables_si']=point.tolist()
        history.append(result)
        if result['search_feasible']:
            accepted.append(result)
            if checkpoint: checkpoint(result)
        return result

    def residual(row):
        by_id={r['id']:r for r in row['criteria']}
        values=[]
        for c in hard:
            r=by_id[c['id']]
            if r['status'] not in ('satisfied','violated') or r['residual_normalized'] is None:
                return None
            values.append(r['residual_normalized'])
        return np.array(values,dtype=float)

    def score(r):
        return float(np.max(np.abs(r))) if len(r) else 0.

    current=call(x,'initial'); reason='evaluation_only' if not len(x) else 'budget_exhausted'
    if current is None: raise ValueError('budget insuffisant même pour évaluation initiale')
    if checkpoint and not current['search_feasible']: checkpoint(current)
    best=current
    # Explicit reproducible restarts used only when the local iteration cannot progress.
    rng=np.random.default_rng(0)
    for iteration in range(budget['iterations'] if len(x) else 0):
        r=residual(current)
        if current['search_feasible']:
            key=preference_key(contract,current)
            if not key:
                reason='feasible_witness_found'; break
            improved=False
            for j in range(len(x)):
                for direction in (1.,-1.):
                    proposal=x.copy(); proposal[j]+=direction*.01*scale[j]/(iteration+1)
                    if np.any(proposal<low) or np.any(proposal>high): continue
                    trial=call(proposal,'lexicographic_poll')
                    candidate_key=None if trial is None else preference_key(contract,trial)
                    if trial is not None and trial['search_feasible'] and candidate_key is not None and candidate_key<key:
                        x=proposal;current=trial;best=trial;key=candidate_key;improved=True
            if not improved:
                reason='local_preference_poll_complete';break
            continue
        if r is None or not len(r):
            reason='unresolved_or_unsupported'; break
        jac=np.zeros((len(r),len(x))); complete=True
        for j in range(len(x)):
            delta=1.e-4*scale[j]
            direction=1 if x[j]+delta<=high[j] else -1
            probe=x.copy(); probe[j]+=direction*delta
            trial=call(probe,'finite_difference')
            rr=None if trial is None else residual(trial)
            if rr is None:
                complete=False; break
            jac[:,j]=(rr-r)/(direction*1.e-4)
        if not complete: reason='incomplete_jacobian_or_budget'; break
        rank=int(np.linalg.matrix_rank(jac))
        rank_diagnostics.append(dict(iteration=iteration,rank=rank,rows=len(r),columns=len(x),
                                     interpretation='diagnostic local; aucune preuve globale'))
        damping=1.e-8*max(float(np.linalg.norm(jac,ord=2))**2,1.)
        step=-np.linalg.lstsq(np.vstack((jac,np.sqrt(damping)*np.eye(len(x)))),
                             np.concatenate((r,np.zeros(len(x)))),rcond=None)[0]
        norm=float(np.max(np.abs(step)))
        if norm>.2: step*=.2/norm
        moved=False
        for factor in (1.,.5,.25,.125,.0625,.03125,.015625):
            proposal=x+factor*step*scale
            if np.any(proposal<low) or np.any(proposal>high): continue
            trial=call(proposal,'backtracking')
            rr=None if trial is None else residual(trial)
            if rr is not None and score(rr)<score(r):
                x=proposal; current=trial; moved=True
                br=residual(best)
                if br is None or score(rr)<score(br): best=trial
                if checkpoint and not trial['search_feasible']: checkpoint(trial)
                break
        if not moved:
            proposal=low+rng.random(len(x))*scale
            trial=call(proposal,'restart_seed_0')
            rr=None if trial is None else residual(trial)
            if rr is None: reason='budget_or_unresolved_restart'; break
            x=proposal; current=trial
            br=residual(best)
            if br is None or score(rr)<score(br): best=trial
    if current['search_feasible']: best=current
    return dict(history=history,best=best,accepted=accepted,termination=reason,
                local_jacobians=rank_diagnostics,evaluations=len(history),
                elapsed_seconds=time.monotonic()-start,seed=0,
                global_optimum_proven=False,infeasibility_proven=False)
