# SEARCH-RESUME-01 — frontières explicites de candidats

État C2 au 19 octobre 2026 : correction ciblée du nouveau test poll ; aucune
modification de production. Validation finale sur commit exact et publication
restent au pilote ; aucune intégration n'est déclarée.

La fixture poll copie les entrées de test avant toute exécution : seules les
obligations géométriques restent hard (longueur demandée 88–89 cm, domaine
91–95 cm), les critères joués deviennent observe. Les profils explicites,
y compris le troisième départ et le doublon, sont préparés et empreintés dans
fixture-prepared.json avant le premier processus ; leurs octets restent identiques
jusqu'à la fin. Ce choix appartient seulement à ce nouveau test et ne modifie
aucune demande R49/R70 ni aucun critère produit. Les huit candidats (trois départs
uniques et cinq voisins), les pas .5/.25 et directions +1/−1 exercent réellement
les bornes, le doublon, plusieurs select et l'ancre déplacée de .93 à .91 m.
Les assertions de parité exacte entre sans session, session entière et deux pauses
sont conservées ; le déplacement d'ancre est désormais vérifié explicitement.
Le filtrage statique et les lecteurs sont natifs ; le routage API reste le double
de test annoncé. Zéro fit, tentative de fit, trajectoire ou pas acquis/facturé ;
aucune conformité jouée n'est revendiquée. Les variantes synthétiques pures restent
complémentaires, elles ne valident pas à elles seules le produit.

Les diagnostics T25 conservés portent sur ccab14e6f2d5ce45d77c9811fd3d14ea453b7715.
À 17:36:58 UTC, le test temporel natif passe (198,11 s pytest / 198,7748 s
superviseur) : deux candidats, deux scénarios par candidat, un fit partagé par
candidat, 14/15 enfants, 320 pas cumulés, et seulement 160 nouveaux pas du candidat 2
à la reprise en processus neuf. Séries, états, bilans et critères sont identiques ;
les archives ne sont pas réécrites. Routage/fit de test synthétiques, trajectoires,
couplages, checkpoints et lecteurs natifs : aucune preuve de fit d'instrument.
Les fonctions de cette preuve et ses dépendances sont inchangées en C2 ; ce test
n'est pas relancé, conformément à FINAL_DIAGNOSTIC_T25.md.

Le passage agrégé du 19 octobre 14:32–14:47 UTC reste interrompu après plus de
900 s, sans XML final : E historique, F poll et trois derniers verdicts non acquis.
E passe isolément dans T25 (15,7238 s superviseur), mais sa cause agrégée reste
inconnue. Les assertions et fixtures historiques restent intactes. Ni les marqueurs
de ce passage ni les 1175 interruptions synthétiques initiales ne constituent une
validation du produit. Les logs/XML/reçus C2, y compris son premier échec de nouvelle
assertion sur un compteur optionnel absent à zéro, restent dans la remise privée
corrector2 ; aucune preuve antérieure n'est effacée ni réattribuée aux octets C2.

Preuve C2 acquise le 19 octobre 2026 : poll ciblé, puis groupe evaluate_only,
refus CLI, interruption historique et plafond natif de lecteur : cinq tests uniques
ont passé leurs assertions sur les mêmes octets sources, avec XML authentiques.
Le groupe de quatre termine toutefois en code 3 : son journal privé utilisait
Path.open, temporairement remplacé par le test historique du plafond. Le journal
est corrigé sans toucher au test ; plafond et interruption historique repassent
ensuite en groupe isolé code 0, enfants collectés. Ce groupe code 3 reste un incident
de supervision, jamais une exécution entièrement validée. Poll : 233,56 s pytest / 234,2938 s
superviseur ; détail et durées du second groupe dans corrector2/REMISE.md.
Cette sélection ciblée ne remplace pas les huit suites finales du pilote.

Le protocole ci-dessous décrit l'interface implémentée. Les résultats d'acceptation,
les empreintes exactes et les limites de preuve sont consignés dans les remises C1/C2 et les reçus T25.
Les tests de continuation distinguent le filtrage statique natif, le routage de
test en processus neuf et les trajectoires temporelles natives sur la fixture
`synthétique_bundle` existante (`synthetic_bundle` dans le code). Cette fixture
conserve ses cibles et reçus synthétiques : elle ne prouve aucun fit d'instrument.

