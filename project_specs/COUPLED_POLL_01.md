# COUPLED-POLL-01 — sécantes géométriques issues des départs

Extension **opt-in** du poll natif PLAYED-SEARCH :

```yaml
search:
  coupled_directions: start_secants_v1
  normalized_steps: [0.125]
  directions: [1, -1]
  stop_on_witness: false
  starts:
    include_initial: false
    mode: poll
    profiles:
      - {id: short_wide, design: ../seeded_search/short_wide.json}
      - {id: long_wide, design: ../seeded_search/long_wide.json}
```

Le champ exige exactement la chaîne `start_secants_v1`, un input `fixed`, des
`search.starts` explicites et `mode: poll`. Types, valeurs et autres combinaisons
sont refusés avant science par l'entrée publique `run`. Hors session et en
dry-run, le refus ne crée aucune destination ; une session réelle peut laisser
ses journaux administratifs de refus nonreprenable. Un seul départ unique, des départs identiques ou
uniquement alignés sur une coordonnée ne fournissent aucune direction couplée :
le précontrôle refuse explicitement, sans direction de secours.

Sans ce champ, stratégie, champs JSON et CSV historiques restent identiques.
Aucun paramètre scientifique, cible, règle passive→jouée, critère, coefficient,
modèle ni contrôle joueur n'est ajouté ou ajusté.

## Construction déterministe

`optimization.played_search.build_coupled_directions(variables, starts)` utilise
seulement `starts.selected`, dans l'ordre admis. L'initial participe seulement
lorsque `include_initial` le sélectionne. La liste native des départs reste
intacte ; seuls les doublons numériques exacts sont retirés pour construire les
directions. Les identités documentaires restent celles du premier profil.

Pour chaque paire `i < j`, dans cet ordre, on calcule pour chaque variable :

```
t_i[k] = (x_i[k] - low[k]) / (high[k] - low[k])
d[k] = t_j[k] - t_i[k]
d[k] /= max(abs(d))
```

Les valeurs et bornes doivent être des nombres finis, hors booléens/chaînes ;
les largeurs doivent être finies et strictement positives. Les bornes entières
sont admises. Un vecteur nul ou à une seule composante non nulle est ignoré.
Le premier coefficient non nul est orienté positivement et les zéros signés
normalisés en `0.0`. La première occurrence de chaque vecteur exactement égal
est conservée. Pas de tolérance, arrondi, SVD, coût acoustique ni hypothèse de
conformité héritée des parents. Au plus neuf départs sélectionnés donnent 36
paires avant élimination.

Le plan porte `coupled_directions` : `method`, `variable_order`, puis `directions`
contenant chacune `id` (`secant-001`, etc.), `source_pair` (identifiants des deux
starts) et `components_normalized`. Les profils, sources et représentations
natives complets restent dans le registre `starts` existant. Le plan est
reconstruit depuis les entrées originales ; un registre réempreinté falsifié
reste refusé.

## Ordonnancement et preuve

Les départs sont tous évalués selon R68 avant les polls. À chaque pas normalisé
de chaque départ, le centre est figé. Les sécantes sont proposées dans l'ordre
du registre, chacune suivant `search.directions`, puis viennent toutes les
directions coordonnées historiques depuis **la même ancre** :

```
x'[k] = anchor[k] + sign * step * width[k] * d[k]
```

Une coordonnée hors bornes refuse la proposition entière. Aucun clamp, réparation
ou recentrage implicite. Le meilleur candidat est choisi par `better` natif,
identique ; l'ancre ne change qu'à la fin du pas. Déduplication, compteur de
candidats, budgets de science, checkpoints et `stop_on_witness` restent communs.
Chaque nouveau point traverse l'évaluateur natif ; s'il atteint le jeu, le chemin
natif acquiert son modèle propre selon les identités exactes existantes.

Le stage est `coupled_poll`. Candidats et décisions conservent un objet
`coupled_direction` : `id`, `source_pair`, `sign`, `step`. Ce champ est indépendant
de `start_origin`, qui continue à nommer le départ dont le poll est actif. Les
refus de bornes et doublons conservent aussi cette identité. L'identité numérique
du poll reste distincte de l'encodage JSON contrôlé par empreinte.

`result.json` conserve le registre via son plan et les décisions via `search`.
En opt-in seulement, `directions.csv` exporte une ligne par composante : méthode,
identifiant, paire source, index et nom de variable, composante normalisée.
`candidates.csv` ajoute `coupled_direction`, sérialisé en JSON à clés triées.
Le lecteur compare les octets de ces tables aux tables reconstruites comme pour
les CSV seeded existants. La synthèse nomme `start_secants_v1`.

## Précontrôle, sessions et compatibilité

