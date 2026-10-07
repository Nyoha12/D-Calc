# REGISTER-TARGET-01 — commandes prescrites et registre observé

Ce produit exécute un instrument fixe, ou une configuration statique d’assemblage,
avec son CONFIG, DESIGN, DB et modèle passif sauvegardé associés. Il ne réajuste
aucun modèle et ne choisit aucun contrôle en fonction d’une cible. Les paramètres
V2 sont provisoires, à calibrer ; une réussite logicielle ne constitue pas une
validation physiologique, matérielle ou A–E.

## Utilisation

```sh
python -m tools.register_target --plan plan.yaml --output-dir experience --dry-run
python -m tools.register_target --plan plan.yaml --output-dir premier --steps-this-run 1200
python -m tools.register_target --plan plan.yaml --output-dir suite --resume premier/checkpoint-000001.json
```

Le premier arrêt est explicitement partiel (code CLI 1), ses pas sont acquis.
Choisir le checkpoint indiqué par `result.json`, ou le dernier checkpoint complet
vérifié après interruption. Ne pas choisir un fichier `.pending-*`. La reprise
exige le même PLAN et les mêmes sources/versions/contexte. Elle conserve le quota
cumulé et ne recalcule pas les pas déjà engagés. Une modification du PLAN est
refusée, même si son but est seulement d’augmenter la durée. Déclarer dès le départ
l’expérience entière et employer `--steps-this-run` pour la découper.

L’API `pipeline.register_target.preflight` ne crée aucune destination et n’exécute
ni pas, fit, acoustique ni eigensolve. Elle réutilise les lecteurs stricts
`reporting.paired_onset` et `_case_context` sur le véritable cas. Les chemins du
CONFIG, DESIGN et modèle sont relatifs au PLAN ; les DB suivent les conventions
natives du CONFIG. Le certificat historique du fit est vérifié pour son identité,
pas recertifié. Les paramètres de recette non récupérables du fichier historique
restent signalés comme déclarations.

`nonlinear.register_target.Request.validate` fournit une représentation immuable.
`Experiment(model, request, source=...)` supporte des petits modèles synthétiques
explicitement qualifiés. `advance`, `checkpoint` et `restore` exposent le noyau
mémoire. `analyze(request, times=..., states=..., midpoint_times=..., pressure=...,
source=...)` analyse sans trajectoire des tableaux NumPy réellement sourcés. Les
cibles peuvent être modifiées pour cette analyse, sans changer les observations.
L’import de l’API depuis un client extérieur fonctionne ; une copie de la CLI
hors du checkout est refusée au lieu de revendiquer son SHA.

## Contrat du PLAN v1

Voir `examples/register_target/plan.yaml`. Tous les paramètres V2, rho, source et
ports sont explicites. Un seul cas, 1–8 plateaux et au plus 16 fenêtres. Chaque
plateau contient pression en Pa, fréquence propre labiale en Hz, damping_ratio,
pas entiers et durée concordante. Masse, aire, ouverture, largeur, Cd, contact,
signe, géométrie, modèle, fs et rho restent constants. Les champs supplémentaires
sont refusés, jamais ignorés. JSON/YAML refuse doublons, cycles, booléens numériques,
NaN et valeurs non finies. Les champs dérivés et quotas sont contrôlés avant les
tableaux scientifiques. Les fenêtres sont relatives à leur plateau et utilisent
les intervalles d’échantillons semi-ouverts `[start_step, stop_step)`.

L’initialisation est native. L’import externe R36 n’est **pas supporté** par cette
version ; il ne faut pas déguiser un ancien checkpoint en checkpoint du produit.
Le reset initial de l’expérience est conservé. Même contrôle : checkpoint natif
exact, y compris diagnostics et reset. Changement déclaré : tous les x, v, q,
vres, horloge et dernière perte sont transférés par les méthodes publiques ; le
nouveau reset natif correspond à l’état du segment. Les anciens diagnostics et
identités restent dans l’événement, les nouveaux diagnostics sont initialement
absents. Aucune identité n’est falsifiée pour passer `import_checkpoint`.

Le couplage utilise exclusivement `SimultaneousCoupling.step`, appelé par
`regime_reference.advance`. K=m(2πfl)² et r=2ζm2πfl restent natifs. À état inchangé,
le travail de commande est ΔE=½(Knouveau−Kancien)x², comparé aux deux énergies
labiales natives. Une pression seule ne donne pas d’impulsion de travail et le
changement d’amortissement ne change pas l’énergie stockée. Les travaux/pertes
natifs des pas et les travaux des événements ont des bilans distincts.

## Observations et exigences

La bande d’observation, indépendante des cibles, la taille/hop des fenêtres,
activité minimale, périodes minimales, erreur normalisée, concurrence des minima,
dérive et dispersion sont fixés avant les données. Le protocole de différence
quadratique utilise le même support W pour tous les lags :

`d(τ) = Σ(p[j] − p[j+τ])² / (2 W énergie_AC)`.

