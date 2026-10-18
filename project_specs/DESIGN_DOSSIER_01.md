# DESIGN-DOSSIER-01 — dossier de points acquis

Le produit rapproche des candidats **fixes**, sans recherche ni nouvelle acquisition.
Chaque candidat sélectionne un scénario/projection `fixed` d'un `tolerance_audit`, un
`geometry_state` et 1 à 8 expériences `register_target` de type `saved_state`.
Il n'établit ni optimum, ni adoption, ni fabrication, ni robustesse continue, ni
validation physique. La fidélité finie et le certificat de passivité gardent leurs
portées natives distinctes. JOINT-DIRECTIONS/FIT-ADAPT restent suspendus.

## JOB et API

Voir [l'exemple réutilisable](examples/design_dossier/job.json). Les répertoires
`proofs/` sont des emplacements à renseigner avec des preuves **déjà produites** ;
l'exemple ne prétend pas inclure ces preuves. Tous les chemins du JOB sont relatifs
au fichier JOB (les `..` sont autorisés, les liens symboliques sont refusés).
Aucune commande, import, expression Python ou option de compatibilité historique
n'est acceptée. Les clés inconnues, clés/identifiants répétés, nombres non finis,
booléens numériques, cardinalités et budgets hors domaine sont refusés.

```python
from didgeridoo_optimizer.pipeline.design_dossier import preflight, run
from didgeridoo_optimizer.reporting.design_dossier import read_result
plan = preflight("job.json", output="nouveau-dossier")
result = run("job.json", "nouveau-dossier", dry_run=False)
checked = read_result("nouveau-dossier")
```

```bash
python -B -m tools.design_dossier --job job.json --output-dir nouveau-dossier --dry-run
python -B -m tools.design_dossier --job job.json --output-dir nouveau-dossier
python -B -m tools.design_dossier --read-result nouveau-dossier
```

Les modes sont exclusifs. Code 0 : admission dry-run ou lecture/création complète ;
code 1 : refus ou partiel, distinct d'une violation scientifique. Un dossier complet
peut contenir des obligations violées. La destination doit être nouvelle, avec un
parent existant. Aucun écrasement. La vraie entrée CLI vérifie son chemin et ses octets.

Le preflight ne lit que des fichiers bornés et leurs métadonnées, sans tableau
numérique, diagonalisation, lecteur natif complet ni création de cache/destination
(avec `python -B`). Les contradictions d'association connues sont rejetées avant
les lecteurs. Un refus d'admission ne crée pas de dossier ; la CLI renvoie sa raison
JSON. Les échecs après admission conservent les réponses, erreurs et reçus terminaux.

## Budgets et processus

Le JOB porte explicitement tous les budgets : `experiments` (1..64), `native_reads`
(1..80), `input_files` (1..4096), `input_mib` (1..512), `child_seconds` (1..175),
`total_seconds` (1..550), `closure_seconds` (5..20), `memory_mib` (128..768),
`file_mib` (1..8), `output_mib` (4..160), `blas_threads` (exactement 1).
Il contient 1..8 candidats et 1..8 expériences par candidat. Les produits de
cardinalité, les fichiers liés, les représentations et la réserve de sortie sont
bornés avant destination ; un budget insuffisant est refusé. Le budget global de
lecture est distinct des délais par enfant et conserve la réserve de clôture.
La relecture admet un `total_seconds` optionnel inférieur au budget original.

Les trois fonctions autoritatives `reporting.tolerance_audit.read_result`,
`reporting.geometry_state.read_result` et `reporting.register_target.read_result`
s'exécutent dans des interpréteurs **neufs et sériels**. BLAS1 est établi avant
NumPy ; les limites mémoire, CPU et fichiers sont installées uniquement dans
l'enfant. L'hôte API conserve ses limites, timers et handlers. Les annulations
Python collectent l'enfant ; la vraie CLI traduit SIGTERM en annulation. Linux
ajoute une terminaison de l'enfant si son parent disparaît. Les reçus indiquent
PID, code réel, délai, limites, flux et collecte. Aucun processus permanent.

Le cache est strictement local à une exécution : une même paire type/répertoire
résolu n'est lue qu'une fois. Les copies indépendantes ne sont pas fusionnées par
nom ou par ressemblance. Les octets sources sont vérifiés aux frontières.

## Association et verdicts

Le profil vient du fichier natif exporté de la projection passive. Ses octets
sont comparés au DESIGN géométrique et au cas joué. Les copies octet-identiques
CONFIG/DESIGN sont admises par leurs hashes. CONFIG, matériaux, règles, modèles
effectifs, rayon, recette native projetée, preuve de fidélité, source scalaire,
hash du fichier modèle et hash de ses paramètres sont associés explicitement.
Les trois lecteurs conservent leur propre validation, sans réimplémentation ni
contournement. Les demandes originales sont exportées avec toutes leurs obligations.

La conjonction hard conserve les violations prouvées, même dans un dossier partiel.
Sans violation, une obligation inconnue, non supportée ou une lecture incomplète
interdit la conformité. Une observation `observe`, notamment f1, peut être violée
ou inconnue sans annuler des hard satisfaits. Statut hard, complétude, association
et couverture des observations sont distincts. Les observations des scénarios
passifs non sélectionnés restent visibles, hors conjonction des candidats.
Aucun absent n'est converti en zéro. Les marges jouées utilisent l'arithmétique
native `played_search.played_rows`, sans lancer de recherche, et leurs unités
explicites ; les marges passives sont conservées telles que relues.

`same_contract` compare les demandes intégrales, critères, rôles, unités, recettes
et budgets. `same_playing_protocol` compare lèvres, pressions, fenêtres, observateur,
état initial et autres paramètres du protocole ; seules les identités géométriques
comparées et les critères/budgets déjà inclus au contrat sont séparés. Une différence
de budget reste visible et interdit le delta contractuel. Les expériences sont
appariées par leurs identifiants explicites. Un delta b−a n'apparaît que si contrats,
protocoles et contextes concordent, lectures complètes et valeurs/unités disponibles.
Il reste descriptif : les modèles scalaires associés aux géométries peuvent différer.
Aucun meilleur candidat, classement ou score compensatoire n'est calculé.

## Artefacts et relecture

`result.json` contient les candidats, demandes, valeurs, marges, verdicts,
comparaisons motivées, observations de l'audit partagé et identités d'acquisition.
`plan.json` et `preflight.json` pinnenent entrées, sources, artefacts, ancêtres de
checkpoints et séries. Les profils sont copiés octet pour octet. `geometry.csv`
décrit chaque section physique (longueur et diamètres en cm), sans discrétisation.
`criteria.csv`, `comparison.csv` et `summary.txt` rendent le dossier en français.
Les réponses natives et stderr sont conservés ; manifeste, exécution, clôture et
reçu terminal sont liés par hashes. Un marqueur terminal signifie clôture ; son `ok` et le reçu préparatoire doivent
concorder (voir les champs v2 ci-dessous). Aucun timeout n'est un succès.

La relecture vérifie les reçus, les sources pinnées, relit les preuves par les mêmes
lecteurs neufs, recompose les obligations/comparaisons et compare les vues. Une
simple modification suivie d'un rehash du dossier ne suffit donc pas à falsifier
ses valeurs ou demandes. Les sources originales restent nécessaires : ce dossier
n'est **pas autonome**. Les hashes et la cohérence interne ne prétendent pas
authentifier un ensemble de preuves intégralement réécrit. SHA producteur,
acquisition géométrique, SHA lecteur et date de création sont conservés séparément.
La provenance du lecteur courant peut changer après un commit, sans changer les
identités d'acquisition ni les sources requises.

Les tests portables utilisent des stubs documentaires explicitement synthétiques
pour les associations et doubles de lecteurs, un audit natif purement géométrique
pour un vrai enfant, et de vraies CLI pour admission/refus/relecture partielle.
Ils ne fabriquent pas de preuve instrumentale, certificat, conversion, fit ou
trajectoire. La démonstration complète sur preuves acquises reste séparée.

## Correction C1/C2 : délais, reçus v2 et code chargé

Le JOB reste en v1 et l'exemple public reste inchangé. Les nouveaux dossiers,
manifestes et marqueurs terminaux utilisent `dcalc.design_dossier.v2` ; les reçus
utilisent `dcalc.design_dossier.child.v2` et `dcalc.design_dossier.execution.v2`.
Les archives v1 ne sont ni réparées ni réétiquetées : cette relecture les refuse.

L'échéance d'opération part de l'entrée API et vaut `total_seconds -
closure_seconds`. Elle couvre admission, lecteurs, composition et exports. Les
contrôles précèdent et suivent les frontières utiles, y compris une réponse de
`wait` tardive sans exception. La publication et son contrôle final disposent de
la réserve, sous l'échéance publique `total_seconds`. La relecture contrôle aussi
son retour, après fermeture de son répertoire temporaire. Aucun délai implicite
« durée enfant +5s » n'autorise une opération réussie.

Chaque collecte dispose d'une seule échéance cumulative d'au plus 4s, bornée aussi
par l'échéance publique. TERM puis KILL concernent uniquement la session créée ;
tous les `wait` ont un timeout calculé sur le temps restant. Les tentatives sont
finies même sous annulations répétées. `operation_seconds`, `collection_seconds`,
`collection_limit_seconds` et leur somme `seconds` décrivent le processus et la
fermeture de ses descripteurs. `reaped` exige un code de retour confirmé ;
`collection_confirmed` exige également une fermeture confirmée dans le délai.
Une erreur de lancement garde `launched=false`, PID/code nuls et la cause. Un
échec de collecte garde les faits observés, sans prétendre que l'enfant a disparu.
Les limites, timers et handlers de l'hôte API ne sont pas modifiés.

Ces bornes sont supervisées et contrôlées aux frontières : elles ne peuvent pas
préempter un appel système de l'hôte bloqué dans le noyau ni un double Python qui
ignore son timeout. Un tel retour tardif est refusé ; il ne produit aucune autorité
de succès. Les délais demandés et les durées réellement constatées restent séparés.

Le reçu d'exécution porte `candidate_complete` et `status=ready|partial` : il
n'est pas une autorité de succès. Seul le marqueur final lié porte `ok`. En cas de
timeout, annulation ou échec tardif, l'appel retire uniquement son propre nouveau
marqueur `execution.completed.json` et conserve `execution.failed.json`, les
causes et les reçus disponibles. Si le retrait échoue, le marqueur d'échec
invalide explicitement cette publication. Aucune relecture ne réécrit un dossier.
La relecture refuse toute fausse clôture, les booléens numériques, les durées
contradictoires (y compris leur somme cumulée) et toute preuve issue d'un timeout,
d'un code non nul ou d'une collecte non confirmée. Un résultat natif partiel mais
relativement vérifié, rendu par un processus confirmé de code zéro, conserve ses
violations et reste lisible.

Les modules possédés capturent leurs octets au chargement, avant admission. Leur
identité n'est jamais recalculée pour accepter une mutation. Les fonctions natives
`played_rows` et `state_recipe`, leurs dépendances scalaires et constantes sont
contrôlées explicitement contre le code compilé des fichiers épinglés ; les
captures natives existantes sont également contrôlées. La compilation seule
n'exécute aucun calcul, ajusteur ou lecteur. Un module déjà chargé avec d'anciens
codes ou constantes est refusé avant admission. Les vrais lecteurs restent en
processus neufs ; les substitutions pytest de lecteur restent des doubles
synthétiques, sans valeur de provenance instrumentale.

Les 60 cas auteur sont conservés avec leurs assertions. Seuls leurs helpers de
reçus synthétiques et de rehash jetable sont adaptés mécaniquement aux champs v2
(durées séparées, lancement/collecte, `candidate_complete`, `ok` terminal et durée
publique). Les tests ajoutés couvrent horloge/enfant simulés, délais cumulés,
seconde expiration, retour tardif, lancement/fermeture refusés, annulations
répétées, provenance après import et falsification malgré rehash. Les petits tests
réels d'annulation/timeout et le lecteur natif sans acoustique restent distincts
de toute démonstration instrumentale R64/R66.
