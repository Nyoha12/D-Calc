# NL-REGIMES-01 — progression et observations expérimentales

Ce workflow opt-in avance uniquement `SimultaneousCoupling.step`. Le calendrier
est simultané ; les ports restent `jet-only` par défaut, `conjugate` est explicite.
Aucun ancien corps physique, signature ou défaut n'est changé. Ce produit
n'appelle ni calcul acoustique TMM, ni fit, FIR, classement, score ou validation A–E.

## Entrées et exemple CLI

CONFIG, DESIGN, DB et variantes passent par les helpers natifs stricts de
`fixed_design` et `time_domain_reference`. Un modèle passif sauvegardé est
obligatoire : mêmes quatre empreintes, recette acoustique, domaine précompensé,
fs exacte, inventaire actif et certificat historique. Les coefficients sont
conservés. Vérifier les octets et la recette ne recertifie pas la fidélité du fit.
Les modèles synthétiques servent aux tests d'API, sans fausse recette CLI.

```bash
python -B -m tools.regime_reference \
  --config project_specs/examples/design_pitch/config.yaml \
  --design project_specs/examples/design_pitch/cylinder.json \
  --model-in MODEL.json --basis-completion r29 --h-cm 2 \
  --lip-parameters LIPS.json --rho 1.204 \
  --observation-plan OBSERVATION.json --target-steps 120 \
  --v2-port-model conjugate --output NEW_RUN --dry-run
```

MODEL doit réellement provenir de cette recette. Le maillage fait partie de
l'identité, même si deux maillages donnent un cylindre acoustiquement proche.
LIPS est un JSON contenant tous les champs de `DimensionedLipParameters.as_dict()`.
La pression est `mouth_pressure_kpa`, les autres paramètres sont en SI ; les
valeurs effectives restent dans le plan. La densité de Bernoulli est explicitement
choisie par `--rho` et conservée séparément de l'air historique du fit.

OBSERVATION déclare au minimum `windows`, `scales`, `section_index`,
`section_level` et `groups`. Exemple pour un témoin API à quatre états :

```json
{"windows": [[2, 2.5], [2.5, 3], [3, 3.5], [3.5, 4]],
 "scales": [0.0008, 0.40212385965949354, 1e-9, 1e-6],
 "section_index": 0, "section_level": -0.0004, "groups": [1, 2]}
```

Cet exemple n'est pas une échelle universelle ni celle du modèle chargé.
Il faut une échelle strictement positive pour **chaque** composante
`[x, v, q_i..., v_i...]`. `ObservationPlan` est immuable ; son empreinte lie
section, groupes, fenêtres, critères et budgets avant toute progression.
Une sensibilité postérieure exige un plan distinct, avec sa propre provenance.

Retirer `--dry-run` lance un seul enfant borné. Dry-run ne crée aucun fichier,
ne calcule aucune acoustique et n'exécute aucun pas. Pour reprendre :

```bash
python -B -m tools.regime_reference <mêmes entrées et plan> \
  --resume PREVIOUS_RUN/checkpoint-000001.json \
  --target-steps 240 --output ANOTHER_NEW_RUN
```

La cible est **cumulative depuis l'origine**, avec plafond absolu 6 s / 72000 pas
et 192 termes natifs. Une reprise ne renouvelle pas ce budget. Les dossiers et
sources doivent rester disponibles : chaque parent et série est vérifié, puis
les séries antérieures nécessaires sont relues sans recopier les gros artefacts.
Les fenêtres déclarées s'appliquent à la chaîne complète, sans nouveau comptage.

## Interprétation des observations

Les statuts distinguent équilibre observé, AC et dérive, récurrence de l'état
complet sur les fenêtres déclarées, période minimale non identifiée et stabilité
orbitale non évaluée. Aucun booléen `stable` universel. Les fréquences exportées
sont le pic FFT auxiliaire (moyenne retirée avant Hann), les passages montants à
la section et les retours après k passages. DC et AC non observée donnent `null`.
La fréquence de retour sur deux passages n'identifie pas une note à 33 Hz.
Tous les groupes fixes restent exportés avec les candidats concurrents et leurs
échecs ; aucun k n'est sélectionné automatiquement d'une fenêtre à l'autre.

Les critères déclarés v1 reprennent les repères expérimentaux R35 : au moins
12 retours, AC pression 0,01 Pa, variation d'équilibre 1e-6 aux échelles, dispersion
de période 0,002, amplitude et dérive logarithmique 0,005, retour absolu 0,003,
retour relatif à l'AC 0,01, forme 0,005 et moyenne d'état 0,001. Le plan peut
fixer d'autres critères avant simulation ; ils ne constituent aucune nouvelle
politique universelle ou calibration des lèvres.

