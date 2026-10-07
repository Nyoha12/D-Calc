# PAIRED-ONSET-01 / R44

Comparaison locale réutilisable de 1 à 4 couples CONFIG/DESIGN/modèle passif
sauvegardé, sous 1 à 16 scénarios labiaux communs déclarés. Refs #79, qui reste
ouverte pour jeu, robustesse, contraintes et configurations plus larges.
Aucun refit, réaccord, optimisation, simulation temporelle ou classement.
Les DESIGN R41/R43 sont des entrées statiques ordinaires avec leur propre modèle.

## Utilisation

```sh
python -m tools.paired_onset_reference --plan PLAN.yaml --output-dir DEST_NEUVE --dry-run
python -m tools.paired_onset_reference --plan PLAN.yaml --output-dir DEST_NEUVE
```

Le [plan d’exemple](examples/paired_onset/plan.yaml) contient des chemins de modèles
à remplacer par des réalisations déjà sauvegardées et associées à ces DESIGN.
Il ne fournit aucun fit inventé. Les chemins relatifs se résolvent depuis PLAN.
Le dry-run lit, valide et planifie sans acoustique, eigensolve, fit, pas natif,
écriture de résultat ou création de destination. La CLI copiée hors de son
worktree est refusée ; l’API importée depuis un programme extérieur est admise.
Une destination existante, même partielle, ne peut pas être réutilisée.

Le schéma `dcalc.paired_onset.plan.v1` est strict. Tous les champs de l’exemple
sont obligatoires, y compris `criteria: []` et `tmm: null`. Clés inconnues ou
dupliquées, cycles YAML, NaN/Inf, booléens numériques et liens symboliques sont
refusés. Les identifiants sont uniques dans chaque collection. Un plan validé est
sérialisé dans `Plan`, dataclass figée ; `as_dict()` restitue une copie indépendante.
L’empreinte du PLAN porte sur les octets effectivement interprétés, puis chaque
enfant recontrôle les octets, valeurs, quotas, sources et versions.

`reference_lips` contient les douze champs `DimensionedLipParameters` explicites.
Chaque scénario applique ses seuls `changes` à cette référence pour tous les cas.
Le signe et `mouth_pressure_kpa` ne sont pas des changements de scénario : le
protocole est inward −1 et chaque `pressure_pa` remplace la pression de référence.
Les paramètres demandés, effectifs, référence kPa et pression SI par évaluation
sont exportés séparément. Ces paramètres restent **to_calibrate** ; les variantes
R40 ne sont ni distributions physiologiques ni intervalles d’incertitude calibrés.

Une grille contient exactement `fractions` ou `pa`, 2 à 33 valeurs strictement
croissantes. Les fractions appartiennent à (0,1). Toutes les pressions doivent
rester dans `0 < Pu < Pcontact`, avec `Pcontact=K(h0−hmin)/A` : borne conservatrice
du protocole libre, aucune limite physique de jeu. Amont idéal et ports conjugate
uniquement. Jet-only est explicitement unsupported dans cette version.

## Algèbre et portée

Le cœur `nonlinear.paired_onset` réutilise les rigidité/amortissement V2 et
`onset_stability.validate_parameters/equilibria`. L’équilibre résout la loi
originelle non quadratée `delta+R0 Ujet(delta)=Pu`, avec contrôle de la borne
suffisante `1−R0 Cd w A/K sqrt(2 Pu/rho)>0`, du résidu original et de la parité
native. Sans cette borne, le point reste non résolu. Aucun Jacobien lisse au
contact, à la fermeture ou à DeltaP=0.

La matrice représente exactement les équations R40 :

```
dp = (R0 B dx − R0 A dv + somme(a_i dvi))/(1+C R0)
dU = B dx − A dv − C dp
xdot=v ; m vdot=−K x−r v+A dp
qidot=vi ; vidot=dU−gamma_i vi−omega_i² qi
F(s)=(m s²+r s+K)(1+C Z)−A B Z+A² s Z
```

