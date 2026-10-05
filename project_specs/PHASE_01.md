# PHASE-01 — carte stationnaire et prédiction hors apprentissage

Ce complément **offline** lit des séries natives archivées. Il ne simule pas,
ne relance aucun fit, ne charge pas CONFIG/DESIGN et ne remplace pas les
observations NL-REGIMES-01. Le résultat est une répétabilité descriptive
conditionnelle aux fenêtres, section, groupes, échelles et reconstruction.
`minimal_period`, `fundamental` et `orbital_stability` restent `null`.
Une faible erreur n'est ni une note identifiée, ni une validation expérimentale,
ni une borne sur la trajectoire continue entre les échantillons.

## Entrées et usage

```console
python -m tools.phase_reference --input-bundle ARCHIVE --plan phase.json --output NOUVEAU_DOSSIER --dry-run
python -m tools.phase_reference --input-bundle ARCHIVE --plan phase.json --output NOUVEAU_DOSSIER
```

Les trois chemins sont explicites ; aucune sortie par défaut dans le dépôt.
Le parent de sortie doit exister. Le dossier de sortie doit être neuf et ne
peut englober un bundle source, être ce bundle ou se trouver à l'intérieur,
y compris pour chaque ancêtre d'une reprise. Les liens symboliques sont refusés.
Le dry-run vérifie identités, chaînes, empreintes et budgets sans calcul de
phase, écriture ou simulation ; le chargement vérifie ensuite les en-têtes NPZ.

Exemple de plan pour **quatre** états (adapter les échelles à TOUS les états
réellement sauvegardés, sans retirer de composante) :

```json
{
  "schema": "dcalc.phase_plan.v1",
  "train": [1.0, 2.0],
  "validations": [[2.0, 3.0], [3.0, 4.0]],
  "section_index": 0,
  "section_level": 0.0,
  "section_direction": "rising",
  "section_method": "cubic",
  "groups": [1, 2],
  "scales": [0.001, 1.0, 1e-8, 1e-6],
  "state_units": ["m", "m/s", "m^3.s", "m^3"],
  "phase_tolerance": 1e-10,
  "max_phase_gap": 0.25,
  "min_crossings": 12,
  "cubic_iterations": 36,
  "max_points": 72001,
  "max_states": 386,
  "max_chain_bytes": 262144000,
  "batch_points": 1024,
  "component_block": 16,
  "seconds": 170.0
}
```

`PhasePlan` est une dataclass gelée ; les vecteurs de son API sont des tuples.
`from_dict` convertit un document JSON strict, et `as_dict` restitue les valeurs
effectives, y compris les valeurs par défaut. Le JSON original est conservé
comme demande, avec l'empreinte des **mêmes octets** que ceux parsés. Doublons,
NaN, Inf et débordements JSON sont refusés. Fenêtres ordonnées, disjointes et
semi-ouvertes ; toutes les validations suivent TRAIN. Groupes distincts entre
1 et 8, tous publiés, sans choix du meilleur groupe sur contrôle.

API de tableaux (oracles ou archives de recherche explicitement attribuées) :

```python
from didgeridoo_optimizer.nonlinear.phase_reference import PhasePlan, analyze
p = PhasePlan.from_dict(document)
r = analyze(times, states, plan=p, signals=signals,
            stop=lambda: False, callback=None)
```

`times` et `states` sont des ndarray réels de forme `(n,)` et `(n, états)`.
L'API conserve le mode `inline`, sans inventer un fichier d'entrée. Les valeurs
sont exprimées en SI ; les échelles ont les unités de leurs composantes.
`state_units` peut être omis pour des oracles ; l'unité demeure alors celle
que l'appelant déclare pour ses tableaux, sans déduction acoustique.
Les archives de recherche ne deviennent pas des bundles natifs certifiés.

## Méthode et différences de robustesse

La section utilise exclusivement les échantillons dont le temps appartient
à TRAIN. Le passage montant exige `x[i] < niveau <= x[i+1]`. La méthode linéaire
interpole le segment. La méthode cubique réutilise `regime_observables.interpolate`
sur quatre nœuds TRAIN, avec bissection déclarée. Le polynôme normalisé doit
avoir une dérivée strictement positive sur tout le segment ; sinon la section
est indisponible avec raison, sans inventer une racine unique. Segment et
résidu sont vérifiés. Aux deux bords TRAIN, seuls deux points sont disponibles :
repli **linéaire déclaré et compté**, sans lire le voisin de validation.
Cette condition de monotonie est conservatrice : une racine pourtant unique
peut être refusée si le polynôme ne permet pas ce certificat simple.

Pour chaque groupe k, les temps de section sont pris tous les k passages,
en commençant au premier passage TRAIN. Pour les ordinaux j :

```
T = Σ((j−moyenne(j)) (tau_j−moyenne(tau))) / Σ((j−moyenne(j))²)
t0 = moyenne(tau) − T moyenne(j)
phi(t) = ((t−t0)/T) modulo 1
```

