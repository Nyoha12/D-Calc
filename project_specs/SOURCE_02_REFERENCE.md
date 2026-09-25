# SOURCE-02 — source acoustique à impédance finie

Statut : **inferred**, dérivation algébrique explicite. Ce diagnostic linéaire
n'est ni un coefficient empirique, ni un modèle de joueur, ni une validation
physique A–E. Les lois de propagation, pertes, charge et sources idéales restent
celles du dépôt.

## Activation et unités

API : `didgeridoo_optimizer.acoustics.source_impedance.apply_thevenin_source(transfer, pressure, impedance)`.
`pressure` est Ps complexe crête en Pa ; `impedance` est Zs en Pa.s/m^3.
Chacun est un scalaire ou un vecteur exactement aligné sur les fréquences du
transfert. Booléens, tableaux imbriqués, non-finis et Re(Zs)<0 sont refusés.
Re(Zs)>=0 est une passivité ponctuelle ; cette admission ne garantit pas la
causalité d'un spectre arbitraire. Aucune normalisation ni valeur de joueur
implicite. Le transfert doit conserver `propagation_invalid`, son masque global,
les états normalisés et leurs bornes ; les raisons des transferts idéaux ne
remplacent pas ce masque.

CLI (répertoire de sortie neuf) :

```text
python -B -m tools.forced_response_compare --case cylinder --thevenin-pressure-peak-pa 1 --source-resistance-pa-s-m3 300000 --loss-model zk --air-reference ck_dry20 --radiation-model silva_unflanged --f-min 70 --f-max 72 --points 3 --h-cm 5 --output-dir /chemin/neuf/source02
```

Ps réel fini >0 ; Rs réel fini >=0, **obligatoire et explicite**. L'option de
résistance est interdite avec les sources idéales, y compris Rs=0. Les trois
options de source sont mutuellement exclusives. `--dry-run` valide avant toute
propagation et création de fichiers. Aucun mode Norton CLI n'est ajouté.

## Fermeture et résolution numérique

Convention exp(+jωt), amplitudes crête et débits U1/U2 vers la sortie :

```text
Ps = p1 + Zs U1
p1 = exp(L) Zref q U2 ; U1 = exp(L) v U2
d = Zref q + Zs v
U2 = Ps exp(-L)/d ; p1 = Ps Zref q/d ; U1 = Ps v/d ; p2 = Zr U2
```

L'implémentation réutilise q, v, L sans repropagation ni réévaluation des pertes
ou de la charge. Les produits et incertitudes sont formés dans une échelle
logarithmique commune ; elle ne forme ni exp(L), ni Zs/Zref, ni les quotients
idéaux via leurs projections cartésiennes.

À cette même échelle, la borne du dénominateur inclut Zref ep + |Zs| eu,
les erreurs des produits/somme, des conversions log/phase et un plancher
subnormal conservateur. Pour h=|d calculé| et E sa borne, la fermeture non nulle
exige h>E. Le défaut relatif d'inversion est majoré par E/(h-E).
Avec rn=ep/|q| (ou eu/|v|), la division transporte rn+rd+rn rd,
plus une marge de calcul log/phase. U2 ajoute la marge liée à L. Ces bornes
numériques au maillage fixé ne couvrent pas l'erreur de discrétisation, la
validité du modèle ni les incertitudes physiques.

Une annulation locale q ou v sous sa borne ne certifie pas le port correspondant,
même si d et U2 restent résolus. Une invalidité globale rend les ports et
puissances indisponibles, même avec Ps, Rs ou charge nuls. Ps=0 dans un cadre
valide choisit la réponse forcée nulle, même si d est singulier : cela ne résout
pas et n'exclut pas le mode homogène. L'identité vide à charge nulle prouve p1=0 ;
une annulation numérique seule ne prouve aucun zéro analytique.