Les échelles `[h0,h0*2*pi*fl,3e-4/omega²,3e-4/omega]` sont purement numériques.
L’oracle public est modal synthétique, pas mesuré : m=1e−4, fl=80, zeta=.2,
A=3e−6, h0=.0008, w=.012, Cd=.72, rho=1.204, signe=−1, hmin=1e−6,
Kc=1e4, Cc=.04, omega=2*pi*70, gamma=omega/8, a=1e7*gamma, R0=0.
Traversée conjugate : **4003.21108643 Pa**. Les tests utilisent aussi R0=1100,
les variantes R40 et les vrais pas simultanés à 4/12 kHz ; ils confrontent leur
dérivée à `(I−J/(2fs))^-1 (I+J/(2fs))` et à un quartique indépendant.

Toutes les racines sont exportées aux nœuds et aux raffinements. Le protocole
recherche les changements de stabilité **dominants**, puis effectue au plus 32
dichotomies par intervalle. Il ne recense pas les branches supérieures d’un
équilibre déjà instable. `local_crossing_verified` exige une paire simple non
nulle, suivi local unique et réciproque sans saut, autres racines stables,
compte instable 0→2, côtés cohérents et résidus propres/marginaux <1e−7.
Restabilisation, multiplicité et ambiguïté sont conservées sans certification.
Absence sur grille n’est ni impossibilité globale ni seuil minimal universel.

`root_real_s`, `root_imag_s` et `realization_frequency_hz` décrivent la réalisation.
Les coordonnées discrètes utilisent `z=(1+s/(2fs))/(1−s/(2fs))`, croissance
`fs log(abs(z))` et fréquence `arg(z) fs/(2*pi)`. En `discrete_prewarped`, s
n’est pas une fréquence continue physique à transmettre au TMM.

## Candidats et exigences

Chaque fenêtre possède `id`, `pressure_pa: [min,max]`, `frequency_hz: [min,max]`.
Elle identifie une candidate par ses coordonnées discrètes ; plusieurs
correspondances restent ambiguës. Aucun choix « plus proche de la cible ».
Un bracket qui traverse une frontière de fenêtre reste non résolu.
Les comparaisons descriptives exportent B−A et B/A pour chaque paire de cas,
scénario et fenêtre, avec identité des candidats et raison des valeurs nulles.
Les conditions acoustiques et domaines doivent être compatibles. Une permutation
inverse l’ordre, le signe des différences et les rapports. Une paire identique
a une différence exactement nulle, y compris l’incertitude corrélée.

Exemple de critère, à insérer dans `criteria` :

```yaml
- id: pressure_local
  role: hard
  case: cylinder
  scenario: central
  window: low_mode
  observable: onset_pressure
  target: {value: 2350, unit: Pa}
  tolerance: {value: 100, unit: Pa}
  priority: 1
- id: paired_pressure
  role: observe
  pair: [cylinder, exponential]
  scenario: central
  window: low_mode
  observable: pressure_difference
  target: {value: 0, unit: Pa}
  tolerance: {value: 50, unit: Pa}
```

Observables couvertes : `onset_pressure`, `onset_frequency` pour `case` ;
`pressure_difference`, `frequency_difference`, `pressure_ratio`, `frequency_ratio`
pour `pair`. Rôles `hard`/`observe`. Une priorité est conservée, jamais convertie
en score. Pression et fréquence sont deux exigences séparées, sans compensation.
Nombres/unités/notes/cents réutilisent `optimization.design_contract` sans modifier
R41 : cible fréquence `{note: C2, reference_hz: 440, cents: 0}`, tolérance
`{value: 10, unit: cent}` possible pour une fréquence absolue positive.
Les ratios ont l’unité `1`, les différences Pa ou Hz.

Le bracket numérique, le résidu et la tolérance utilisateur restent distincts.
La conformité nécessite que l’intervalle numérique soit inclus dans la tolérance ;
chevauchement de frontière ⇒ unresolved. L’intervalle fréquence est une estimation
locale issue des extrémités, pas une borne analytique certifiée de monotonie.
Tout hard unsupported/unresolved bloque `hard_conforming`. Un observe indisponible
rend la couverture partielle sans contaminer les hard indépendants. Zéro critère
est une comparaison descriptive valide. `played_frequency`, `toot_accessibility`,
`regime`, `transitions` restent explicitement unsupported, jamais remplacés par
onset ou un pic. Aucun instrument ou joueur « meilleur » n’est sélectionné.