Extension opt-in pour les **nouvelles** recherches `fixed`, avec `search.starts`
explicite (`evaluate_only` ou `poll`), backend `certified_scalar_v1` et scénarios
`saved_state`. Les paramètres scientifiques restent dans le JOB original.
Les voies historiques, assemblages et modèles modaux ne deviennent pas reprenables.
Aucune archive ni ancien checkpoint témoin n'est importé.

```python
from didgeridoo_optimizer.pipeline.played_search import run
from didgeridoo_optimizer.reporting.played_search import read_session

paused = run(job, destination, session=True, pause_after=1)
continued = run(job, destination, resume=paused['resume_point'], pause_after=1)
finished = run(job, destination, resume=continued['resume_point'])
verified = read_session(destination)  # lecteur natif synchrone à superviser
```

```sh
python -m tools.played_search --job JOB --output-dir SESSION --session --pause-after 1
python -m tools.played_search --job JOB --output-dir SESSION --resume SESSION/session-00001.json --pause-after 1
python -m tools.played_search --job JOB --output-dir SESSION --resume SESSION/session-00002.json
python -m tools.played_search --read-result SESSION
```

`session=True` / `--session` crée une destination nouvelle. `resume` / `--resume`
nomme le **dernier point exact**, dans le même dossier ; il est exclusif de
`session`. `pause_after` / `--pause-after` est un entier strict de 1 à 16, distinct
du budget cumulatif de candidats. Booléens, flottants et chaînes numériques ne
sont pas des entiers API. Sans quota, l'invocation poursuit jusqu'à l'arrêt natif.
La pause n'existe que lorsqu'un autre candidat neuf serait évalué. Un dernier
candidat, un témoin avec `stop_on_witness`, une recherche descriptive terminée ou
un budget de candidats épuisé ne sont donc pas remplacés par une pause.

Codes CLI : **3** = pause confirmée, **0** = résultat final complet (ou précontrôle
réussi), **1** = partiel/non confirmé, **2** = refus/exception. La conformité est
le champ `conforming`, indépendamment du code 0 ou 3. `resumable` distingue une
pause valide d'une publication finale. Un résultat final n'est jamais rouvert.

## Dossier et frontières

Un dossier unique conserve les fichiers natifs, leurs chemins, leurs octets et
leurs producteurs. Aucun gros bundle n'est copié pour reprendre. `plan.json`,
`session.json` et les profils exportés sont créés une fois. Chaque invocation
ajoute `invocation-NNNNN.json`, puis un point `session-NNNNN.json` et son marqueur
`boundary-NNNNN.json`. Ce dernier est publié après récolte des enfants, fermeture
des flux et restauration des signaux. Le verrou de dossier est un `flock` noyau,
libéré à la fin de l'invocation ou du processus ; aucun daemon ni verrou disque
persistant n'est créé.

Les points contiennent le plan original, le préfixe, les décisions, meilleur et
témoin, les coûts, reçus d'enfants, associations du cache et acquisitions,
l'ascendance, la génération et l'inventaire exact des fichiers et répertoires.
Les aliases `.pending` natifs restent présents et comptés. Les vues finales
`result.json`, CSV, synthèse et clôture ne sont publiées qu'une fois, à la fin.
Une pause n'écrit aucun ancien résultat ni ancien marqueur de completion.

Une invocation commencée sans frontière confirmée est **non reprenable**, même
si un point antérieur existe. Cela comprend SIGKILL, candidat actif, enfant non
récolté, erreur de lecture, trajectoire partielle, publication inachevée et coût
inconnu. Aucun retour silencieux en arrière, nettoyage, complément ou retry n'est
fait. Un candidat complètement archivé avec violation statique prouvée est acquis.

## Reconstruction et lecteurs