Les projections sous/débordantes gardent les logs et phases lorsqu'ils sont
résolus. Zs=0 retrouve les ports de pression idéale aux points définis. La limite
Norton exige Ps=Zs Us avec **Us fixé** quand |Zs| croît ; Ps fixé donnerait une
autre limite. Un changement d'impédance source peut inverser une comparaison de
réponses, sans établir une supériorité instrumentale ou une validation de joueur.

## Puissances

Le groupe instrument `powers` conserve sa signification :

```text
Pin = 0.5 Re(p1 conj(U1)) ; Pload = 0.5 Re(Zr) |U2|²
Pdiss = Pin - Pload ; eta = Pload/Pin
```

Le groupe distinct `source_powers` contient :

```text
Psupply = 0.5 Re(Ps conj(U1)) ; Pinternal = 0.5 Re(Zs) |U1|²
eta_source = Pload/Psupply
Psupply = Pin + Pinternal
```

Les produits réels transportent leurs incertitudes sur l'enveloppe 0.5|p||U|,
y compris en quadrature. Pinternal utilise Re(Zs) directement. Le bilan source
est testé avec la somme des bornes des trois puissances calculées séparément ;
aucune puissance n'est reconstruite pour forcer ce bilan. Les résidus signés,
`roundoff_limited` et `passivity_violation` ne sont pas clampés. Pdiss peut être
indisponible si les watts nécessaires à la soustraction sont hors plage.
Les deux rendements ont leurs propres critères de positivité et résolution ;
les logs peuvent permettre un ratio malgré des watts sous/débordants. Aucun
rendement physiologique ni somme de watts sur les fréquences.

## Contrat d'export et comparaison

Les sources idéales gardent `dcalc.forced_response.v1` et leurs champs/valeurs.
Thévenin utilise exclusivement `dcalc.forced_response.v2` :

- `source.kind = thevenin_pressure`, `source.amplitude` représente Ps exact fourni,
  `source.units = Pa peak` ;
- `source.impedance` : même représentation complexe alignée (value real/imag,
  status, reason, log_abs, phase_rad), `impedance_units = Pa.s/m^3` ;
- `source_powers.Psupply/Pinternal` en W, `eta_source` sans dimension (`1`) ;
- bornes `roundoff_tolerance_w` et `roundoff_tolerance_log_w` pour les puissances
  source et Pin/Pload ; positivité résolue explicite pour Pin et Psupply.

JSON strict : `null` n'est pas zéro, aucun NaN/Inf. CSV conserve valeurs,
unités, logs, statuts et raisons. Le texte contient les paramètres source et
un tableau Markdown de ses puissances. Les comptes de statuts et
`numerically_complete` incluent les puissances source ; complétude numérique
ne signifie pas validation physique.

Le comparateur hors ligne admet v1/v2 et refuse les métadonnées manquantes ou
contradictoires. Même famille, même vecteur complexe Ps **et même vecteur Zs**
exacts requis, outre les gardes air/grille/maillage existantes. Thévenin Zs=0
reste une famille distincte de la pression idéale. Aucune normalisation.
Le schéma de sortie `dcalc.forced_response_comparison.v1` garde sa structure
avec observables extensibles : les entrées v2 ajoutent des noms qualifiés
`source_powers.Psupply`, `source_powers.Pinternal`, `source_powers.eta_source`.
Différences absolues/relatives pour les trois, rapports dB seulement pour les
puissances positives résolues ; aucun dB pour eta_source. JSON/CSV/Markdown
conservent valeurs, unités, statuts, raisons et couverture.

## Vérification ciblée

`test_source_impedance.py` couvre ABCD modéré indépendant, perturbations Decimal,
limites pression/Norton, amplitude/phase, Zref, extrêmes logarithmiques,
annulations, invalidité globale, admission et sentinelles de non-repropagation.
`test_source_impedance_cli.py` couvre CLI réelles, dry-run, erreurs JSON,
écrasement, schémas, paramètres exacts, exports et comparaison hors ligne.
Les quatre suites historiques forced_response/roundoff/cli/comparison et les
témoins v1 sont conservés. Ces contrôles ne remplacent aucune validation A–E.