Toutes les composantes sont contrôlées. La grille de forme a au moins 65 points
et deux points par pas sur le retour, quel que soit k. La reconstruction est
traitée retour par retour ; les plafonds de dimensions, travail et allocation
précèdent la conversion. L'interpolation linéaire/cubique est une sensibilité,
pas une borne rigoureuse. Les racines cubiques sont recherchées dans les mêmes segments échantillonnés ;
leurs écarts temporels et d'état sont exportés, sans certificat de localisation
continue, notamment au contact. Un échec de forme reste `observation_insufficiently_resolved` ; il
ne prouve ni décroissance transitoire, ni impossibilité de cycle.

Repères **historiques R35**, pas nouvelles mesures : modal 5000 Pa / 4 s à
4/8/12 kHz : passages 66,459819 / 66,481039 / 66,482417 Hz et AC 742,3940 /
737,3609 / 736,1330 Pa. Modal 1500 Pa : équilibre observé. Chargé 2700 Pa / 6 s :
forme complète hors critère, périodicité non résolue. Le témoin mathématique
exactement périodique à 66,482417 Hz avec pointe triangulaire explique pourquoi
un groupe 2 peut mieux passer à 12 kHz sans identifier la période minimale.
L'oracle continu indépendant R35 ne constitue pas un Floquet de ce pas discret.

## Checkpoints, intégrité et exports

L'API publique additionnelle `export_checkpoint` / `import_checkpoint` contient
modèle, paramètres, rho, fs, fermeture, budgets du solveur, état complet, temps,
pertes, diagnostics et état initial utilisé par reset. L'import valide tout sur
un candidat indépendant avant une seule mutation. Les anciens corps natifs sont
conservés par AST ; reprise et reset sont comparés bit à bit.

Les checkpoints numérotés sont immuables, chaînés par SHA256, vérifiés après
publication atomique. Les empreintes des octets du producteur sont conservées
séparément de la compatibilité numérique de reprise et du SHA Git. Une somme de
contrôle détecte corruption et incohérence ; ce n'est pas une signature attestant
l'identité d'un auteur malveillant. Dossiers existants, liens (y compris pendants)
et fichiers inattendus sont refusés. Les fichiers `.pending-<identifiant>`
sont des écritures non engagées, sans identité de checkpoint ; un arrêt pendant
sérialisation conserve le dernier checkpoint complet antérieur. Les fichiers
sources et résultats ne sont jamais écrasés.

Les handlers SIGINT/SIGTERM posent un drapeau ; le dernier pas accepté est sauvé
avant l'arrêt non nul. Chaque enfant a BLAS1, 768 Mio et plafond mural de 180 s ;
le paramètre `seconds` laisse une réserve de fermeture. Le budget de campagne
600 s doit être suivi par l'appelant entre commandes indépendantes. Le bundle et
son ascendance sont plafonnés à 250 Mio. Le superviseur ne récolte que son enfant.

`result.json`, CSV et synthèse française conservent statuts, raisons, groupes,
identités et nulls. Les CSV utilisent des cellules JSON pour préserver les
structures sans perte. NPZ conserve uniquement des tableaux numériques, jamais
de pickle. États aux temps n/n+1, ports au milieu et travaux sur [n,n+1] restent
distincts. Les éventuels diagnostics natifs optionnels non représentables sont
null en JSON et NaN dans les colonnes numériques NPZ, sans masquer un état invalide.

`read_execution(output)` résout l’issue terminale après terminaison : reçu
`execution.json`, sceau `execution.closed.json` et éventuelle annulation durable
prioritaire. PID, code réel, récolte et statut du superviseur restent séparés
de la complétude de données dans `result.json`. L’absence de clôture valide
n’est jamais un succès. Voir la frontière précise ci-dessous. Les sorties
d’API directe ne revendiquent pas ce reçu CLI.

Les bilans utilisent les travaux et stockages natifs : El+Er en conjugué,
terme mécanique conservé en jet-only. Fractions initiale/finale, changement de
stockage et écart énergie interpolée / énergie de l'état interpolé sont exportés.
Le prorata identique des travaux et énergies est un bilan algébrique, aucune
preuve indépendante de récurrence. Les paramètres physiques proviennent du plan.
Aucune validation empirique, promotion de matériau, son rayonné ou toot acquis.

