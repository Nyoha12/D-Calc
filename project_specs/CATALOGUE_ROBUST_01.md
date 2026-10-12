# CATALOGUE-ROBUST-01

Statut de cette version : proposition à relire et à valider.

## Contrat

Un JOB référence un JOB adjustment_policy natif et 1 à 8 géométries nominales.
Chaque candidat conserve le même instrument physique dans tous ses scénarios.
Seules les commandes autorisées par fixed, per_configuration ou shared_offset
peuvent varier. R54 reste propriétaire de leur recherche et de leur vérification.

Le masque nominal contient des chemins dimensionnels natifs, une unité de
longueur et des bornes nominales. Il ne permet ni changement de matériau,
topologie, configuration, commande initiale, modèle ou demande. Les relations
et bornes REQUEST sont revalidées au nominal de chaque candidat.
Les bornes nominales ne sont pas appliquées aux écarts de fabrication.

L'identifiant supérieur de géométrie et l'annotation supérieure catalogue_label
peuvent différer. Les identités et l'ordre des pièces, blocs et configurations
restent communs. Les autres annotations restent verrouillées.
Les positions et le total explicitement fournis dans un DESIGN sont vérifiés
avant la normalisation native ; aucune donnée déclarée incohérente n'est réparée.

## Décisions

- admissible : témoin R54 sémantiquement vérifié pour tous les hard échantillonnés ;
- excluded_fixed_counterexample : contre-exemple vérifié aux commandes fixes ;
- excluded_nominal_geometry : contradiction géométrique nominale reconnue nativement ;
- unresolved : capacité, calcul, recherche ou preuve non résolus ;
- unexamined : candidat prêt mais non lancé.

Un échec de recherche adaptative ne prouve pas l'absence de commande satisfaisante.
Un toot obligatoire unsupported empêche le témoin ; aucun pic ne le remplace.
Une observation facultative manquante laisse subsister les hard indépendants.

catalogue_complete signifie que chaque entrée a été examinée, pas décidée.
candidates_decided signifie que chaque candidat a un témoin ou une exclusion.
execution_complete exige les résultats natifs complets et leur collecte.
request_fully_covered conserve les lacunes natives, y compris les observations.
admissible_candidates conserve les témoins, même sans clôture terminale.

Sans preferences, tous les admissibles sont rendus ; best_known est vide et
selection_complete reste faux. Une liste explicite de préférences ordonne
lexicographiquement des longueurs nominales natives par priorité distincte.
Les unités m/cm/mm sont vérifiées, les comparaisons utilisent des rationnels,
et tous les ex aequo sont conservés. Aucune moyenne ou pondération n'est ajoutée.
Une préférence absente laisse le candidat admissible mais non classable.

best_known porte exclusivement sur les témoins vérifiés et classables connus.
selection_complete est conservateur : catalogue parcouru, candidats décidés,
exécution complète, couverture des concurrents non exclus, préférences connues
et clôture confirmée. Une exclusion géométrique nominale démontrée dispense
ce seul candidat de couverture acoustique pour la comparaison ; la couverture
globale REQUEST reste explicitement fausse.
Il ne certifie aucune optimalité continue ou meilleure commande adaptative.
Une longueur ne constitue ni un coût ni une masse.

## Composition et ressources

Chaque appel public R54 s'exécute dans le thread principal d'un processus neuf,
sans alarme héritée après exec. Ses gardes et plafonds sont inchangés :
worker scientifique AS768 Mio, CPU175 s, calcul mur180 s.
Le contrôleur isolé possède une enveloppe distincte AS1024 Mio/CPU200 s.
Le parent catalogue possède un budget mural cumulatif explicite, au plus 600 s.

Un seul contrôleur et donc au plus un worker scientifique sont lancés à la fois.
La session/groupe créé appartient exclusivement à cet appel. Le parent Linux
active temporairement l'adoption des descendants orphelins et restaure son état.
Il collecte son enfant direct via Popen et les seuls enfants adoptés de ce groupe
via waitpid(-pgid). Aucun waitpid(-1), kill global ou inventaire d'autres processus.
Une collecte non confirmée interdit le lancement du candidat suivant.
TERM puis KILL éventuel partagent un budget de collecte total de 10 secondes.
Les observations, essais et reçus partiels ne sont pas supprimés.

Avant allocation, la réservation est :
8 Mio + N * (budget_output_R54 + 16 Mio), dans le plafond catalogue de 200 Mio.
La réserve couvre documents, logs, artefacts et clôture. Les plafonds natifs ne
sont jamais augmentés : un catalogue de huit entrées peut exiger un budget
output natif inférieur pour respecter cette réservation conservatrice.

Les compteurs distinguent candidats nominaux, examinés, décidés, scénarios
déclarés, essais de commande, projections et appels spectraux natifs.
Les raffinements et leurs traces restent dans les bundles R54.
Une projection n'est pas présentée comme un appel TMM.

