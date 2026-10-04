# TD-COUPLE-01 — calendrier simultané V2 expérimental

Base : `7ef487b94bfc10baad892d26db664859368f29a9`, après TD-PASS-01 et la recherche #69.
Le calendrier historique reste le défaut. Aucun coefficient, signe, loi V2,
matériau, score, fit, critère KKT/fidélité/maillage ou politique A–E ne change.
Les paramètres labiaux restent **to_calibrate**. Ce travail vérifie un schéma
numérique ; il ne valide ni joueur, son joué, registre, ni passivité globale.

## Usage et temps

```python
from didgeridoo_optimizer.nonlinear.simultaneous_coupling import SimultaneousCoupling
from didgeridoo_optimizer.nonlinear.lips import DimensionedLipParameters

coupling = SimultaneousCoupling(model, params=DimensionedLipParameters(), rho=1.204)
result = coupling.simulate(.05)
assert result['ok']  # à vérifier, jamais déduit de la seule création du modèle
```

`model` doit être un vrai `PassiveResonator`. Aucun faux `impulse_kernel`,
remplacement du constructeur FIR ou copie du simulateur historique. L'API
`initialize([x,v,q...,v_res...], time_s=..., last_dissipation_w=...)` valide
l'état complet avant mutation ; `state`, `snapshot()` et `diagnostics` sont des
copies. `reset()` retrouve l'état initial du constructeur. Par défaut,
`x=.02*h0`, `v=.001 m/s`, résonateur au repos, comme le départ normal V2 natif.
L'état courant d'un modèle fourni n'est pas implicitement adopté.

Le wrapper `pipeline.time_domain_reference.simulate_v2(...,
v2_schedule='simultaneous')` borne fs à 1000–12000 Hz et la durée à .2 s.
La CLI existante accepte `--v2-schedule historical|simultaneous`, avec
`--v2-pressure-pa` et `--v2-duration-s`. CONFIG/DESIGN/DB, contrôles de sources,
`--model-in` et dry-run suivent le workflow TD-PASS-01 existant. Exemple à
adapter à un modèle chargé avec ses options acoustiques exactes :

```text
python -m tools.time_domain_reference --config CONFIG --design DESIGN \
  --output-dir OUTPUT --model-in MODEL --basis-completion r29 --h-cm 2 \
  --air-reference ck_dry20 --v2-pressure-pa 7000 --v2-duration-s .05 \
  --v2-schedule simultaneous
```

Le comparateur FIR reste explicitement historique. Une demande simultanée sur
FIR à l'API est refusée sans fallback. Un changement de calendrier n'entre pas
dans l'identité acoustique ni les coefficients/provenances du modèle sauvegardé.
Le modèle se recharge avec les mêmes conditions de fit. Le backend avance une
seule fois à sa fs native : aucun sous-pas acoustique, rééchantillonnage ou
changement implicite de fs des coefficients `discrete_prewarped`.

La simulation contient N+1 états et temps aux extrémités, N pressions/débits
au milieu et N diagnostics. `floor(duration*fs)` pas sont demandés, avec garde
8 epsilon sur la multiplication ; durée demandée/effective et fs sont
exportées. Il n'y a pas de minimum caché de 32 pas en simultané ; le minimum
historique natif reste inchangé. Les limites de pas/temps et erreurs rendent
`ok=False`, `non_resolu`, la raison et les partiels. Un pas refusé conserve
lèvres, résonateur, perte native, temps et derniers diagnostics. Un checkpoint
conserve le résultat passif avant l'exécution du comparateur FIR.

## Même loi, quadrature de contact

Unités SI ; x positif ouvre. `h=h0+x`, `xt=hmin-h0`, `tau=dt/2`,
`K=m*(2*pi*fl)^2`, `r=2*zeta*m*2*pi*fl`, `Au=s*A`, `Ad=-Au`.
Raideur, damping, validation, force de pression et débit sont ceux de l'API V2.
La masse volumique est celle de l'appel, exportée sans substitution.

