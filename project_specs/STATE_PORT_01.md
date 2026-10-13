# STATE-PORT-01 — R57 : port d'état numérique explicite

Ce bloc décrit un **nouveau modèle numérique fourni** et son expérience à débit
imposé. Il ne constitue ni un ajusteur, ni une réponse validée d'un instrument.
Aucune géométrie, TMM, calibration, promotion ou validation A–E n'est appelée.
La famille VF R50 reste candidate pour un raccord ultérieur ; aucun travail R50
n'est exécuté ici. Le catalogue R56 et les workflows `played_search` /
`register_target` restent inchangés et ne sont pas déclarés compatibles.

## Modèle public et unités

`didgeridoo_optimizer.nonlinear.passive_state.PassiveState` reçoit explicitement
`J`, `g`, `L`, `omega_ref`, `Zscale`, `sample_rate_hz`, `coefficient_origin`, et
éventuellement `domain` et `provenance`. Les coefficients sont immuables ; les
propriétés de tableaux rendent des copies. Aucun ancien modèle A/B/C/D n'est
projeté ou corrigé. `J == -J.T` doit être exact dans les nombres fournis : aucune
symétrisation, aucun clipping de pôle, aucune correction pour passer un test.

Avec `n = len(g)`, `J` est `n×n`, `L` est `(n+1)×r`, `0≤n≤128`, `0≤r≤129`.
`W=L Lᵀ=[[R,p],[pᵀ,d]]`, `A=J-R`, `B=g-p`, `C=(g+p)ᵀ`, `D=d`.
Les paramètres J/g/L sont sans dimension dans cette normalisation. L'état `x`
et le débit `U` sont en m³/s, la pression en Pa, `omega_ref>0` en rad/s,
`Zscale>0` en Pa.s/m³. `fs` est un entier entre 1 et 192000 Hz pour le modèle.

- `xdot = omega_ref*(A*x+B*U)` ; `P = Zscale*(C*x+D*U)`.
- `E = (Zscale/omega_ref)*xᵀx/2` en joules.
- `Z(s) = Zscale*(D+C*((s/omega_ref)I-A)^(-1)*B)`.
- Feedthrough : `Zscale*D`. DC : `Zscale*(D-C*A^(-1)*B)`.

La provenance des coefficients et les expressions du feedthrough et de la DC
sont conservées séparément. `D` n'est jamais appelé impédance DC.

La passivité est structurelle par le facteur L :
`P*U-dE/dt = Zscale*||Lᵀ*[x;U]||² ≥ 0`.
La stabilité asymptotique est un **autre** contrôle : valeurs propres de
`omega_ref*A` à partie réelle strictement négative, avec garde d'admission
`64*eps*||omega_ref*A||∞`. Cette garde peut refuser un système stable très
faiblement amorti ; le refus signifie que la stabilité n'est pas représentée
avec la marge numérique exigée, pas que la passivité est absente. La stabilité
du pas discret, les produits finis, la DC et le port sont également contrôlés.
Un modèle sans état est une résistance (éventuellement nulle).

## Pas et état

`h=1/fs`, `tau=h/2`, `M=(I-tau*omega_ref*A)^(-1)` :

```
xm    = M*x + (M*tau*omega_ref*B)*U
xnext = 2*xm-x
Pmid  = Zscale*(C*xm+D*U)
ph    = Zscale*C*M*x
Dh    = Zscale*(D+tau*omega_ref*C*M*B)
loss  = Zscale*||Lᵀ*[xm;U]||²
```

La construction effectue les résolutions denses/précalculs. `step(U)` ne fait
aucune résolution dense. Le bilan `E_next-E_old=h*(Pmid*U-loss)` est vérifié
avant engagement, avec erreur relative mise à l'échelle ≤2e-10. Les nombres
non finis, débordements et bilans non représentables sont refusés sans mutation.
`initialize(x, last_dissipation_w=0)`, `reset()`, `copy(with_state=True)`,
`export_state()` et `import_state()` exposent un état plat et indépendant.
`copy()` seul démarre au repos, comme le backend modal historique.

`continuous_response(f)` utilise `s=2j*pi*f`. `discrete_response(f)` utilise
exactement `s=2j*fs*tan(pi*f/fs)`, avec `0≤f<fs/2` ; au plus 4096 fréquences
par appel. `domain` vaut `continuous` ou `discrete_prewarped` et explicite
l'interprétation des coefficients. Aucun rééchantillonnage implicite, aucune
fréquence complexe passée au TMM. Une réponse fréquentielle ne modifie pas l'état.