## Modèles sauvegardés et provenance

L’adaptateur minimal appelle le preflight natif sans chargement de modèle, puis
les contrôles historiques d’identité R39 sur les métadonnées. Il valide les quatre
empreintes CONFIG/DESIGN/matériaux/règles, fs/domain, coefficients actifs/inventaire,
grille de fit complète, certificat historique, origine DC, complétion et identité
de recette acoustique. Aucun faux `ObservationPlan`, refit ou `_reload_certificate`.
Chaque enfant charge une réalisation une seule fois et vérifie qu’elle reste
inchangée. Les contrôles historiquement refusés ne sont pas relancés.

`guard_max_hz`, `guard_points`, `audit_points` restent des déclarations de recette :
le format de modèle historique ne permet pas de les reconstruire depuis les seuls
hashes spectraux. `recipe_evidence` rend cette limite visible. Empreinte intacte,
certificat sauvegardé et validation physique courante ne sont pas équivalents.
Les sources historiques du fit sont séparées des sources réellement exécutées.
L’API core autorise un `PassiveResonator` synthétique explicitement qualifié.

## Contrôle TMM facultatif

Remplacer `tmm: null` par un objet entièrement explicite :

```yaml
tmm:
  h_cm: 0.25
  loss_model: zk
  radiation_model: legacy
  air_reference: ck_dry20
  pressure_pa: [1000, 4000]
  frequency_hz: [40, 150]
  max_iterations: 12
  max_evaluations: 160
  max_ka: 2.0
```

DESIGN physique, rayon terminal physique, maillage/pertes/air/radiation déclarés.
Le TMM reçoit seulement un float Hz réel ; la mécanique utilise
`s=2j*fs*tan(pi*f/fs)` dans F. Fermeture moyenne par **R0 historique** conservée.
Le plafond ka déclaré est contrôlé ; il n’est pas une certification de précision
legacy. Une autre radiation ne modifie pas le modèle sauvegardé. Newton borné,
échecs conservés. `marginal_candidate` TMM ≠ traversée rationnelle ou TMM, période,
trajectoire ou nouveau fit. Les évaluations TMM utilisent le quota commun restant.

## Budgets, sorties et fin

192 termes/386 états maximum, incluant les réalisations utiles de 312/344 états.
Cas et scénarios traités séquentiellement ; enfants ≤180 s/768 Mio, CPU175 s,
BLAS1, fichiers bornés. Le superviseur possède son propre budget ≤600 s couvrant
préflight, ensemble des enfants, export et clôture. Il ne réduit pas à 180 s
l’ensemble des enfants. Quotas d’évaluations et stockage ≤100 Mio déclarés avant
calcul ; estimation racines/fichiers vérifiée avant matrices. Si la combinaison
de quotas dépasse les limites JSON/stockage, réduire les quotas explicitement.

JSON strict, unités SI, CSV cases/scénarios/grille/candidats/côtés/différentiels/
critères, synthèse française. Un fichier par unité évite l’agrégation des spectres
dans un JSON géant. Les checkpoints incrémentaux conservent résultats acquis et
compteurs, **pas un état de solveur complet ni une reprise automatique**. Ils
restent disponibles lors d’une interruption. Les calculs partiels ne deviennent
pas des succès. Aucun FIR 12000² ni simulation longue.

`reporting.paired_onset.read_result(DEST)` est l’entrée de relecture fraîche :
marqueur terminal, manifeste, fermeture, enfants réellement récoltés, contexte,
sources, versions et unités sont vérifiés. Un résultat candidat n’est pas une
autorité terminale. Le marqueur est publié après récolte, écritures et restaurations
faillibles, par une dernière liaison atomique sans écrasement. Une panne avant ce
point ne laisse aucun succès, même sans notification d’annulation. Ce protocole
ne prétend pas garantir universellement la durabilité après panne matérielle.

Tests publics autonomes : `test_paired_onset.py`, `test_paired_onset_cli.py`.
Les métadonnées synthétiques de la fixture CLI servent uniquement aux contrats
logiciels ; elles ne sont aucune preuve de fit acoustique. Les données privées
R39/R40 ne sont ni dépendances runtime ni fixtures indispensables publiées.
