# COMPARE-01 — comparer deux réponses existantes hors ligne

Le comparateur lit deux fichiers `dcalc.forced_response.v1`, choisit exactement
un couple cas/modèle par fichier et produit un diagnostic descriptif. Il ne
relance aucune acoustique, recherche de pics, notation ou optimisation. Aucun
export v1, coefficient, défaut ou critère physique existant n’est modifié.

## Usage

Depuis le checkout, avec un répertoire de sortie neuf :

```text
python -m tools.compare_forced_response --baseline baseline/forced_response.json --candidate candidate/forced_response.json --baseline-case body_bell --candidate-case body_bell --baseline-model zwikker_kosten_circular --candidate-model zwikker_kosten_circular --output-dir evidence/comparison
```

`--baseline-case`, `--candidate-case`, `--baseline-model`, `--candidate-model`
sont facultatifs seulement si les sélections restantes désignent un seul couple.
Une sélection ambiguë ou introuvable échoue avec la liste des couples disponibles.
Le sélecteur modèle désigne **les pertes** (`legacy_beta`,
`zwikker_kosten_circular` dans les exports actuels). La radiation (`legacy`,
`silva_unflanged`, `silva_flanged`) reste identifiée séparément dans le contexte.

`--dry-run` effectue lecture, sélection, comparaison, sérialisation et vérification
des destinations, sans créer de dossier ni artefact. En fonctionnement normal,
stdout contient un objet JSON `ok`, `schema`, `exports`, couverture et provenance ;
les erreurs donnent `ok:false`, une raison et un code 1. `--help` affiche l’aide.

Le module de comparaison emploie exclusivement la bibliothèque standard. La CLI
le charge directement depuis son chemin relatif au checkout : le `__init__` de
`reporting` importe historiquement tracés/classement et reste gelé. Cette CLI
fonctionne également avec `python -B -S`, sans paquets tiers ni import du moteur.
Ce chargement direct concerne uniquement ce diagnostic offline.

## Conditions d’admission

Chaque fichier est un JSON UTF-8 régulier, limité à **32 MiB**. Les doublons de
clés, NaN/Inf, valeurs numériques booléennes, nombres hors plage finie, schémas
inconnus et structures incomplètes sont refusés. Tous les cas/modèles sont
validés avant sélection : tableaux alignés, 1 à 20000 fréquences réelles positives
strictement croissantes, bornes/effectif cohérents, source, transferts, ports,
puissances, matériaux résolus, géométries physique et analysée. Un export bas
niveau sans contexte physique suffisant n’est pas admis comme comparaison
contextualisée. Aucun contexte absent n’est inventé.

La grille effective doit être **exactement égale** entre les sélections, ainsi
que le pas spatial demandé `h_cm`, les unités/conventions v1 et la source imposée
(type et chaque amplitude complexe crête). Aucune interpolation ou remise à
l’échelle. Les propriétés physiques de l’air effectif doivent être égales ; seuls
`identifier`, `provenance` et `thermoviscous_parameters_used` sont des annotations
exclues de cette égalité. Les propriétés thermovisqueuses sont requises pour ZK.
L’air original du CONFIG n’est jamais substitué à l’air effectif.

Les valeurs et leurs logs/phases représentables doivent être cohérents. Le
contrôle de sérialisation autorise 64 eps binary64 × max(1, |a|, |b|) dans les
logs/phases issus de libm ; ce n’est **pas** une tolérance acoustique ou de grille.
Les projections subnormales sont quantifiées : leurs logs/phases conservés ne
sont pas reconstruits depuis les composantes arrondies pour ce contrôle.

## Changements autorisés et attribution

Les changements de géométrie, matériaux, pertes et radiation sont autorisés et
énumérés. Les valeurs `acoustic_model` et variantes des matériaux sont comparées,
pas seulement leurs IDs. Les affectations de matériaux dans le profil et le
maillage sont contrôlées, y compris un déplacement de frontière entre matériaux.
Une subdivision homogène n’invente pas un changement de matériau.

L’ID du design et ses métadonnées décoratives ne sont pas une géométrie. Les
segments physiques et leurs paramètres le sont. Un changement de maillage à
profil physique identique est un facteur distinct. Les charges réellement
exportées sont conservées par fréquence ; un changement de charge sans changement
de géométrie/radiation est signalé séparément. La référence numérique `zref` et
la provenance producteur sont également contrôlées. Une modification du producteur
interdit d’attribuer implicitement l’écart à un seul facteur physique.

Plusieurs facteurs changés donnent explicitement **comparaison multifactorielle,
aucune causalité isolée**. Un seul facteur permet une lecture conditionnelle
locale, sans certification causale, amélioration universelle ou meilleur instrument.
Les dossiers `contexts` gardent géométries, matériaux complets, avertissements,
air, source, hypothèses de montage et provenances. Les annotations modifiées sont
signalées séparément des facteurs physiques.

## Métriques et disponibilité

À chaque point conservé, le sens est candidat − référence. Les observations
originales (valeur, statut, raison, log, signe et champs de qualité disponibles)
sont gardées pour **Zin, Hu, Yt, Pin, Pload, Pdiss, eta**.