## Fichiers et empreintes

Le schéma est `dcalc.passive_state.v1`, distinct de
`dcalc.passive_resonator.v1`. `save(path)` crée exclusivement un JSON
`{"parameters": ..., "sha256": ...}` ; l'empreinte SHA-256 est calculée sur
le contenu canonique trié. `load(path, expected_sha256=...)` peut aussi imposer
l'empreinte des octets du fichier. Le lecteur strict existant du dépôt refuse
clés dupliquées, NaN/Inf, débordements JSON, fichiers non réguliers et liens.
Les fichiers modèles chargés sont contrôlés par empreinte avant/après les
opérations sensibles, y compris le pas. Aucun pickle ni code exécutable.

L'état utilise `dcalc.passive_state.state.v1` avec empreinte du modèle, vecteur
`x`, dernière dissipation et checksum. L'import contrôle entièrement le candidat
avant engagement. Les tailles/types sont bornés avant coercition dense ; les
annotations JSON ont aussi des budgets de profondeur, nombre et taille.

## Expérience à débit imposé

Deux exemples numériques sont fournis dans `project_specs/examples/state_port/` :

- `modal.json` : `J=[[0,420],[-420,0]]`, `g=[0,sqrt(200000)]`,
  `L=diag(0,sqrt(70),sqrt(10))`, échelles 1 ; équivalent à
  `10+200000*s/(s²+70*s+420²)`.
- `negative_residue.json` : `J=0`, `g=100`,
  `L=[[sqrt(200),0],[400/sqrt(200),sqrt(200)]]`, échelles 1 ;
  `Z(s)=1000-150000/(s+200)`, DC 250 et feedthrough 1000 Pa.s/m³.
  Le résidu négatif n'empêche pas `Re Z(jw)≥250`.
- `flow.json` : 120 échantillons explicites SI à 12000 Hz, soit 0,01 s.

Depuis la racine du dépôt, avec l'interpréteur du projet :

```bash
python -m tools.state_port_reference \
  --model-in project_specs/examples/state_port/negative_residue.json \
  --signal-in project_specs/examples/state_port/flow.json \
  --output-dir /chemin/autorise/nouveau-dossier --dry-run

python -m tools.state_port_reference \
  --model-in project_specs/examples/state_port/negative_residue.json \
  --signal-in project_specs/examples/state_port/flow.json \
  --output-dir /chemin/autorise/nouveau-dossier

python -m tools.state_port_reference --read-result /chemin/autorise/nouveau-dossier
```

Le signal suit `dcalc.prescribed_flow.v1`, unités explicites, même fs que le
modèle, `initial_condition: "rest"`, tableau `flow_m3_s`, provenance. Le CLI ne
reçoit pas de CONFIG. `preflight`/`--dry-run` vérifie seulement les données et la
représentabilité algébrique, sans pas temporel, réponse fréquentielle ou sortie.

