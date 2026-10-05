"""Strict, immutable PHASE-01 exports and a finite analysis closure."""
from __future__ import annotations
import csv
import io
import json
from pathlib import Path
from . import regime_reference as native

ROOT=Path(__file__).resolve().parents[2]


def sources():
    result=native.sources()  # validates the roots of actual loaded modules
    for name in ('tools/phase_reference.py','didgeridoo_optimizer/nonlinear/phase_reference.py',
                 'didgeridoo_optimizer/pipeline/phase_reference.py','didgeridoo_optimizer/reporting/phase_reference.py'):
        result[name]=native.file_sha256(ROOT/name)
    return result


def export(output,result):
    out=Path(output)
    native.write_json(out/'result.json',result)
    def table(name,rows,fields):
        stream=io.StringIO(newline='');writer=csv.writer(stream)
        writer.writerow([*fields,'record_json'])
        for row in rows:
            writer.writerow([*[row.get(k) for k in fields],json.dumps(row,sort_keys=True,allow_nan=False)])
        native.atomic_bytes(out/name,stream.getvalue().encode())
    table('summary.csv',[result],['schema','ok','status','reason','samples','states','train_samples'])
    groups=[];windows=[];components=[];signals=[]
    for group in result.get('groups',[]):
        candidates=[('central',group.get('central'))]+[('half_'+str(s['half']),s.get('result')) for s in group['sensitivities']]
        for kind,candidate in candidates:
            label=dict(group=group['group'],variant=kind)
            if candidate is None:
                groups.append(dict(label,status='not_evaluated',reason=group['reason'],period_s=None));continue
            groups.append(dict(candidate,**label))
            for number,window in enumerate(candidate.get('windows',[])):
                row=dict(window,**label,window=number);windows.append(row)
                for c in window.get('components',[]):
                    index=c['component'];units=result['plan'].get('state_units') or []
                    components.append(dict(c,**label,window=number,unit=units[index] if units else 'caller SI unit',scale=result['plan']['scales'][index]))
            for name,signal in candidate.get('signals',{}).items():
                if not signal.get('windows'): signals.append(dict(signal,**label,signal=name))
                for number,window in enumerate(signal.get('windows',[])):
                    signals.append(dict(window,**label,signal=name,window=number,unit=signal['unit'],scale=signal['scale']))
    table('groups.csv',groups,['group','variant','status','reason','period_s','origin_s','passage_frequency_hz','return_frequency_hz','grouped_crossings','returns','timing_max_s','timing_rms_s'])
    table('windows.csv',windows,['group','variant','window','status','reason','samples','maximum_scaled','rms_scaled','worst_component','worst_time_s','maximum_nearest_phase','maximum_nearest_phase_s'])
    table('components.csv',components,['group','variant','window','component','unit','scale','maximum_si','rms_si','maximum_scaled','rms_scaled','worst_time_s'])
    table('signals.csv',signals,['group','variant','signal','window','status','reason','unit','scale','samples','maximum_si','rms_si','maximum_scaled','rms_scaled','worst_time_s'])
    lines=['# PHASE-01 — prédiction hors apprentissage','',
        'Traitement : '+result['status']+' ; '+str(result.get('reason') or 'analyse effectuée')+'.',
        'Le maximum sur tous les états reste la mesure principale ; le RMS est secondaire.',
        'TRAIN : '+str(result['plan']['train'])+' ; contrôles : '+str(result['plan']['validations'])+'.']
    for g in result.get('groups',[]):
        c=g.get('central') or {}
        vals=[w['maximum_scaled'] for w in c.get('windows',[]) if w.get('maximum_scaled') is not None]
        lines.append('Groupe '+str(g['group'])+' : '+g['status']+' ; '+
            ('maximum aux échelles '+format(max(vals),'.8g') if vals else str(g.get('reason') or 'aucun contrôle évalué'))+'.')
        sensitive=[]
        for s in g['sensitivities']:
            values=[w['maximum_scaled'] for w in (s.get('result') or {}).get('windows',[]) if w.get('maximum_scaled') is not None]
            sensitive.append('moitié '+str(s['half'])+' : '+(format(max(values),'.8g') if values else str(s['reason'])))
        lines.append('Sensibilités TRAIN, carte refaite sur TRAIN complet : '+' ; '.join(sensitive)+'.')
    lines+=['','La pression est celle du port au milieu du pas, pas un son rayonné. Les débits et la pression utilisent leurs temps natifs propres.',
        'Une couverture de phase dense ne change pas la fréquence de simulation. Les périodes groupées, fréquences de passages et de retours sont distinctes.',
        'Répétabilité descriptive conditionnelle aux fenêtres, échelles, section et carte ; aucune borne entre échantillons, note fondamentale, période minimale ou stabilité orbitale identifiée.',
        'Les observations historiques et le certificat de fit restent attribués à leur exécution originale. Une commande historique interrompue ne devient pas une simulation réussie.',
        'La clôture d’analyse se lit avec read_completion(output) ; result.json seul ne certifie pas la fin de commande.',
        '[Résultats](result.json) · [Groupes](groups.csv) · [Fenêtres](windows.csv) · [Composantes](components.csv) · [Signaux](signals.csv)']
    native.atomic_bytes(out/'summary.md',('\n'.join(lines)+'\n').encode())


def read_completion(output):
    """Require the final authority, not just a synchronized candidate closure.

    No guarantee is made about a signal arriving after the final decision sample,
    hardware failure, or subsequent modification by another process.
    """
    out=Path(output)
    try:
        if (out/'analysis.cancelled.json').exists():
            value=native.read_json(out/'analysis.cancelled.json')
            if type(value) is not dict or value.get('ok') is not False:
                raise ValueError('Invalid analysis cancellation')
            return dict(ok=False,status='cancelled',reason=value.get('reason'))
        result=native.read_json(out/'result.json')
        if type(result) is not dict or result.get('schema')!='dcalc.phase_reference.v1' or type(result.get('ok')) is not bool:
            raise ValueError('Invalid phase result')
        closed=native.read_json(out/'analysis.closed.json')
        if closed != dict(schema='dcalc.phase_closure.v1',result_sha256=native.file_sha256(out/'result.json'),ok=result['ok']):
            raise ValueError('Closure differs from result')
        completed=native.read_json(out/'analysis.completed.json')
        if completed != dict(schema='dcalc.phase_completion.v1',result_sha256=closed['result_sha256'],
                closure_sha256=native.file_sha256(out/'analysis.closed.json'),ok=result['ok']):
            raise ValueError('Completion differs from candidate')
        return dict(ok=result['ok'],status=result['status'],reason=result.get('reason'))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return dict(ok=False,status='unconfirmed',reason=str(exc))
