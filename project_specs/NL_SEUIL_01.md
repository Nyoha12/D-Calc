# NL-SEUIL-01 — diagnostic additionnel d'équilibre et de stabilité

Ce bloc ajoute un diagnostic au modèle labial `dimensioned_v2` et au FIR
existants. Aucun fichier préexistant, coefficient, matériau, parser, score,
classificateur, dépendance ou politique de validation n'est modifié. La base
de développement est `08b478ffb70ff4e7ee0aaf4aaba79cb646f51bd4`.

Les résultats sont numériques et locaux. Ils ne concluent ni à la possibilité
d'un registre joué, ni à un cycle périodique stable, ni à une aptitude du joueur.
Le diagnostic ne produit pas de signal temporel joué ou de FFT de jeu.
Il ne remplace pas A–E et ne promeut aucun matériau.

## Entrées et commande

La commande réutilise `load_fixed_context` : CONFIG existant, DESIGN physique
JSON/YAML, vraie base matériaux et variantes, validation de géométrie et
d'analyse. Aucun second parser CONFIG/DESIGN n'est créé. Les contrôles ajoutés
concernent les paramètres V2, hypothèses et budgets de ce diagnostic.

Exécuter depuis la racine du dépôt, avec l'environnement Python du projet :

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
python -B -m tools.nonlinear_onset_audit \
  --config CONFIG.yaml --design DESIGN.json --output-dir OUTPUT \
  --upstream ideal --parameter-source explicit \
  --pressure-min-pa 0 --pressure-max-pa 5000 \
  --frequency-min-hz 40 --frequency-max-hz 300 \
  --pressure-seeds 5 --frequency-seeds 8 --max-iterations 16 \
  --max-evaluations 6000 --seconds 120 --memory-mib 768 --dry-run
```

Retirer `--dry-run` pour calculer et exporter. Le dry-run ne lance ni acoustique,
ni construction du FIR, ni recherche et n'écrit aucun artefact. Employer `-B`
évite aussi les caches bytecode de l'interpréteur. La sortie est du JSON strict ;
code de retour 0 pour diagnostic terminé/préflight valide, 1 pour refus ou
interruption. Le statut scientifique reste distinct du code de retour.

CONFIG doit sélectionner explicitement `lip_model_type: dimensioned_v2` dans
`nonlinear_simulation`. `--parameter-source explicit` exige tous les champs du
dataclass V2. Exemple de section synthétique, avec le signe existant :

```yaml
nonlinear_simulation:
  enabled: true
  lip_model_type: dimensioned_v2
  mouth_pressure_kpa: 1.5
  resonance_hz: 80.0
  effective_area_m2: 0.000003
  mass_kg: 0.0001
  rest_opening_m: 0.0008
  lip_width_m: 0.012
  damping_ratio: 0.20
  contact_stiffness_n_per_m: 10000.0
  contact_damping_n_s_per_m: 0.04
  min_opening_m: 0.000001
  flow_coefficient: 0.72
  pressure_force_sign: -1.0
  sample_rate_hz: 12000
  resonator_model_type: fir_long_logfit
  resonator_kernel_duration_s: 1.0
