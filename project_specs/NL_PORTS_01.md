# NL-PORTS-01 — ports conjugués V2 expérimentaux

Le choix `v2_port_model=jet-only|conjugate` conserve `jet-only` par défaut.
La CLI expose `--v2-port-model` dans le workflow CONFIG/DESIGN/DB existant.
`conjugate` exige `--v2-schedule simultaneous` et `PassiveResonator` ; une
incompatibilité est refusée avant contexte, acoustique et écriture. Le
comparateur FIR annonce toujours `historical` et `jet-only` explicitement.
Aucun classement, score, seuil de joueur ou changement des pipelines usuels.

## Hypothèses et statut physique

Le modèle réduit utilise un déplacement généralisé x positif vers l'ouverture,
une pression amont idéale Pu prescrite, des pressions uniformes aux ports et
les paramètres mécaniques V2 déclarés. Il n'ajoute ni source amont d'impédance
finie ni tractus vocal. La force V2, le contact et le jet unidirectionnel natifs
restent identiques. Les aires de force proviennent exclusivement des paramètres
existants : Au=s A, Ad=-Au, où s vaut -1 ou +1, défaut inchangé -1.

La fermeture conjuguée est **lambda=1**, relativement à ce stockage mécanique :

```
Uu = Uj + Au v
Ud = Uj - Ad v
Pu Uu - p Ud = (Pu-p) Uj + (Au Pu + Ad p) v
(Uu-Ud) dt = (Au+Ad) dx
```

Ainsi s=-1 donne Uu=Ud=Uj-A v. Le débit total de port est signé, même si Uj=0 ;
il n'est jamais écrêté. Le jet reste Cd w max(h,0) sqrt(2 max(Pu-p,0)/rho).
Il ne devient pas le jet signé utilisé dans d'autres modèles d'anche.

La conjugaison force/volume résulte du travail virtuel pour la même coordonnée
et la même projection géométrique. Changer de coordonnée transforme ensemble
masse, raideur et aire. La famille lambda variable de la recherche R33 reste une
étude de sensibilité ; elle n'est pas un coefficient réglable du produit.
La fermeture ne mesure ni n'identifie une aire labiale, un mouvement réel ou un
joueur. Les paramètres sont **to_calibrate** ; la validité mathématique du bilan
du système réduit est distincte de sa validation empirique, **non obtenue**.

## Un seul pas simultané et une seule gestion d'état

`SimultaneousCoupling` conserve le solveur scalaire, le gradient discret du
contact, la récurrence passive native et l'engagement atomique de l'état.
`lip_ports.py` fournit uniquement des fonctions sans état pour les ports et
leur borne. Aucun second intégrateur ou gestionnaire de contact.

Avec tau=dt/2, hmid=h0+x0+tau vm, M=m+tau r+tau² K :

```
deni = 1 + tau gammai + tau² omegai²
ph = sum(ai (vi-tau omegai² qi)/deni)
D = R0 + sum(ai tau/deni)
ph_eff = ph-D Ad vm                # conjugate ; ph en jet-only
B = Pu-ph_eff
k = Cd w sqrt(2/rho)
```

Si B<=0 ou hmid<=0, Uj=0 et p=ph_eff. Sinon la racine stable est
`t=B/(hypot(D*k*hmid,2*sqrt(B))/2+D*k*hmid/2)` ; Uj=k hmid t et
p=ph_eff+D Uj. Le carré t² est conservé, sans perdre les petits écarts par
soustraction de pressions. La différence avec le jet natif évalué à p et
l'incertitude de soustraction sont exportées.
Le résidu mécanique est celui du pas V2 :
`M vm-tau(Fc+Fd)-m v0+tau K x0-tau(Au Pu+Ad p)`.
Le candidat appelle exactement une fois le vrai `PassiveResonator.step(Ud)`.
Sa fs native et les coefficients chargés ne sont ni refittés ni rééchantillonnés.

Pour Ad>0, une condition suffisante de monotonie stricte est

```
Bedge = Pu-ph-D Ad (h0+x0)/tau
M > tau Ad D max(k tau sqrt(max(Bedge,0))-Ad,0).
```

En branche active, `p'=D*(k*tau*t-Ad)/(1+D*k*hmid/(2*t))`.
La borne majore sa partie positive ; contact et amortissement unilatéral
renforcent la monotonie. Hors jet, p'=-D Ad. Pour Ad<=0, M>0 suffit ;
pour D=0, la pression est indépendante de vm. La garde flottante reste
`64 eps (M+abs(feedback))` ; elle n'est pas une preuve par intervalles.
Son échec signifie **non résolu**, pas absence physique de solution.
Les budgets historiques restent 32 extensions /80 itérations, plafonds64/100.
Le jet-only conserve son calcul et sa borne historiques, y compris l'arrêt au
résidu mécanique d'arrondi au quasi-équilibre. Aucun plafond assoupli.

## Bilan, sorties et temps

El=m v²/2+K x²/2+Kc max(xt-x,0)²/2 inclut le potentiel de contact natif.
Er est le stockage natif du résonateur. Pour les ports conjugués :