```text
Phi(x) = Kc*max(xt-x,0)^2/2
Fc = -(Phi(x1)-Phi(x0))/(x1-x0)
Fd = Cc*max(-vm-max(x0-xt,0)/dt,0)
x1 = x0+dt*vm ; v1 = 2*vm-v0 ; xmid=x0+tau*vm
M = m+tau*r+tau^2*K
R(vm) = M*vm-tau*(Fc+Fd)-[m*v0-tau*K*x0+tau*(Au*Pu+Ad*p(vm))]
```

`Fc` utilise des branches factorisées : nul libre/libre, moyenne des
pénétrations contact/contact, triangle pondéré à l'entrée/sortie. La limite
stationnaire est native. `Fd` est l'intégrale du damping natif à vitesse vm
sur le segment ; `Fd*vm<=0`. Une frontière ponctuelle a une mesure nulle,
alors qu'un segment entrant depuis xt a une portion en contact. Aucun lissage
physique de xt. Avant le code produit, 10 vérifications indépendantes ont
comparé ces forces à des intégrales par morceaux, au potentiel, aux quatre
transitions et à la réduction libre 2×2 ; aucune correction de loi nécessaire.

Pour chaque vm, élimination du port :

```text
den_i=1+tau*gamma_i+tau^2*omega_i^2
ph=sum(ai*(vi-tau*omega_i^2*qi)/den_i)
D=R0+sum(ai*tau/den_i) ; B=Pu-ph
k=Cd*w*sqrt(2/rho)
si B<=0 ou hmid<=0 : U=0,p=ph
sinon b=D*k*hmid ; t=2*B/(sqrt(b*b+4*B)+b)
U=k*hmid*t ; p=ph+D*U ; delta=t*t
```

`hypot` et la forme divisée évitent le carré/numérateur débordant ; les
intermédiaires non représentables sont refusés. Le résidu Bernoulli dépend
du domaine actif, pas d'un débit éventuellement sous-débordé à zéro. La
différence signée Pu−p et son incertitude sont distinctes de `delta_clipped_pa`.
La valeur conservée t² évite d'utiliser une soustraction mal conditionnée
comme oracle de la branche active. Le débit natif est également évalué et
son écart exporté.

## Certificat et engagement du pas

Convexité de Phi et décroissance de Fd donnent la borne suffisante :

```text
M - tau^2*max(Ad,0)*D*k*sqrt(max(B,0)) > 0
```

Le port est continu, borné entre ph et Pu quand B>0, et sa pente est bornée
par `tau*D*k*sqrt(B)`. Sous ce certificat, R est strictement croissant et ses
limites ont des signes opposés. Le code exige une marge supérieure à
`64*epsilon*(M+abs(feedback))` pour les produits, sommes et soustraction en
flottants. C'est un garde conservateur numérique, pas un certificat par
arithmétique d'intervalles. Hors certificat : **non résolu**, jamais « aucune
solution physique ». Pas de première racine choisie ou Newton aveugle.

Bracket symétrique déterministe, au plus 32 extensions et 80 itérations par
défaut (plafonds 64/100), dichotomie avec résidu aux échelles des termes
mécaniques et arrêt aux flottants adjacents. Le résidu relatif maximal accepté
est 2e−10 ; les résidus absolus sont également exposés. Une petite couche
contrôlée initialise q/v sur une copie privée validée du backend. Le candidat
appelle **le vrai `PassiveResonator.step`** ; aucune récurrence n'est recopiée.
Finitude des états, énergies, pertes, temps et diagnostics, résidus et garde
du fichier source sont vérifiés avant engagement.

## Bilans et frontières

```text
El=(m*v^2+K*x^2)/2+Phi(x)
DeltaEl=dt*((Au*Pu+Ad*p)*vm-r*vm^2+Fd*vm)
DeltaEr=dt*(p*U-R0*U^2-sum(ai*gamma_i*vi_mid^2))
```