```

Ces nombres sont des références synthétiques, pas des mesures/calibrations.
Avec `--parameter-source pipeline_defaults`, les champs omis sont effectivement
choisis par `NonlinearPipeline._default_dimensioned_lip_params` après le calcul
linéaire. L'export sépare `direct`, `pipeline_defaults_chosen` et `effective`.
En dry-run, les champs omis restent `pending_pipeline_defaults` : aucune valeur
dépendant de f0 ou du profil n'est prétendue calculée. Les valeurs V2 sont
`to_calibrate`. L'amont idéal `Zu=0` doit être choisi explicitement.

Le champ `mouth_pressure_kpa` est la valeur d'entrée du pipeline ; chaque
équilibre emploie sa pression en Pa du domaine demandé. Aucun signe nouveau
n'est introduit. Les valeurs en dessous des planchers effectifs V2, NaN/Inf,
booléens numériques, paramètres négatifs incompatibles et clés non reconnues
sont refusés au lieu de subir silencieusement les clips du modèle.

## Équilibres et frontières

Convention SI : `x>0` ouvre ; `h=h0+x`. Le code réel fournit
`K=m*(2*pi*f_l)^2`, `r_l=2*zeta*m*(2*pi*f_l)`, `Au=s_lip*A`, `Ad=-s_lip*A`.
Avec le signe `-1`, `Au=-A` et `Ad=+A` ; cela n'identifie aucune famille
physiologique.

Au repos, `xt=h_min-h0` et `Fbar=Au*Pu_bar+Ad*Pd_bar`. La solution libre est
`x=Fbar/K` ; si `x<xt`, la solution de contact est
`x=(Fbar+Kc*xt)/(K+Kc)`. Le débit original vaut
`Cd*w*max(h,0)*sqrt(2*max(Pu-Pd,0)/rho)`.

Deux fermetures sont conservées : référence imposée `Pd_bar=0` et fermeture
du FIR effectif `Pd_bar=sum(kernel)*Ubar`. Aucune racine n'est sélectionnée
implicitement. Pour le FIR, poser `t=sqrt(Pu-Pd)` et `d=Cd*w*sqrt(2/rho)`.
Sur chaque branche mécanique `h=b+c*t²`, résoudre
`R*d*c*t³+t²+R*d*b*t-Pu=0`, puis vérifier domaine et équations originales non
élevées au carré. La solution à débit nul est examinée séparément.

Toutes les branches admissibles sont exportées avec résidus de force, débit,
fermeture et résidu normalisé. Les racines proches sont regroupées et signalées
comme mal conditionnées : une multiplicité numérique n'établit pas deux
équilibres distincts. Un tel groupe donne `not_resolved` au niveau de
l'énumération, même si son représentant satisfait les équations. L'absence
algébrique, les racines rejetées et les ambiguïtés sont explicites. Les IDs
ordonnent les branches par débit dans chaque région mécanique ; la recherche
refuse une continuation changeant l'inventaire ou la régularité.

`h=h_min` et `h=0` sont deux frontières distinctes. À `DeltaP=0,h>0`, Bernoulli
n'a pas de Jacobien fini. En contact strict, `max(-v,0)` a les dérivées
unilatérales `-1` et `0` en `v=0` lorsque l'amortissement de contact est non nul.
Le diagnostic refuse une tangente centrale arbitraire. La décroissance de
l'énergie mécanique à pressions fixées ne prouve pas la stabilité couplée FIR.
Seuls les équilibres libres réguliers sont linéarisés pour la recherche actuelle.

## Continu et discret

Sur branche libre ouverte avec `DeltaP>0` :

```text
B = Cd*w*sqrt(2*DeltaP/rho)
C = Ubar/(2*DeltaP)
dU = B*dx + C*(dpu-dpd)
Dl(s) = m*s² + r_l*s + K
F = Dl*(1+C*(Zu+Zd)) - B*(Ad*Zd-Au*Zu)
Zu=0 : F = Dl*(1+C*Zd) + s_lip*A*B*Zd
```

La CLI compare trois représentations, étiquetées dans les exports :

1. `sampled_impedance_reference_pd_zero` : fermeture de référence `Pd=0`,
   impédance linéaire complexe interpolée sur la bande réellement calculée ;
2. `continuous_lips_fir_delay_real_axis` : fermeture DC réelle et réponse DTFT
   exacte du FIR, mécanique continue (comparaison avec un opérateur à retards) ;
3. `exact_discrete_fir` : même équilibre DC, pas RK4/sous-pas et FIR effectifs.

Aucune fréquence complexe n'est envoyée au TMM. Les deux premières recherches
restent sur l'axe réel et donnent seulement des candidats marginaux. La
deuxième ne mélange pas une moyenne FIR avec une dynamique TMM différente ;
elle ne reproduit toutefois pas l'ordonnancement discret du simulateur.

Le pas discret maintient `p[n]` figée pendant TOUS les sous-pas RK4, puis calcule
le débit, puis le FIR. Le nombre de sous-pas provient de `integration_substeps`
du modèle réel, qui utilise `K+Kc` même sur branche libre. Le polynôme RK4 de la
matrice mécanique augmentée par une pression constante donne exactement
`y[n+1]=Phi*y[n]+g*p[n]` et `U[n+1]=B*e1T*y[n+1]-C*p[n]`.

Avec `H(z)=sum(h[j]*z^(-j))` et `d(z)=det(z*I-Phi)` :

```text
Fdiscret = z*d(z) + (C*d(z)-B*z*e1T*adj(z*I-Phi)*g)*H(z)
```

La stabilité discrète est `|z|<1`, distincte de `Re(s)<0`. Une matrice dense est
permise uniquement pour témoins FIR de 64 coefficients au plus. La recherche
long FIR évalue directement la caractéristique et ne recense pas toutes ses
racines. La continuation vérifie un centre marginal simple, les résidus, la
branche d'équilibre, le domaine fréquentiel, un voisinage local dans le plan z,
et le changement de signe de `log|z|` à trois distances de chaque côté. Une
`local_crossing_verified` concerne uniquement cette paire locale ; aucune
stabilité globale du FIR n'en découle.

## FIR, recherche et limites de ressources

Le diagnostic du noyau effectif exporte DC, réponse **complexe** DTFT, phase,
partie réelle, erreur complexe et erreur de phase par rapport aux données
linéaires documentées. Hors bande, la cible est absente et le point est marqué
non documenté ; la construction historique extrapole les extrémités du spectre.
`Re(H)<0` réfute la passivité au point testé. Une grille positive ne la certifie
pas. fs, durée, longueur, gain historique et SHA256 des octets float64 little
endian du noyau sont exportés. Aucune correction de DC, moyenne, gain ou
passivité n'est appliquée. `3*f0` est un point de réglage du gain, pas un mode.

Les graines sont les grilles linéaires déterministes pression/fréquence
demandées. Newton borné, différences finies et réduction du pas fournissent des
candidats et une trace par graine. Les résidus normalisés sont exportés. Les
échecs, changements de branche, frontières et budgets épuisés restent visibles.
`no_candidate_in_tested_domain` ne prouve jamais une absence de racine ; si
aucun essai régulier n'est exploitable, le statut est `not_resolved`.

Le budget d'évaluations est partagé entre les trois recherches et la
continuation, sans calcul parallèle ni sous-processus diagnostique. Limites :
180 s et 768 Mio au plus par invocation ; BLAS à un thread ; FIR ≤24000,
grille linéaire ≤16384, maillage ≤2048 et produit maillage×fréquences ≤8 millions.
La CLI applique sous POSIX une limite d'espace d'adressage et une alarme réelle.
L'API Python `run` n'installe pas ces limites de processus : son appelant doit
les fournir, comme le harnais de tests. Les recherches gardent leurs budgets.
Il n'y a ni sweep1320, ni benchmark lourd, ni suite globale, ni replay A–E.

Après préflight valide, `onset_partial.json` est un checkpoint atomique et
`onset_trace.jsonl` conserve les étapes. Timeout, SIGINT/SIGTERM et mémoire
épuisée produisent `not_resolved` et un retour non nul. Une interruption avant
préflight donne seulement une erreur JSON sur stdout ; aucun calcul n'est
annoncé. En cas d'interruption pendant la finalisation, le dernier checkpoint
reste récupérable, sans prétendre qu'un bundle complet a été publié. Le
diagnostic n'arrête aucun processus tiers.

## Exports et provenance

Le bundle final comprend `onset_audit.json` strict (NaN/Inf interdits),
`onset_equilibria.csv`, `onset_response.csv`, `onset_candidates.csv` et
`onset_summary_fr.txt`. Les exports préexistants sont refusés. Chaque document
final est publié avec ses octets complets, par création exclusive, sans
écraser un résultat. Le checkpoint demeure explicitement partiel.

Les empreintes CONFIG/DESIGN/base/variantes viennent du contexte réel. Les
sources Python effectivement chargées et l'entrée CLI sont hachées ; le SHA
Git provient du mécanisme existant, avec état dirty ou indisponibilité explicite.
Les entrées et sources sont revérifiées avant publication finale. Les exports
ne recopient pas les chemins d'entrée ; aucun journal privé n'est destiné à la PR.

Statuts distincts : `equilibrium_solved`, `marginal_candidate`,
`local_crossing_verified`, `not_resolved`, `contact_boundary`,
`non_regular_boundary`, `numerical_model_only`, `no_candidate_in_tested_domain`.
`numerical_model_only` signifie diagnostic numérique terminé, sans conclusion
physique de jeu. Un point non résolu dans le domaine demeure explicitement tel.

## Témoins indépendants et validation native

Références numériques du mandat, pour les paramètres synthétiques ci-dessus :

| Témoin | Référence |
|---|---|
| P1500, Pd0 | K=25.26618726678876 N/m ; h=0.0006218963568787032 m ; U=0.0002682125771076499 m³/s |
| Modal 70 Hz, Q8, pic 1e7 | P=3996.6718916070563 Pa ; f=64.87124624686057 Hz |
| Modal P−10 / P+10 | max Re(s)=−0.0596518928 / +0.0595101696 s⁻¹ |
| FIR modal 8192 points 40..3000 Hz, fs12000, 1 s | gain=0.9938128593443679 ; DC=−1633267.279330331 Pa.s/m³ |
| DTFT FIR à 70 Hz / impédance modale analytique | 0.996085926383 (la FFT interpolée historique donne 0.9952304946643669) |
| Fermeture FIR, P1500 | Pd=−456.69958043 Pa ; h=0.00056766978415439 m |
| Fermeture FIR, P0 | branche nulle et branche U=0.000122061795864883 m³/s, Pd=−199.35953724 Pa |
| FIR court [2e5,1e5], P1500 | Pd=79.4910906959 Pa |

La branche non nulle à P0 est algébrique, liée au DC numérique négatif ; son
existence n'établit ni stabilité ni oscillation sans souffle. Le modal rationnel
est un témoin synthétique indépendant, sans ajustement aux matériaux du dépôt.

Les nouveaux tests confrontent équilibres aux lois originales, matrice modale
à quatre états au polynôme, pas discret aux différences finies mises à l'échelle
du vrai RK4/débit/FIR (4 et 12 kHz), et continuation FIR à un résidu indépendant
obtenu par élimination des états. Les frontières, racines doubles, dérivées
unilatérales, budgets, phase, provenance, exports, refus et vraie CLI/signal
d'interruption sont couverts. Les signaux prescrits 70/réf70, 210/réf70 et
210/réf210 donnent respectivement accepté, refusé, accepté dans le classificateur
historique, sans interprétation des anciens sweeps.

Commande ciblée (caches/temp/XML à placer hors du dépôt selon le harnais local) :

```bash
python -B -m pytest -q -p no:cacheprovider \
  didgeridoo_optimizer/tests/test_onset_stability.py \
  didgeridoo_optimizer/tests/test_nonlinear_onset_cli.py \
  didgeridoo_optimizer/tests/test_lip_model_v2.py \
  didgeridoo_optimizer/tests/test_time_domain_resonator_scaling.py \
  didgeridoo_optimizer/tests/test_fixed_design_input.py \
  didgeridoo_optimizer/tests/test_fixed_design_internal.py \
  didgeridoo_optimizer/tests/test_fixed_design_cli.py \
  didgeridoo_optimizer/tests/test_run_optimizer_cli.py
```

Les assertions/tolérances historiques restent intactes. Les passages précommit
et sur SHA committé sont enregistrés séparément, sans additionner les répétitions.
La baseline R25 de 111 tests et 2 sous-tests est historique, distincte de cette
validation. Les 179 fichiers de base doivent garder leurs empreintes.

Références bibliographiques fournies dans le mandat, sans nouvelle acquisition
ni validation expérimentale revendiquée : Fletcher et al., JASA 2006,
DOI 10.1121/1.2146090, équation 9 ; Silva/Kergomard/Vergez, ISMA 2007,
arXiv:0705.4242 ; Matteoli et al., arXiv:2112.08751v2. Les équations explicites
du mandat et les modules réels constituent le point de départ de ce bloc.