## Provenance des deux entrées de commande (correction R36)

`reporting.regime_reference.read_json_source(path)` retourne `(valeurs, source)`.
Le hash SHA256 et le parse strict portent sur **le même buffer d'octets** borné,
issu d'un fichier régulier sans lien ; doubles clés, non-finis (y compris
`1e400`) et fichiers surdimensionnés sont refusés. La source contient `mode=file`,
`format=json`, chemin absolu, hash et valeurs `requested`. Aucun chemin privé
n'est nécessaire aux exemples publics.

L'API `preflight` / `run` accepte `request_sources={parameters: source,
observation: source}`. `request_provenance(params, observation, request_sources)`
contrôle les octets et leur concordance avec les objets demandés, puis ajoute
les valeurs `effective` après résolution des défauts du plan. Sans cet argument,
un appel API natif est explicitement `mode=inline`, avec valeurs demandées et
effectives, sans chemin ni hash de fichier inventé. Un descripteur inline ne peut
contenir de métadonnées fichier. Le worker exige la provenance sérialisée et
revalide les valeurs avant toute écriture de données ou progression.

Contrôles répétés : entrée et sortie du préflight, reconstruction worker,
avant le premier checkpoint, avant/après export et avant clôture du superviseur.
Un changement de source est un refus/échec de commande. Les données déjà
publiées restent immuables. Comme tout contrôle de fichiers, ces vérifications
observent des instants, sans verrouiller les écritures d'un processus extérieur.
Le garde de producteur CLI précède aussi `--worker`, le parsing et les écritures.

`request_sources` et `request_ancestry` sont distincts de `compatibility` et de
`plan_identity`. Une reprise avec paramètres/plan identiques, autre chemin,
autre mise en forme JSON ou API inline est compatible si les autres identités
numériques le sont. L'ascendance conserve chemin et hash des plans parents et
leur provenance demandée/effective ; une ancienne provenance non enregistrée
reste `null`. Les quatre entrées acoustiques et le certificat historique de fit
restent des identités distinctes. Aucune modification du checkpoint natif.

## Autorité de commande et frontière de complétion (correction R36)

Le lecteur normatif est `reporting.regime_reference.read_execution(output)`,
à utiliser après terminaison de `run`. Un lecteur pendant l'exécution ne doit
pas utiliser les fichiers comme notification de fin. Il exige le reçu candidat
`execution.json` **et** son sceau `execution.closed.json` lié par SHA256.
`execution.cancelled.json`, s'il existe, est prioritaire et lié au même reçu.
Un reçu/sceau absent, invalide ou une publication échouée ne donne jamais succès.
Le retour de `run` est exactement ce résultat normatif. Les anciennes commandes
sans sceau restent non confirmées par ce nouveau lecteur ; leurs données et
checkpoints restent reprenables.

La frontière finie est l'instant du **snapshot `sigpending()`**, SIGINT et
SIGTERM temporairement bloqués, après sérialisation, publication atomique et
fsync du reçu candidat, ainsi qu'après le dernier contrôle des sources.
Tous signaux déjà livrés (drapeau) ou en attente à cet instant rendent la commande
non réussie, y compris enfant déjà terminé, sérialisation, `os.link`, `fsync`
et retour de l'écriture du reçu. Sous le masque, la décision est alors engagée
par l'éventuelle annulation et le sceau immuables. Une erreur de cette dernière
publication laisse la commande non confirmée, jamais réussie.

Les signaux postérieurs au snapshot, notamment pendant l'engagement de cette
décision déjà fixée, sont **après** la frontière : ils ne rouvrent pas la commande.
Cette limite explicite ne promet pas l'impossible après retour/fin du processus.
Le masque et les handlers originaux sont restaurés ; seul l'enfant créé est
récolté. Les tests couvrent les deux côtés du snapshot. Les probes avec faux
enfant déjà fini et vrais signaux sont des tests de plomberie, distincts des
vraies CLI avec progression scientifique.

Les CSV fournissent colonnes scalaires typées et cellules vides pour les valeurs
indisponibles : statut/raison/identité/durée/pas, fenêtres/section/AC/FFT/passages,
et groupement/retours/erreurs/critères. `record_json` reste la copie exacte sans
perte de chaque ancien enregistrement. La synthèse française présente les
**DONNÉES**, renvoie l'issue de **COMMANDE** au lecteur normatif, expose fenêtres,
groupements, raisons et limites, et lie les détails sans recopier tout le JSON.