| Métrique | Définition | Condition |
|---|---|---|
| Différence complexe | C − B, composantes réelle et imaginaire | Deux valeurs résolues représentables |
| Différence relative complexe | (C − B) / B, composantes réelle et imaginaire | B non nul ; aucune division par le seul module |
| Rapport des modules en dB | 20/ln(10) × (ln\|C\| − ln\|B\|) | Logs finis, modules non nuls résolus |
| Différence de phase | remainder(arg C − arg B, 2π), dans [−π, π] | Deux phases résolues non nulles |
| Différence réelle signée | C − B ; relative (C − B) / B | Valeurs résolues ; B non nul pour la relative |
| Rapport de puissances en dB | 10/ln(10) × (ln C − ln B) | Puissances positives résolues seulement |
| eta | Différence absolue et relative, sans dB | Rapport acoustique exporté disponible |

Les composantes relatives utilisent des intermédiaires rationnels exacts
`Fraction` avant projection binary64 : un débordement intermédiaire évitable ne
masque pas un résultat représentable. Les composantes de différence restent
indépendantes si une seule déborde. Toute projection dérivée non finie ou tout
résultat non nul sous la plage représentable devient `null` motivé. Les résultats
subnormaux restent disponibles mais signalés ; aucun epsilon ou écrêtage ajouté.

Un zéro analytique reste zéro pour les différences. Un quotient par la référence
nulle est `null` motivé ; les dB et phases d’un zéro sont indisponibles, y compris
0/0. `unavailable` et `roundoff_limited` ne produisent aucune métrique, même si une
valeur résiduelle signée est exportée. Les résidus `passivity_violation` gardent
leurs signes et avertissements ; une puissance négative n’a aucun rapport dB.
Pour Pin, `positive_log_resolved` doit confirmer la résolution positive.

`overflow`/`underflow` avec logs finis peuvent permettre des écarts dB et phases
sans reconstruire de valeurs cartésiennes. Les différences cartésiennes restent
alors indisponibles. Les nulls de log ne deviennent jamais des zéros. Les signes
sont lus avant les dB des puissances. L’extrapolation physique de radiation reste
un diagnostic séparé, jamais effacé par un statut numérique `ok`.

## Artefacts et traçabilité

Le nouveau schéma est **`dcalc.forced_response_comparison.v1`** :

- `comparison.json` : contextes complets, empreintes SHA256 des octets des deux
  fichiers, provenance producteur inchangée, provenance propre du comparateur,
  facteurs, définitions, couverture et tous les points/raisons.
- `comparison.csv` : une ligne par fréquence et observable, valeurs/statuts
  originaux, métriques et raisons, charge et statut physique des deux côtés.
- `comparison.md` : synthèse française, contexte/source, facteurs changés,
  hypothèses, couverture et points explicites (premier/dernier, 70/1000/1500 Hz
  lorsqu’ils appartiennent exactement à la grille), limites et définitions.

Chaque métrique compte tous les points ; `refused_indices` liste les indices
(base 0) refusés, dont les raisons figurent dans `points` et le CSV. Aucun point
refusé n’est éliminé d’un agrégat. Pas de moyenne masquant les indisponibilités.
La provenance du comparateur contient sa version, Python, ses deux SHA256 de
sources et le HEAD/dirty du worktree lorsque son appartenance Git est vérifiée.
Un HEAD dirty n’est pas présenté comme l’identité exacte des sources modifiées.

Les trois contenus sont sérialisés avant toute création. Chaque nom existant,
y compris un lien symbolique pendant, bloque le bundle ; la création exclusive
protège aussi contre un fichier apparu après le contrôle. Une défaillance I/O
tardive peut laisser des fichiers neufs partiels, sans succès annoncé ni
suppression automatique. Recommencer dans un nouveau dossier après diagnostic.
Les fichiers d’entrée ne sont jamais modifiés.

## Vérification et étude bornée

Commande native ciblée :

```text
python -B -m pytest -q didgeridoo_optimizer/tests/test_forced_response_comparison.py didgeridoo_optimizer/tests/test_forced_response_cli.py
```

Les nouveaux tests couvrent sélection et ambiguïtés, compatibilité, rapports,
phase circulaire, vrais zéros, signes, arrondi, subnormaux, limites finies/logs,
JSON strict, absence d’import moteur, écritures et dry-run. Deux petits exports
réels sont aussi produits par la CLI existante en subprocess puis comparés par
la nouvelle CLI. Les tests et fixtures historiques restent inchangés.

L’étude COMPARE-01 conserve hors dépôt six bundles produits depuis le main
canonique `af736016d3028885cd206610367baf98f131b797` : quatre built-ins,
legacy/ZK, CK sec 20 °C, 40–1500 Hz par pas de 10 Hz (147 points), h=.5 cm,
trois radiations × sources pression 1 Pa crête / débit 1e-6 m³/s crête. Les
comparaisons restent internes à une même source. Les commandes, codes, empreintes,
chiffres à fréquences explicites et liens de preuve sont dans le rapport de
remise hors dépôt ; aucun SHA futur n’est attribué aux résultats.

Les sorties décrivent des calculs synthétiques locaux : aucune mesure, validation
A–E, calibration, promotion matériau, monotonie générale, FFT jouée ou rendement
joueur. Une grille de 10 Hz ne suffit pas à déduire f0, Q ou déplacement de pics
sans analyse dédiée. Les watts des différentes fréquences ne sont pas additionnés.
