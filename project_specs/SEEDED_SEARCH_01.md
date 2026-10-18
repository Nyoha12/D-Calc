# SEEDED-SEARCH-01 — départs fixes explicites

L'extension opt-in de `dcalc.played_search.job.v1` admet plusieurs DESIGN physiques
contre **le même Contract original**. Elle permet de proposer simultanément
plusieurs variables, notamment en réutilisant les fichiers `design_*.json` de
`constrained_design`. L'admission d'un profil ne certifie aucune contrainte
acoustique. Le nom du fichier et une conformité annoncée ailleurs ne sont pas
une preuve. Le REQUEST original et tous les scénarios restent applicables.

```yaml
search:
  normalized_steps: [0.25, 0.125]
  directions: [-1, 1]
  stop_on_witness: false
  starts:
    include_initial: false
    mode: evaluate_only
    profiles:
      - {id: proposal_a, design: proposals/design_001.json}
      - {id: proposal_b, design: proposals/design_002.json}
```

Les trois champs de `starts` et les deux champs de chaque profil sont obligatoires.
`include_initial` est un booléen strict ; `mode` vaut `evaluate_only` ou `poll`.
La liste comprend 1 à 8 profils, avec identifiants uniques conformes au validateur
natif (`[A-Za-z][A-Za-z0-9_-]{0,63}`). `initial`, `nominal` et `coordinate_poll`
sont réservés, avec distinction de casse. Les chemins DESIGN sont relatifs au
JOB ; `..` reste permis selon la convention native pour partager des références
publiques. Chemins absolus et liens symboliques sont refusés. Aucun `model_in`
ou modèle externe n'est admis dans un départ.

Les clés inconnues, booléens numériques, NaN/Inf, doublons de clés, cycles, fichiers
non réguliers ou de plus de 2 Mio sont refusés avant destination/science. Un DESIGN
contient 1 à 128 sections avant allocation native. Un plan seeded résolu est limité
à 3 Mio, laissant de la place à l'enveloppe enfant native de 4 Mio. Les autres
plafonds JOB et la supervision native restent applicables. `starts` avec `assembly`
est explicitement refusé ; l'assemblage historique sans `starts` reste disponible.

## Admission et représentation

Chaque source passe par le lecteur strict natif puis `validate_design`, utilisant
la base matériaux et le CONFIG du Contract de référence. `Contract.check_locks`
vérifie bornes, groupes liés, relations dérivées, ID, nombre/type/matériaux des
sections, paramètres non variables et annotations. Les variables SI proviennent
des champs natifs (`field_info`), indépendamment de l'unité choisie pour leurs
bornes dans le REQUEST.

Le profil est régénéré par `Contract.generate`. Son encodage canonique doit être
exactement celui du profil natif fourni, y compris les types des annotations.
Une relation dérivée fausse, une différence non représentable ou un aller-retour
SI qui change une dimension est refusé : aucune nouvelle tolérance, réparation,
projection ou correction silencieuse. Les positions et `metadata.total_length_cm`
suivent uniquement la reconstruction native. Leurs valeurs fournies restent dans
les octets source conservés. Les annotations ordinaires restent verrouillées.

Le plan conserve séparément source/empreinte des octets, données fournies,
profil natif généré/empreinte et vecteur SI. L'initial original conserve sa source
propre, même lorsqu'il est explicitement exclu des évaluations.

## Ordre, arrêts et budgets

1. Si `include_initial: true`, le point initial original est premier. Sinon il
   reçoit une décision `not_attempted / initial_excluded` et n'est pas lancé.
2. Tous les départs déclarés suivent dans l'ordre du JOB. Les doublons référencent
   le candidat déjà acquis (`duplicate_of`) et ne lancent aucune unité.
3. `evaluate_only` s'arrête après ces points uniques. En `poll`, chaque départ
   retenu sert ensuite d'ancre, dans le même ordre. Pour chaque départ : pas
   décroissants déclarés, variables déclarées, puis directions déclarées.
   Chaque tour conserve son ancre fixe ; son meilleur candidat devient le centre
   du tour suivant, selon `better` natif. Un candidat déjà acquis peut servir
   d'ancre ou de meilleur voisin sans nouvelle évaluation.

Il existe un seul ensemble de déduplication et un seul budget cumulé : candidats,
fits, trajectoires, pas facturés/acquis, enfants, durée et stockage. Aucun budget
n'est remis à zéro par départ. `stop_on_witness` s'applique dès la première
évaluation, y compris pendant les départs ; les départs restants portent le motif
de non-tentative. Sans hard, seuls les départs explicitement retenus sont décrits,
même en mode `poll`. Sans variables, il n'y a aucun déplacement.