## Entrées, sorties et relecture

API : pipeline.catalogue_robust.load_inputs et run.
CLI : python -B -m tools.catalogue_robust.
Dry-run : validation native sans recherche, maillage, TMM, processus ou sortie ;
stdout est silencieux en succès. Utiliser -B pour éviter les caches Python.

Chaque sous-répertoire candidat contient nominal.json, audit.json, policy.json
et un bundle native R54 avec observations, critères, commandes et profils.
Les références CONFIG/REQUEST restent celles du JOB original ; leurs contenus
et les octets de DB sont liés par les plans et sources natifs.
Le bundle catalogue contient plan, résultat, comparaison CSV, synthèse française,
manifest, reçus de collecte et reçu terminal.

read_result(output, original_job) recharge les entrées originales sans importer
le producteur catalogue ou adjustment_policy. Il reconstruit le plan attendu,
utilise la relecture sémantique R54 et recalcule les décisions et la sélection.
Le lecteur peut importer des modules passifs via les validateurs natifs ; il
n'appelle aucun producteur acoustique, ajusteur, maillage ou TMM.
Les scalaires conservés ne sont pas acoustiquement recertifiés.
Les empreintes constituent une intégrité relative, pas une authentification.

Les entrées originales doivent rester disponibles et inchangées. La relocation
automatique des références d'un bundle n'est pas prise en charge en v1.
Un reçu absent ou annulé interdit la clôture complète sans effacer les témoins.
Une réécriture cohérente de hashes ne rend pas acceptable un mélange de géométries,
scénarios, cibles, rôles ou commandes incompatible avec les entrées originales.

## Exemples et références

examples/catalogue_robust/fixed.json : trois cylindres nominaux de 120,11,
120,16 et 120,21 cm, ID30 mm, PVC, coupe 0/±0,5 mm, f1 70 Hz ±1 cent.
Référence numérique R55 prescrite : seul le centre satisfait tous les scénarios.
Cette référence n'est ni une mesure ni un calcul réalisé par la présente proposition.

examples/catalogue_robust/assembly.json compose le petit exemple natif R54
shared_offset. Le catalogue n'ajoute aucun degré de liberté physique.
examples/catalogue_robust/assembly_fixed.json impose les commandes du même
assemblage. Un résultat violé est un résultat explicite, pas un échec de CLI.

## Validation à effectuer après application autorisée

Exécuter les deux nouveaux modules de tests et les suites historiques
test_adjustment_policy, test_adjustment_policy_cli, test_tolerance_scenarios,
test_tolerance_audit et test_tolerance_audit_cli, sans modifier leurs assertions.
Relire les sorties CLI dans un processus frais et comparer les sous-plans et
résultats aux workflows natifs. Les tests de supervision emploient des doubles ;
ils ne créent pas volontairement de vrai processus bloqué.
Les enveloppes de workers ne doivent jamais être appliquées au processus pytest.

À revoir particulièrement : erreurs pendant staging/clôture, répétition de
signaux, disponibilité Linux du subreaper, bornes de collecte, budget cumulé,
liaisons et relations dérivées natives, et interprétation de « sans TMM » :
absence d'appel garantie par conception, absence d'import passif non promise.

## Retouches de revue de la proposition (non intégrées)

La relecture conserve les témoins natifs récupérables mais refuse une clôture
complète si result.json, comparison.csv, summary.txt ou manifest.json manque.
Les champs delivery_files_complete/missing_delivery_files décrivent cette
complétude matérielle, séparément de la présence du marqueur terminal.
Chaque module catalogue capture son empreinte au chargement et la confronte
aux octets présents avant le préflight et avant la clôture. Les chemins des
objets de fonctions importés sont contrôlés ; ces empreintes ne sont pas une
authentification extérieure ou une garantie contre une réécriture totale.

Le lecteur natif R54 importe déjà pipeline.tolerance_audit pour geometries et
fixed_contract. Le raccord réutilise cette dépendance géométrique existante,
sans importer les producteurs catalogue/adjustment_policy, ni appeler les
fonctions de calcul, recherche, maillage ou TMM de tolerance_audit. L'absence
d'import de tout module contenant aussi un producteur n'est pas promise.
Ce choix technique évite de recopier le lecteur R54 ou de modifier les 295
fichiers gelés ; il ne modifie aucune exigence scientifique ou physique.

L'exemple fixe conserve aussi l'obligation R55 de longueur fabriquée dans
[1,19 ; 1,21] m avec tolérance dimensionnelle 1 micromètre, indépendante de f1.
Un masque de dimensions nominales ne remplace pas cette obligation fabriquée.
Les résultats d'exécution et la provenance pilote de ces retouches figurent
au rapport de revue ; ce texte ne prétend pas être un commit auteur testé.
