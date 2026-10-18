# PEAK-PLATEAU-01 — plateaux exacts du raffinement local

Base : `07b00cda1de260ac366062f0e56fa9af2f7180a7` (R68).

Dans `extract_peaks`, deux comparaisons strictes comptaient zéro maximum
pour un sommet constitué de plusieurs échantillons exactement égaux.
La parabole synthétique positive `10-(f-97/64)^2`, sur `[0,4]` avec un
pas initial de `0.5`, expose ce défaut : la première grille locale place
deux valeurs égales aux indices 16/17, de part et d'autre du maximum unique.
Ce contre-exemple est analytique ; les traces R69 sont numériques, pas des mesures.

La correction traite seulement les lignes actives du raffinement qui ont
des égalités adjacentes exactes. Elle compte les groupes contigus constants
strictement encadrés par des valeurs inférieures, y compris les maxima
secondaires. Un groupe vaut un maximum échantillonné. Le nouvel encadrement
conserve tout le groupe du maximum global et ses deux voisins disponibles.
Un groupe touchant un bord reste ambigu. Le drapeau d'ambiguïté est cumulatif :
une contraction ultérieure ne le remet jamais à zéro.

Un plateau échantillonné ne prouve pas l'unicité continue. Les véritables
sommets plats conservent leur intervalle et son incertitude ; la courbure
peut les laisser non résolus. Les 12 itérations, 33 points, budgets, plancher
relatif `1e-12`, test de courbure `1e-10`, `refinement_hz`, contrôles de modes
et de convergence restent identiques. Aucune égalité approximative ajoutée.
Les lignes sans plateau pertinent gardent leur calcul antérieur ; six
fonctions synthétiques vérifient l'égalité exacte des résultats et des grilles
avec l'ancienne fonction extraite de Git, plus un cas vectorisé mixte.
Une ligne corrigée peut nécessiter une grille vectorisée supplémentaire :
le contrôle de contraste existant peut alors marquer `floating_point_limited`
sur une autre ligne déjà inactive, sans déplacer son intervalle ni modifier
ses valeurs ou son statut. Ce cas est testé explicitement ; le contrôle
de contraste et l'échantillonnage commun n'ont pas été modifiés pour le masquer.

La découverte initiale par `acoustics.peaks.find_peaks` est inchangée : un
plateau déjà présent sur la grille grossière n'est toujours pas découvert.
Cette correction porte sur les bassins déjà détectés, comme le défaut R69.
Elle ne change ni l'identité des modes, ni les modèles physiques, ni les
critères R49/R64/R66, ni l'observateur joué. Les intervalles restent des
estimations échantillonnées, pas des bornes mathématiques uniformes.

Validation portable, depuis la racine du dépôt (Python avec NumPy et pytest) :

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
python -m pytest \
  didgeridoo_optimizer/tests/test_peak_plateau.py \
  didgeridoo_optimizer/tests/test_constrained_design.py \
  didgeridoo_optimizer/tests/test_design_contract.py \
  didgeridoo_optimizer/tests/test_assembly_path.py \
  -vv -ra -p no:cacheprovider --junitxml=peak-plateau.xml
```

Pour R69, les commandes effectives bornées, journaux, XML et empreintes sont
dans le dossier privé `R69-STATIC-SEEDED-20261018/author`, avec `REMISE.md`.
La vérification statique autorisée utilise exclusivement `Contract.generate`
et `Evaluator.verify` au vecteur SI sauvegardé de `search.best`, avec contrôles
indépendants des verrous et critères. Le résultat initial non résolu reste
intact et archivé séparément ; la nouvelle preuve identifie les nouvelles
sources. Aucune recherche R69, acquisition, étape jouée, promotion ou fit.