```
Delta(El+Er) = dt * [Pu Uu - (Pu-p) Uj - r vm² + Fd vm - Pres]
Pres = R0 Ud² + sum(ai gammai vi_mid²)
```

Les travaux/pertes sont exportés en joules : `source_work_j`, `jet_loss_j`,
`lip_loss_j`, `contact_loss_j`, `resonator_loss_j`. Les résidus sont absolus et
normalisés, seuil inchangé2e-10. Le bilan du jet-only comporte le terme mécanique
non conjugué `mechanical_pressure_term_w` ; son `total_energy_residual` reste
null et le statut dit `unavailable_nonconjugate`. Le calendrier historique
n'offre pas de bilan couplé et l'annonce également. La passivité du résonateur
ne devient pas une certification d'un instrument ou d'un régime joué.

`flow_m3_s` désigne toujours le total aval. `jet_m3_s`, `upstream_flow_m3_s`,
`downstream_flow_m3_s`, `induced_upstream_m3_s`, `induced_downstream_m3_s`
sont explicites dans l'API, le JSON, le CSV et la synthèse française.
Pressions : Pa ; débits : m³/s ; déplacements : m ; vitesses : m/s ;
énergies/travaux : J ; temps : s ; masse : kg ; aires : m² ;
raideurs : N/m ; amortissements : N s/m ; rho : kg/m³.

Les états et temps complets sont aux instants n et n+1 (N+1 éléments), les
ports et pressions au milieu (N éléments). Les lignes CSV distinguent
`state_time_s`, `midpoint_time_s` et `time_s` final. Les durées demandée et
effective, la fs et l'état initial complet sont conservés. Les valeurs non
observées sont null en JSON et vides en CSV, jamais des zéros de remplacement.
Le ratio RMS induit/jet porte sur tous les échantillons acceptés, moyenne et
transitoire inclus ; le dénominateur nul donne null, avec fenêtre explicite.

Les essais candidats sont privés. Refus, overflow, interruption et budgets
conservent le dernier état accepté, le temps, les pertes et diagnostics. Un
refus à zéro pas conserve le choix effectif, la raison et l'état initial.
Le checkpoint passif précède le comparateur historique, pour préserver son
résultat si ce dernier échoue. Une simulation non résolue interdit le succès
global. Le dry-run ne calcule pas d'acoustique et n'écrit rien.

## Provenance acoustique et preuves reproductibles

Le choix de ports concerne l'excitation, pas le modèle acoustique sauvegardé.
Au rechargement, le certificat historique du modèle est conservé. Les contrôles
algébriques actuels de rechargement sont séparés du certificat de fit ; ils ne
valident pas à nouveau le fit ou le couplage. Les empreintes des sources réellement
chargées, y compris les nouveaux fichiers et imports tardifs, sont conservées.

Témoin modal SI R33 : m=1e-4, fl=80 Hz, A=3e-6, zeta=.2, h0=.0008,
w=.012, Cd=.72, rho=1.204, s=-1, hmin=1e-6, Kc=1e4, Cc=.04 ;
wa=2 pi70, gamma=wa/8, a=1e7 gamma, R0=0.
Le quartique indépendant, avec b=Cd w sqrt(2 Pu/rho),
c=b(h0-A Pu/K)/(2 Pu), g=gamma+a c, est

```
m z⁴ + (m g+r) z³ + (m wa²+r g+K+A² a) z²
      + (r wa²+K g-A a b) z + K wa².
```

Le terme A² a est absent en jet-only. Les références locales sont
PH=3996.671891607 Pa /64.8712462469 Hz en jet-only et
4003.211086430 Pa /64.8612883691 Hz en conjugate. Elles ne sont pas des seuils
physiologiques. Les tests recoupent quartique, Hurwitz et matrice, puis la
Jacobienne du vrai pas contre Cayley à4/8/12 kHz, à PH et PH±10.

Les témoins de convergence libre et contact utilisent un RHS continu littéral
indépendant et RK4 raffiné : 96/192 kHz en libre, 768/1536 kHz au contact,
20 ms, face aux pas natifs4/8/12 kHz. La référence raffinée n'est jamais
annoncée exacte ni localisée aux événements ; l'écart de raffinement est
comparé aux erreurs observées. Le contact ne promet pas un ordre universel.

Le témoin cylindrique154termes à12 kHz porte le SHA256
`0b82cd091954225fd823ca5855943a055d59e7022b23b85489d3f32c7245a59c`.
R0=1090.7616448793 Pa s/m³. Avec rho=1.204, ses traversées rationnelles R33
sont2328.934301400 Pa /67.2545425400 Hz puis2333.883284675 Pa /67.2469516338 Hz.
Ce sont des témoins de recherche, pas une nouvelle validation TMM.
La vraie CLI utilise son air CONFIG effectif (notamment CKdry20), dont la rho
est distincte. Les tests ne réajustent aucun oracle pour obtenir un gain.

Les références méthodologiques R33 (Bilbao et al., arXiv1405.2589 ; Karkar
et al., DOI10.1121/1.3651231 ; Lopes/Hélie, DOI10.3813/AAA.918931) motivent
la distinction entre travail, volume et modèles de jet. Aucune mesure brute,
aire ou coefficient de ces publications n'est adopté ou recalibré ici.