T et t0 sont constants et appris sur TRAIN seulement. Les résidus de timing
maximum/RMS, le nombre de passages, de retours et de passages groupés sont
publiés. `min_crossings` compte les temps groupés, donc au moins onze
intervalles avec la valeur par défaut 12. Aucun gain, DC ou période par cycle.
`grouped_period_s`, `passage_frequency_hz=k/T` et `return_frequency_hz=1/T`
restent distincts ; le groupe 2 ne nomme pas une fondamentale à la fréquence
de retour.

Les phases natives sont triées et les phases adjacentes séparées d'au plus la
tolérance sont agrégées par moyenne. Dispersion intra-amas maximum/RMS en SI et
aux échelles exportée. Contrairement au prototype R37 qui joignait simplement
les extrémités, la coupure est placée dans le plus grand arc vide : les phases
proches de l'ancien 0 et 1 appartiennent ainsi au même amas. Les chaînes de
voisinage sont transitives. La carte est périodique et linéaire par segments,
y compris le raccord. Moins de trois nœuds ou un intervalle de phase supérieur
au `max_phase_gap` déclaré donnent une indisponibilité, pas une interpolation
non finie. Ce critère est une condition de reconstruction, pas un seuil de
classification physique.

La carte gelée prédit chaque valeur native du contrôle à son temps original.
Les sorties conservent maximum et RMS par composante en SI et aux échelles,
maximum global **sans dilution**, RMS global secondaire, pire composante et
instant, nombres de points, écart maximal de phase et distance maximale au
nœud TRAIN. Une couverture repliée dense ne change pas fs et n'est pas une
nouvelle fréquence de simulation.

Le support temporel admet seulement un arrondi de sommation de
`64 eps_machine × n_points × max(1, |dernier temps|)` secondes ; cette valeur
est exportée, sans déplacer les masques semi-ouverts des échantillons.

Deux sensibilités supplémentaires estiment T/t0 indépendamment sur chaque
moitié temporelle de TRAIN, puis **refont chacune la carte sur TRAIN complet**.
La solution centrale et les deux sensibilités sont conservées, y compris les
moins favorables. Moitié insuffisante : période `null` et raison. Aucune erreur
de validation ne choisit ou ne règle une période.

Le défaut `sensitivity_partition="temporal"` utilise deux moitiés temporelles
disjointes avec leurs propres racines et origines. Pour reproduire le calcul
R37 historique, le plan peut fixer `"r37_grouped_crossings"` : racines du TRAIN
complet, moitiés ordinales des passages groupés partageant le passage médian,
T estimé sur chaque moitié puis t0 recalculé sur tous les passages TRAIN.
Ce choix est déclaré avant calcul. Les deux protocoles peuvent différer :
le défaut temporel n'est pas ajusté pour retrouver les anciens chiffres.

Les diagnostics auxiliaires sont distincts pour pression, débit du jet et
débit aval. Les bundles fournissent leurs temps MIDPOINT sauvegardés, recoupés
avec les deux états voisins. Ils ne sont pas alignés silencieusement sur n+1.
La pression est celle du port d'entrée, pas le son rayonné. L'API reçoit
`signals={nom: {times: ndarray, values: ndarray, unit: chaîne, scale: nombre ou None}}`.
Sans échelle, seules les erreurs SI sont interprétables et les erreurs
normalisées restent nulles. Auxiliaire absent/non fini : indisponible avec
raison ; états/temps/échelles invalides : refus. Les tableaux auxiliaires sont
bornés à un vecteur réel de 64 bits au plus et au budget du plan. Une erreur
arithmétique auxiliaire (norme, moyenne ou normalisation non représentable)
rend cet auxiliaire indisponible avec raison et métriques nulles, sans retirer
les résultats des états. Aucun débordement n’est ignoré. Les calculs usuels
restent identiques ; les normes extrêmes ne sont pas promises représentables.

## Archives, provenance et dépendances gelées

L'adaptateur utilise les helpers privés existants, testés mais non rendus
publics : `reporting.regime_reference.read_json_source`, `safe_path`,
`file_sha256`, `verify_chain`, ainsi que `pipeline.regime_reference._history`
et `_load_history`. Il ne copie pas leur lecteur NPZ et n'appelle ni runner,
ni constructeur de couplage, ni parser CONFIG/DESIGN. Les modèles et sources
de simulation originaux peuvent être absents : leurs identités historiques
sont conservées, sans recertification du fit.

Le dernier checkpoint numéroté présent fait autorité pour les données
analysables. Ascendance, checksums, limites, continuité, colonnes, cadence,
états initiaux/finals, identité modèle/paramètres et observation enregistrée
sont recoupés. Une commande interrompue peut laisser un préfixe vérifié utile ;
`read_execution` conserve séparément l'issue historique normative. Un rapport
absent est attribué comme absent. Un état final non sauvegardé dans un rapport
partiel n'est pas analysé. Une fenêtre non couverte reste indisponible.

