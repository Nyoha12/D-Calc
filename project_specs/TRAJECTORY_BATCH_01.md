# TRAJECTORY-BATCH-01 — appels temporels explicites

`scenarios[].steps_per_call` est facultatif et strictement réservé au backend
`certified_scalar_v1` avec template `saved_state`. Il désigne un nombre de pas
par appel REGISTER-TARGET, sans changer le template scientifique.

La valeur doit être un entier strict, positif, au moins `chunk_steps`, multiple
de `chunk_steps`, et au plus `nonlinear.register_target.MAX_STEPS` (72 000).
Booléens, flottants même entiers, valeurs non finies et backend modal sont refusés
avant science ou création de destination. Une déclaration supérieure au total
est admise : chaque quota vaut exactement `min(steps_per_call, remaining)`.
La dernière portion peut donc être inférieure au quantum, non multiple du chunk.
Il n’y a ni adaptation, clamp de déclaration, ni réécriture du template.

Sans ce champ, le quota historique reste `min(chunk_steps, remaining)` et aucun
champ de regroupement n’est ajouté au JOB ou au résultat. Plusieurs scénarios
peuvent déclarer des valeurs distinctes ; l’absence dans un scénario conserve
son quantum historique. Les workflows fixes, assemblages et départs explicites
conservent leur ordre, leurs critères et leur sélection.

Chaque appel traverse le précontrôle natif `steps_this_run=quota`, puis le worker,
la publication et un lecteur complet frais. REGISTER-TARGET conserve chaque
chunk interne : séries, états, bilans et checkpoints. Les reprises réimportent
les snapshots natifs. Aucun pas n’est approximé. Les checkpoints initiaux propres
aux appels restent présents ; leur nombre dépend du nombre d’appels.

Le budget commun réserve le quota entier avant lancement et le facture dès le
lancement, y compris si l’enfant échoue ou s’arrête partiellement. Les pas acquis,
appels (`trajectories`) et lecteurs (`reader_units`) restent séparés. Aucun
remboursement, retry implicite ou remise à zéro entre scénarios/départs n’existe.
Le plafond enfant de 175 s, la mémoire 768 Mio, BLAS1, le stockage et la réserve
de clôture de 15 s restent inchangés. Un quantum trop grand peut rester partiel ;
les preuves et le motif sont conservés sans allonger le délai.

Le JOB, le plan et leurs empreintes conservent la déclaration. Les segments
conservent quota, accepted_before/after et reçus. En mode explicite, le lecteur
recoupe ces valeurs avec les plans/résultats/checkpoints natifs, les reprises,
les reçus de lancement/lecture et les compteurs communs. Il ne fait aucun TMM,
fit ni nouveau pas temporel. Le lecteur scientifique natif garde ses contrôles.
Une modification réempreintée d’un segment ne remplace pas sa preuve native.

Après acquisition, la vérification d’une unité effectue une seule lecture native
complète de sa dernière chaîne disponible. `verify_candidate` réutilise ce résultat
pour les critères normatifs finaux. Les segments antérieurs gardent leurs contrôles
légers de quotas, continuité, plans/résultats, reçus, checkpoints, provenance,
marqueurs de clôture et manifestes avec intégrité des artefacts. Le lecteur terminal
vérifie toute la série et l’état énergétique sans refaire de pas. Les résultats
intermédiaires restent des traces, sans devenir des verdicts musicaux indépendants.
Les lecteurs producteur restent distincts : un lecteur frais par appel acquis.

Une unité partielle fait lire une seule fois sa dernière preuve native disponible,
même sans reçu de lecteur producteur, sans devenir conforme. Sans résultat, aucun
progrès ni lecture ne sont inventés. Un lancement absent garde sa réservation ;
un lancement échoué ou interrompu garde tout son quota facturé. Les codes de sortie,
pas acquis/facturés et bornes sont des entiers stricts, jamais des booléens ;
`launched` est un booléen exact. Les logs lecteur sont lus sous le plafond natif
de 8 Mio ; un log vide, malformé ou trop grand ne fournit aucun succès.

Les sources restent épinglées : une archive qui référence les anciens octets
`played_search` n’est pas réattribuée aux nouveaux. Aucun override de provenance,
changement d’authentification ou élargissement de la politique historique des
lecteurs n’est ajouté. Les empreintes garantissent une correspondance aux sources
encore disponibles, pas une signature d’auteur.

Les tests courts réutilisent `test_geometry_state.synthetic_bundle` sans modifier
la fixture. Ses cibles et reçus de fit sont synthétiques ; aucun fit d’instrument
n’est réalisé. Les trajectoires, sauvegardes, réimports et lectures REGISTER-TARGET
exécutent le code natif. L’égalité des séries/états/bilans/critères est exigée entre
deux regroupements. Une interruption native contrôlée vérifie acquis < facturé.
Les coûts R70 demeurent des observations historiques : aucun gain de vitesse R70
n’est promis ni mesuré par ces tests, et aucune archive R64/R66/R70 n’est rejouée.