Le poll natif est utilisé sans changement. À chaque appel correspondant au
préfixe, l'identité, le numéro, le vecteur, l'alternative, le stage et l'origine
sont comparés exactement avant de retourner la ligne complète déjà acquise.
Ainsi seen, ancres, choix, directions, doublons et meilleur/témoin se reconstruisent
par l'ordonnancement déterministe, sans calcul statique, fit ni pas temporel.

L'admission vérifie les sources originales, le plan reconstruit, la chaîne des
points, les inventaires, décisions et compteurs. Une relecture native complète
du préfixe s'exécute dans un enfant frais sous les limites existantes, avant tout
nouveau candidat. Un fit partagé entre scénarios possède un seul reçu de lecteur
de fit ; les lecteurs de trajectoire et d'admission restent distincts. Les
lecteurs doivent correspondre bijectivement aux acquisitions et les générations
d'admission doivent être successives. Ses reçus et coûts sont conservés séparément. Chaque unité
utilise le lecteur terminal natif et les contrôles légers des segments antérieurs,
avec ou sans `steps_per_call`. Les résumés ne remplacent pas les preuves natives.

`--dry-run --resume POINT` inspecte métadonnées et empreintes sans modèle, TMM,
fit, pas, destination nouvelle ni modification de session. Il ne prétend pas
avoir exécuté la relecture sémantique native. Une lecture confirmée via
`read_session` ou `--read-result` effectue celle-ci ; l'API synchrone exige que
l'appelant fournisse une enveloppe de ressources adaptée.

Un point obsolète, une copie déplacée, un lien symbolique, un fichier non régulier,
un enregistrement JSON au-delà de 4 Mio, un fichier au-delà de 8 Mio, un nombre
non fini, une acquisition ultérieure non comptabilisée ou un autre écrivain sont
refusés. Les empreintes prouvent l'intégrité et les associations disponibles,
pas une signature d'auteur ni une résistance à la réécriture intégrale de tout
l'historique par un adversaire disposant des mêmes droits.

## Dépenses et provenance

Tous les compteurs sont cumulés : candidats, fits/fit_attempts, appels temporels,
pas réservés/facturés/acquis, enfants, lecteurs et stockage du dossier entier.
Les réservations échouées ne sont pas remboursées. La relecture d'admission est
un vrai enfant lecteur ; elle doit tenir dans le budget restant, y compris la
réserve de clôture. Il n'y a aucun nouveau quota de calcul par invocation.

Le temps utilisateur entre invocations est exclu. Le temps actif commence avant
l'admission et couvre inspection, relecture, reconstruction et acquisition.
Chaque invocation facture en plus **les 15 s complètes de réserve de clôture**,
non remboursables : `elapsed_seconds` est donc une borne conservative facturée,
non une mesure exacte du seul temps CPU ou de la seule science. Les points
séparent `active_seconds`, `charged_seconds`, `closure_reserved_seconds` et
les durées des phases, notamment `reconstruction_seconds`. Cette convention peut
épuiser le budget plus tôt avec de nombreuses pauses ; elle n'accorde jamais du
temps supplémentaire. Les plafonds restent 175 s par lecteur scalar, 768 Mio,
BLAS1, logs 8 Mio, records 4 Mio et réserve 15 s.

Le bootstrap borne les fichiers, le nombre de générations et les types/plafonds
scalaires avant toute admission complète. Il recoupe les dépenses au JOB original.
Le journal est écrit sous délai avant provenance complète et inventaire ; une
écriture échouée demeure visible et ne rouvre pas un point antérieur. Les sources
chargées à l'import sont vérifiées, sans qualifier de nouveaux octets par simple
rafraîchissement d'empreinte.

Chaque invocation conserve date UTC et provenance propre. Le plan et les preuves
scientifiques gardent leur producteur initial. Un commit de merge ayant les mêmes
octets sources est admissible ; des scripts différents ne le sont pas. Le code
reste dans les modules `played_search` déjà inventoriés et suivis par Git ; aucun
contournement de provenance ou étiquette de compatibilité n'est ajouté.

Le mot « global » désigne seulement l'ordonnanceur de recherche. Cette extension
ne prouve aucun optimum, ne change ni critères ni stratégie, et ne constitue
aucune validation matérielle A–E ou physiologique.