L'évaluateur est exactement celui de `played_search` : génération native sous
verrous, filtrage seulement sur violation statique hard prouvée, modèle neuf adapté
à la géométrie via le backend explicitement choisi, critères et observateurs natifs.
Les fonctions `played_rows`, `recipe`, `verdict`, `better` ne changent pas. Aucune
moyenne compensatoire ; inconnu, refus, partiel et non évalué restent distincts.
Une fréquence passive n'est pas une note jouée.

Sans `starts`, aucun champ d'origine n'est ajouté aux résultats/CSV et le poll
historique conserve ordre, valeurs, décisions et terminaisons. Le JOB sauvegardé
reste celui fourni, sans paramètres seeded inventés pour un ancien résultat.

## Preuves et lecteurs

Chaque candidat et décision seeded porte `start_origin` : ID du départ, source
exacte, vecteur initial SI, empreinte du profil généré et mode. Le vecteur du
candidat est conservé séparément, puisqu'un poll peut le déplacer. Les checkpoints
conservent aussi cette origine pour le candidat courant et les décisions acquises.
Les CSV ajoutent `start_id`, `start_source_sha256`, `start_variables_si`,
`start_generated_sha256` et `start_mode`.

`starts/<id>.supplied.<extension>` conserve les octets fournis ;
`starts/<id>.native.json` conserve le profil généré. Le lecteur reconstruit le plan
calculatoire depuis les sources originales et vérifie les deux représentations.
Il rejoue uniquement l'ordonnancement et la sélection sur les résultats enregistrés,
puis compare candidats, décisions, meilleur/témoin, terminaison et CSV. Les
préfixes des checkpoints sont contrôlés de la même façon. Cette vérification de
séquence n'effectue aucun calcul acoustique, fit ou pas joué ; les lecteurs des
preuves scientifiques gardent leur rôle natif. Les sources sont revérifiées avant
et après lecture. Un ID/vecteur/profil/mode modifié dans une copie puis réempreint
ne remplace pas cette correspondance.

Les empreintes établissent une intégrité et une association vérifiable aux sources
encore disponibles, pas une signature d'auteur. Les inventaires de modules importés
par le producteur sont vérifiés sur disque ; ils ne sont pas remplacés par ceux du
lecteur. Il n'y a ni cache inter-runs ni reprise globale. Les reprises internes
natives R63 restent inchangées.

## Exemples publics

Depuis la racine du dépôt, avec des destinations nouvelles :

```sh
python -m tools.played_search --job project_specs/examples/seeded_search/job-filter.yaml \
  --output-dir /tmp/seeded-plan --dry-run
python -m tools.played_search --job project_specs/examples/seeded_search/job-filter.yaml \
  --output-dir /tmp/seeded-filter
python -m tools.played_search --read-result /tmp/seeded-filter
```

`reference.json` est un cylindre numérique de 92 cm, diamètre 30 mm. Deux profils
explicites proposent simultanément longueur et diamètre : 93 cm / 31,25 mm et
94 cm / 37,5 mm. Le REQUEST commun autorise ces variables mais impose une obligation
hard de longueur entre 91 et 92 cm. Les deux départs sont donc admis puis rejetés
par une vraie évaluation géométrique native : **zéro fit et zéro pas joué**. Le code
CLI 0 signifie publication complète de cette exploration, sans témoin conforme.

`job-short.yaml` propose deux profils à 92 cm avec diamètres distincts, sous le
même REQUEST. Il sélectionne explicitement `certified_scalar_v1` et réutilise le
petit template public `geometry_state/experiment.json` par chemin relatif. Ce
parcours positif de raccord doit être acquis séparément au SHA validé par le
pilote : ses 96 pas par candidat, soit 0,008 s à 12 kHz, **ne permettent pas
d'établir une note**. Ce n'est ni une solution physique universelle ni une
validation matérielle ou physiologique. L'auteur R68 ne lance pas ce parcours.

## Compatibilité par sources

Les archives de recherche/dossier qui épinglent les modules `played_search`
modifiés ne sont pas compatibles avec leurs nouveaux octets. Aucun changement
d'étiquette, override de SHA ou contournement du lecteur ne les rend compatibles.
R67 head-cli/dossier reste attaché à `design-dossier-01` et à `8b66944`, conservés.
Selon le constat transmis au mandat, les preuves primaires R64/R66 inspectées
n'épinglent pas `played_search` ; ce lot ne les modifie ni ne les rejoue. Les tests
portables `design_dossier` vérifient sa fonctionnalité sur les nouvelles sources,
sans réattribuer les acquisitions historiques à ces sources.