Er et les pertes sont natifs. Les pertes labiales, contact et résonateur,
travaux de pression et résidus absolus/normalisés restent séparés. Le terme
mécanique `Au*(Pu-p)*vm` est exporté distinctement : il n'est pas annulé par
le débit V2 actuel. Aucun débit balayé ni certificat de passivité globale ajouté.

Contact h=hmin, fermeture h=0, DeltaP=0 et vitesse nulle en contact sont quatre
frontières différentes. Les étiquettes comparent x à xt, avec garde de
8 epsilon multiplié par l'échelle des déplacements/ouvertures. Cela traite
`h0+(hmin-h0)` et ses voisins `nextafter` sans changer force, signe, coefficient
ou ouverture. Les labels expriment incertitude/non-régularité ; ils ne
certifient pas un événement continu localisé. La fraction de contact et les
transitions concernent le **segment discret**. Trois points libres ne prouvent
pas l'absence de contact entre ces points.

`reference.json`, `reference.csv` et la synthèse française conservent le même
résumé des statuts, durées, contacts, bilans et résidus. `v2.csv` comporte une
ligne par pas accepté ; les données indisponibles du calendrier historique
restent vides. Un refus sans pas figure dans le résumé. Un simultané partiel
rend le résultat global non accepté même si les certificats acoustiques sont
valides ; ceux-ci restent explicitement distincts.

## Oracles et portée des résultats

Modal exact : omega=2*pi*70, gamma=omega/8, a=1e7*gamma, R0=0. V2 par défaut
m=1e−4, fl=80, A=3e−6, zeta=.2, h0=.0008, w=.012, Cd=.72,
rho=1.204, s=−1, hmin=1e−6, Kc=1e4, Cc=.04, unités SI.
Le Jacobien du **vrai pas libre** est comparé par différences centrées mises à
l'échelle à Cayley de la matrice continue pour 4/8/12 kHz, à 1500 Pa et PH±10/PH.
Meilleure erreur maximale sur ces douze cas : 1.08e−12 (amplitudes relatives
1e−2, 1e−3, 1e−4 conservées dans la preuve). PH=3996.6718916070563 Pa,
fH=64.87124624686057 Hz ; maxRe(PH−10)=−.0596518928/s et
maxRe(PH+10)=+.0595101696/s. Aucun ajustement physique. Cayley préserve le
changement de stabilité, avec une déformation de fréquence dépendante de fs.

Les anciens 4062.676894137 Pa à 12 kHz / 4198.524055211 Pa à 4 kHz sont des
**témoins historiques R31**, pas des seuils mesurés par cette nouvelle CLI.
Les tests existants gardent leurs assertions et tolérances.

Le témoin continu indépendant de contact résout exactement les systèmes
mécaniques affines 2×2 à pression aval nulle, et localise par bracket/dichotomie
les changements x=xt et v=0. Départ normal, Pu=7000 Pa, durée .02 s, aucun
réglage de Kc/Cc. Erreur RMS de [x/.0008,v/.4] :

| fs | Erreur | Ordre observé depuis la grille précédente |
|---:|---:|---:|
| 4000 | .07003899 | — |
| 8000 | .02482432 | 1.4964 |
| 12000 | .01173232 | 1.8484 |
| 24000 | .00307787 | 1.9305 |

Six transitions de segment sont effectivement traversées à chaque grille,
avec fermeture et réouverture. L'ordre deux n'est pas imposé aux coudes ;
l'erreur à 4 kHz reste substantielle. Le témoin à 24 kHz est une mécanique
analytique sans modes, **hors wrapper public**, jamais un rééchantillonnage
du modèle cylindrique précompensé.

Le workflow réel recharge le cylindre à 154 termes, fs=12000, sans refit,
avec sa provenance de base vérifiée. Les cas libre 1500 Pa et contact 7000 Pa
partent de l'état normal sur .05 s avec la rho réelle CKdry20 ; bilan/résidus
et modèle rechargé sont contrôlés. La parité historique à 2700 Pa compare
les signaux natifs aux résultats antérieurs inchangés. Matériaux et paramètres
restent non calibrés. Aucun sweep global, nouvelle validation A–E ou promotion.