Le précontrôle montre le registre réel sans TMM, fit, pas temporel ni destination.
La CLI existante lit le nouveau champ du JOB : aucun nouveau drapeau n'est requis.
En dry-run, y compris avec `session=True`, le refus reste sans destination.
Une session réelle suit l'admission R72 : invocation journalisée sous deadline,
vérification de provenance, puis `load_inputs` et construction des directions
dans le budget actif. Un refus à ce stade peut laisser `invocation-00001.json`
et `failure-00001.json`, avec `resumable=False`, sans candidat, TMM, fit, pas ni
boundary. Les erreurs interceptées par le bootstrap avant destination gardent
leur sémantique. La reprise conserve également son admission R72.

Le brouillon initial ajoutait dans `run` un préflight complet avant `run_session`,
hors journal, deadline et temps facturé. La correction C1 retire cette prélecture
et restaure `run` exactement à la base R72, sans modifier `run_session`, `Budget`,
compteurs ou timers. Cette précision remplace uniquement la promesse de zéro
effet administratif pour une session réelle ; aucun budget, critère ou seuil
n'est augmenté. Elle ne constitue pas une acceptation finale du patch R73.

Les sessions reconstruisent le même poll, vérifient les métadonnées couplées et
renvoient les lignes du préfixe déjà acquis. Une pause juste après les starts
reprend avant le premier nouveau candidat couplé sans réévaluer ces starts.
Les décisions de bornes/doublons déjà traversées se reconstruisent exactement.
R71 garde ses quotas et compteurs ; aucun lecteur énergétique natif n'est modifié.

Les sources restent épinglées. Les anciennes archives R64/R66/R70/R72 restent
attachées à leurs sources et worktrees conservés. Aucun override de SHA, cache
inter-run ou réattribution de preuve historique n'est introduit.

## Exemple public court

Depuis la racine, avec des destinations nouvelles :

```sh
python -m tools.played_search --job project_specs/examples/coupled_poll/job-filter.yaml \
  --output-dir /tmp/coupled-plan --dry-run
python -m tools.played_search --job project_specs/examples/coupled_poll/job-filter.yaml \
  --output-dir /tmp/coupled-filter
python -m tools.played_search --read-result /tmp/coupled-filter

python -m tools.played_search --job project_specs/examples/coupled_poll/job-filter.yaml \
  --output-dir /tmp/coupled-session --session --pause-after 2
python -m tools.played_search --job project_specs/examples/coupled_poll/job-filter.yaml \
  --output-dir /tmp/coupled-session --resume /tmp/coupled-session/session-00001.json
```

Le JOB référence les exemples existants par chemins relatifs. Il évalue deux
starts et une proposition couplée nouvelle, avec budget commun de trois candidats.
Les trois longueurs dépassent la borne hard de 92 cm : chaque acquisition
statique native prouve une violation, avec **zéro TMM, fit et pas joué**. Le code
CLI 0 indique une publication complète, sans témoin conforme ; 3 indique la
pause confirmée. Aucune acquisition lourde n'est livrée ou lancée par défaut.

## Portée des tests

Le contre-exemple **synthétique** conserve les mêmes observations, critères,
budget de 16 candidats et verdict/ordonnanceur natifs dans les deux stratégies :
corridor hard `x=y`, cible hard `x+y=1.5`, starts `(0.25,0.25)` et `(0.5,0.5)`.
Avec les pas `0.5,0.25`, les axes seuls ne trouvent pas le témoin ; la première
proposition couplée atteint `(0.75,0.75)`, candidat 3. Ce gain illustre une
limitation géométrique du poll coordonné ; il ne prouve aucune note ou solution
physique. Aucun candidat n'hérite de la conformité de ses parents.

Les tests purs couvrent normalisation, orientation, unités, ordre, doublons,
initial, types, absence de directions, bornes, plusieurs pas, budget et pauses.
La parité sans option compare directement les fonctions du commit de base
`08f06a2b17d9d15063b7e60c79285fe02f412b7b`, obtenu par `git show`, et non une copie
du nouvel algorithme. Les tests CLI exercent filtrage natif, registres/CSV,
relecture sans science, altérations réempreintées et reprise en processus neuf.
Les fixtures et assertions historiques restent intactes. Les résultats exécutés,
codes, XML, durées et empreintes sont dans les remises privées auteur et C1.
Les tests C1 vérifient la construction après journal sous timer actif, le refus
réel sans direction avec compteurs nuls et entrées intactes, et l'identité de
`run`, `run_session` et `Budget` à la base. Les passes antérieures sur d'autres
octets ne valent pas validation des sources corrigées ; aucune
intégration n'est déclarée par ce document.