Limite héritée explicite : `_load_history` exige une **pression native finie**
dans un bundle. Un bundle dont cette colonne est non finie est donc refusé par
ce lecteur gelé ; l'API de tableaux prend en charge cette pression comme
auxiliaire indisponible. Les autres colonnes auxiliaires non finies ne retirent
pas les états. Aucun contournement ou changement du lecteur historique.

La provenance courante correspond aux modules réellement chargés et à la CLI
canonique ; une copie de la CLI extérieure à la racine chargée est refusée.
Plan, inventaire, empreintes et sources sont revérifiés à publication. Une
mutation invalide le succès, même si un résultat candidat est déjà sauvegardé.
Les observations R36 restent attribuées séparément, jamais remplacées par
l'erreur PHASE-01.

## Ressources, exports et clôture

Plafonds avant allocation : 72001 points, 386 états, chaîne 250 Mio. Une matrice
native est chargée ; les reconstructions utilisent des blocs de composantes
et de points, jamais train × contrôle × états. Les fenêtres sont des vues,
y compris pour les matrices Fortran. Le lecteur existant charge au plus un
chunk NPZ de 12000 pas en plus des matrices finales. Le cœur coopère avec un
stop/callback et un budget temps à chaque lot. Lecture NPZ, empreintes et
publication atomique finissent leur opération bornée avant le prochain stop.

`result.json`, `plan.json`, cinq CSV (`summary`, `groups`, `windows`,
`components`, `signals`) et une courte synthèse française
conservent les résultats. Les colonnes scalaires servent à l'analyse et
`record_json` garde le détail sans perte. Les fichiers sont immuables, écrits
par les helpers atomiques existants, avec leurs liens `.pending-*` conservés.
Un budget conservateur de lignes (3 Mio estimés) refuse avant calcul les
combinaisons groupes × fenêtres × composantes trop grandes.
Le plafond JSON natif est 4 Mio par document ; dépassement explicite, jamais
NaN/Inf ou sortie tronquée annoncée réussie.

`reporting.phase_reference.read_completion(output)` exige l’autorité finale
`analysis.completed.json`, liée aux hashes de `result.json` et du candidat
`analysis.closed.json`. Le candidat seul ne confirme plus un succès, y compris
pour les sorties antérieures dépourvues d’autorité finale. Une annulation
`analysis.cancelled.json` prime ; autorité absente, corrompue ou discordante =
succès non confirmé. Erreur, interruption ou budget temps avant complétion
donnent un code CLI non nul, même si l’écriture d’annulation échoue. Les données
partielles restent conservées.

Protocole fini : publier et synchroniser les exports et le sceau candidat ;
préparer et synchroniser l’inode de l’autorité sous un nom `.pending-*` sans
valeur normative ; masquer SIGINT/SIGTERM sur les plateformes POSIX qui le
permettent ; recontrôler les sources ; échantillonner le stop, le budget et les
signaux livrés/en attente. **Ce dernier échantillonnage est la frontière de
complétion de l’analyse.** Un signal pendant ou après le sceau candidat reste
avant cette frontière et invalide l’analyse. Les handlers restent actifs ;
masque et handlers antérieurs sont restaurés dans tous les cas. Sans masque
POSIX, le même échantillonnage utilise les flags des handlers.

Après cette frontière, seul un lien atomique sans écrasement publie l’autorité
préparée. Un échec du lien laisse la commande non réussie ; aucun contrôle de
stop ni opération de données susceptible d’échouer ne suit un lien réussi.
Les signaux ultérieurs ne rouvrent pas l’analyse, y compris pendant la
publication de ce lien ou la restauration des handlers. Aucune garantie de
durabilité après crash matériel n’est revendiquée pour cette dernière entrée
de répertoire (pas de fsync après publication de l’autorité), ni de garantie
universelle après sortie ou modification extérieure ultérieure. Le reçu CLI
et le lecteur normatif concordent après terminaison normale. Aucun superviseur
de simulation copié.

## Références et validation

La méthode reprend le diagnostic offline R37. Les archives R35 sont utilisées
comme références privées via l'API ; elles ne sont pas embarquées. Les tests
natifs comprennent sinusoïdes à trois fs/deux groupes, pointe périodique,
faible fondamentale/retour deux passages, dérives, modulation, quasipériodicité,
chirp, DC, raccord et dupliqués, frontières non fuyantes, sensibilités, invalides,
budgets, gros C/F, vraie CLI et chaînes de reprise/corruptions/clôture.

Sous hypothèse C², le reste d'interpolation linéaire par segment est au plus
`h² sup|f''|/8` (DLMF 3.3, équation 3.3.5 ; Driscoll/Braun FNC 5.2).
Pour un coude de dérivée J à la fraction theta du segment, l'erreur de corde
est `|J| h theta (1−theta) <= |J| h/4`. Ces hypothèses ne sont pas garanties au
contact ; aucune borne universelle sur les archives n'est invoquée.