L'API `pipeline.state_port_reference.prescribed_flow(model, samples,
initial_condition="rest")` laisse le modèle fourni intact. `run(model_in,
signal_in, output_dir)` exécute un worker borné ; le CLI l'appelle également.
Le contrôleur exige POSIX et le thread principal. Le worker utilise BLAS1,
768 Mio, 175 s CPU, au plus 175 s de calcul. L'enveloppe publique démarre
avant le préflight : plafond calcul demandé + 2 s de préflight + 2 s de collecte
+ 1 s de clôture, soit au plus 180 s. Le préflight et les retours de chaque
phase sont vérifiés contre leurs échéances absolues. Une alarme au niveau de
`run` couvre aussi les opérations de lecture/publication ; aucun timer n'est
installé par pas numérique. Un timer public préexistant est refusé et les
handlers sont restaurés. La collecte réutilise `_collect_child` natif, avec
attente de terminaison préalable courte, puis drain/attente sous budget partagé.
Une collecte tardive ou non confirmée ne certifie ni `reaped` ni succès.
Les limites mémoire/CPU/BLAS1 sont vérifiées par le worker ; le lancement enfant
ajoute `RLIMIT_FSIZE=4 Mio` par fichier, notamment stdout/stderr. Ces limites
ne sont jamais appliquées à pytest ni au processus appelant l'API. Le point
`--worker` refuse un environnement non borné avant de lire le plan ou d'écrire.
L'API interne `worker(plan)` peut conserver une petite trace `failure.json` de
refus pré-calcul (notamment une entrée altérée), sans publier de résultat. Un seul enfant, 2400 pas maximum, durée ≤0,2 s,
fs≤12000 Hz. Les budgets de JSON sont vérifiés avant exécution/publication.

La sortie contient JSON strict, CSV, synthèse française, modèle numérique,
provenance des entrées et sources réellement chargées, état final, énergie,
travail et dissipation. L'index i décrit `[i/fs,(i+1)/fs]` ; pression et débit
sont attribués à `(i+0.5)/fs`, l'énergie finale à `(i+1)/fs`. Des instantanés
partiels immuables sont conservés tous les 64 pas. Le reçu final donne le code
de fin effectivement collecté du worker. Une fermeture avec empreinte lie le
reçu ; une annulation interdit d'annoncer un succès.

`reporting.state_port_reference.read_result` relit les fichiers et contrôle
empreintes, schémas, indices, continuité et sommes. Il ne reconstruit pas le
modèle et ne relance aucune physique. Il fonctionne sans les entrées d'origine.
Chaque nouveau module capture chemin et SHA-256 à son propre import. Les
frontières préflight/API/worker/lecture/CLI et clôture comparent ces identités
aux octets disponibles ; un modèle déjà importé avant le pipeline reste donc
lié à son empreinte initiale. `source_roles` distingue source seulement référencée
et module chargé vérifié. Le lecteur n'importe pas le pipeline. Le modèle garde
une vérification locale légère par opération ; aucun manifeste complet de code
n'est haché par pas. Ces contrôles d'intégrité relative ne sont pas une signature
d'auteur et ne prétendent pas authentifier une substitution intégrale.

Le bilan conserve exactement le seuil relatif 2e-10 : le défaut est divisé par
l'échelle non nulle, ce qui évite le sous-flux artificiel de `2e-10*scale`.
À énergie/travail/perte exactement nuls, le défaut est exactement nul. Aucun
plancher physique, changement de récurrence ni réglage global NumPy n'est ajouté.
Les vrais calculs non représentables restent refusés atomiquement.

La frontière finie des signaux se situe après publication et vérifications de
clôture. Un signal reçu/en attente jusque-là annule le résultat. Une publication
tardive ou échouée est invalidée par marqueur d'annulation, ou retrait du seul
marqueur terminal de cet appel si sa clôture n'est pas confirmable. Les données
partielles et journaux sont conservés.

## Raccord au couplage natif

`SimultaneousCoupling(PassiveState(...), params=..., rho=...,
v2_port_model="conjugate")` réutilise exactement le solveur scalaire, les lois
labiales/contact et le port conjugué existants. Le port affine provient du
nouveau backend et le vrai `PassiveState.step` reçoit le débit total aval.
L'état est `[x_lip,v_lip,x_res...]` ; les identités/checkpoints distincts sont
`dcalc.simultaneous.passive_state.identity.v1` et
`dcalc.simultaneous.passive_state.checkpoint.v1`.

Pour `PassiveResonator`, la disposition `[x_lip,v_lip,q...,v_res...]`, les
équations, arrondis, tolérances et schémas historiques sont conservés, y compris
les coefficients `a=0`. Aucun faux demi-vecteur modal, aucune conversion par
défaut, aucun backend FIR ou type inconnu admis. Les diagnostics natifs restent
ceux du modèle réduit ; ils ne valident pas une trajectoire instrumentale.

## Vérification

Les nouveaux tests utilisent le cas scalaire analytique, la transformation en z
indépendante, des bilans forcés au repos/nonrepos, plusieurs fs et le vrai
`PassiveResonator` natif. La transformation de comparaison modale dans les tests
est `[sqrt(a)*omega*q,sqrt(a)*v]` pour `a>0`. Un oracle utilisant la pression de
fin de pas échoue explicitement au bilan, ce qui vérifie la sensibilité.
Les tests couvrent le CLI réel, précontrôle sans physique, relecture dans un
processus frais, JSON hostile, tailles, indépendance/immutabilité, refus atomiques,
checkpoint/reprise et couplage conjugué. Les tests natifs ne sont pas modifiés.
Les preuves d'exécution et empreintes précommit sont remises séparément au pilote ;
les tests d'un futur SHA commité restent à sa charge.