Les minima intérieurs sont interpolés par une parabole avec déplacement borné
à un demi-échantillon. Tous les candidats de la bande et leur résolution sont
conservés. Des retours à plusieurs périodes ne sont pas plusieurs registres.
Chaque fenêtre glissante conserve deux sensibilités temporelles ; aucun meilleur
morceau n’est sélectionné. Une bande restreinte ne renseigne pas les périodes
hors bande. Au moins quatre échantillons par période sont exigés ; cela ne
certifie pas l’absence d’alias dans un signal fourni. Les constantes, activité
faible, erreurs excessives ou sensibilités non résolues gardent une fréquence
nulle au sens JSON `null`, jamais zéro inventé. Il s’agit d’une adaptation
explicite de différence quadratique, pas d’une annonce de YIN complet.

Un pic spectral dominant à 210 Hz peut être une harmonique d’un signal à 70 Hz.
Les fréquences de passages dépendent de la section déclarée. Ces observables ne
remplacent jamais automatiquement `played_frequency`, qui désigne ici une
fréquence observée robuste selon ce protocole, pas une primitive mathématique
certifiée ni une preuve d’accessibilité au jeu humain.

PHASE optionnel prend tous les états et leurs échelles/unités explicites, TRAIN
puis validations disjointes, tous les groupes et deux sensibilités. Les pressions
conservent leurs temps natifs de milieu. Les erreurs SI et par composante sont
conservées même si le résultat est non résolu. Un maximum numérique sur les états
n’est pas implicitement une obligation de justesse musicale.

Les critères ont identifiant, rôle `hard`/`observe`, observable et fenêtres
explicites. Cible/tolérance ou bornes indépendantes : notes, Hz, cents et ratios
réutilisent `optimization.design_contract`. Les durées sont en secondes via un
adaptateur local. Un ratio demande deux fenêtres distinctes sans recouvrement ;
aucun rapport 3 n’est préféré universellement. Plusieurs fenêtres d’un critère
sont toutes requises, ce qui permet d’exiger la tenue ; l’activité demeure une
obligation séparée. Pression maximale et fréquence ne se compensent pas.
L’absence de critères donne une expérience descriptive. Un `observe` indisponible
ne bloque pas les hard indépendants. Une obligation unsupported/unresolved bloque
la conformité globale mais ne transforme pas les autres preuves en échecs.
Primitive universelle, Floquet/stabilité orbitale et accessibilité physiologique
restent unsupported.

## Ressources et résultats

Fs natif 1000–12000 Hz, jusqu’à 192 termes / 386 états. Au plus 72000 nouveaux pas
**et** 6 s par expérience entière, reprises comprises. La taille complète des
séries/checkpoints peut imposer une limite inférieure au plafond de pas. Les
quotas de calcul d’observation, fichiers JSON, séries et stockage sont vérifiés
avant allocations. Les enfants POSIX ont 180 s de mur au plus, CPU 175 s,
768 Mio et BLAS 1 ; l’orchestration est bornée à 600 s. L’API mémoire reste sous
la responsabilité du superviseur de son appelant pour les limites OS.

Les NPZ sont numériques stricts sans pickle, contrôlés par morceaux, avec
manifeste, hashes, checkpoints complets, paramètres/événements/bilans JSON,
CSV SI et synthèse française. Les observations riches et PHASE sont séparés du
résultat principal. Les références d’ancêtres évitent de copier l’historique.
`reporting.register_target.read_result` dans un processus neuf vérifie clôture,
manifestes, chaîne, contexte, versions, sources et continuité des séries.
Les sources historiques du fit et celles du calcul courant sont distinctes.

Un pas refusé ne modifie pas l’état natif. Un checkpoint n’engage qu’un morceau
écrit avant lui ; un morceau orphelin après interruption n’est pas une acquisition
confirmée. Les checkpoints précédents restent disponibles si export ou clôture
échouent. Aucun marqueur de réussite n’est publié avant les dernières écritures
faillibles, fermetures, restaurations de signaux et récolte de l’enfant identifié.
Ce protocole ne promet pas une transaction universelle sur disque. Les checksums
établissent l’intégrité, pas une preuve d’auteur.

Les tests publics utilisent des signaux analytiques et des modèles synthétiques.
Leurs métadonnées de fit factices testent seulement l’adaptateur de contexte.
Aucun fichier scientifique privé n’est une dépendance runtime ou de test.

La comparaison des périodes utilise aussi `period_error_floor`, seuil numérique
explicite distinct de `competitor_margin`. Un raffinement borné par interpolation
cubique sur support fixe réduit l’erreur due au lag fractionnaire. Les périodes
plus courtes encore concurrentes gardent l’ambiguïté ; les composantes sous la
résolution numérique déclarée ne sont pas certifiées absentes. Le protocole
conserve ses candidats et son domaine, sans prétention de primitive universelle.
Un critère explicite `state_recurrence` se rapporte exactement aux fenêtres de
validation PHASE correspondantes et exige tous les groupes et leurs sensibilités.
Ses valeurs sont les maxima aux échelles prescrites ; ce n’est jamais une
condition implicite de `played_frequency`.
