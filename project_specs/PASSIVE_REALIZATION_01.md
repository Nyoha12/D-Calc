# PASSIVE-REALIZATION-01 — conversion scalaire explicite

Ce produit **opt-in** transforme des coefficients rationnels réels en un vrai
`nonlinear.passive_state.PassiveState`. Il ne fait ni fit, ni TMM, ni validation
instrumentale. Aucun chemin historique, couplage, coefficient matériau ou gate
acoustique n'est modifié. Il n'est raccordé ni à `played_search` ni à
`register_target`. Les sorties ne constituent pas une promotion physique.

## Contrat d'entrée et API

```python
from pathlib import Path
from didgeridoo_optimizer.nonlinear.passive_realization import convert

p = Path('project_specs/examples/passive_realization')
r = convert((p/'relaxation.json').read_bytes(),
            (p/'relaxation-certificate.json').read_bytes())
model = r['model']                 # le modèle natif, au repos
model.save('/chemin/neuf/model.json')
print(r['diagnostics']['dc'])
```

`convert(source_bytes, certificate_bytes=None, frequency_hz=None, seconds=175)`
reçoit des **bytes JSON immuables en mémoire**, sans chemins numériques cachés.
Il renvoie `model`, `diagnostics`, `witness`, `source`, `certificate` et leurs
empreintes. L'appel ne sauvegarde rien et ne simule aucun pas. Le modèle possède
les API natives `save/load`, `copy`, `step`, `parameters`, états et checkpoints.
Les paramètres et états restent indépendants ; aucune entrée n'est modifiée.
L'appel direct appartient à l'environnement de son appelant : pour imposer les
limites de processus, utiliser `pipeline.passive_realization.run` ou le CLI.

Le schéma public `dcalc.scalar_rational.v1` est illustré intégralement dans les
exemples JSON. Ses clés exactes sont : `schema`, `p_real`, `p_imag`, `residues`,
`direct_normalized`, `zscale`, `wref`, `sample_rate_hz`, `coefficient_domain`,
`training_domain_hz`, `order`, `passivity`, `physical_validation`,
`coefficient_origin`, `provenance`. Les pôles réels ont `p_imag=0`, les représentants
des paires ont `p_imag>0`. Chaque pôle réel consomme un résidu réel ; chaque paire
consomme deux nombres successifs `[u,v]`, représentant le résidu `u+i v` au pôle
`a+i b` et son conjugué. Aucun pôle/résidu n'est retiré ou réfléchi.

Le format acquis `dcalc.private.scalar_frequency.r59.v1` est aussi accepté,
strictement avec ses clés originales : il remplace les deux clés publiques de
provenance par `R0`. Son fs reste 12000 Hz, son domaine `discrete_prewarped`.
`R0` reste une attribution de référence R59 ; il n'est ni le terme direct D ni
une instruction de correction de la DC. Les octets originaux sont conservés,
avec `passivity="not_assessed"` et `physical_validation="not_validated"`.
Le certificat R60 reste une preuve séparée, sans réétiquetage de la source.

Bornes : 128 états, 4096 fréquences de contrôle, fs entier dans [1,12000],
pôles strictement stables, échelles positives, scalaires de coefficients finis
et de module ≤10¹². La bande déclarée est strictement positive et sous Nyquist.
Le JSON exige des objets/listes ordinaires, aucune clé dupliquée, profondeur ≤12,
150000 nœuds, listes ≤4096, chaînes ≤4096, entiers ≤512 bits, fichiers ≤4 Mio.
Les symlinks dans tout le chemin, traversées `..`, fichiers spéciaux et sources
changées durant la lecture sont refusés. Un répertoire de sortie existant est
refusé, y compris un résultat partiel.

## Certificat scalaire distinct du stockage numérique

La voie générale exige un terme direct **strictement positif** et un certificat
`dcalc.scalar_interval.v1`, ou `dcalc.private.scalar_interval.r60.v1` pour R59.
Le vérificateur produit indépendant `scalar_passivity_certificate` ne lance et
n'importe aucun générateur. Il recalcule sur les valeurs binaires64 exactes,
converties en fractions rationnelles, les bornes de chaque feuille, leur
partition sans trou ni recouvrement de [0,W], le minimum global et la DC.
Pour chaque paire, il utilise les extrema du dénominateur quadratique en ω².
Les sommes sont arrondies vers l'extérieur en unités de 2⁻⁸⁰.

La queue est recalculée avec `N=sum(|r|)` pour les réels et
`2(|u|+|v|)` pour les paires, `rho=max(-Re(p)+Im(p))`,
`W >= max(2 rho,4 N/D)` et `Re H >= D-2 N/W > 0`.
Les bornes rationnelles sont des paires réduites de chaînes décimales canoniques
(numérateur/dénominateur), chacune ≤100 caractères. W≤2⁴⁰, au plus 2048 feuilles,
contrôle temporel fini. Le statut déclaré, le hash ou un précédent
`independent-check.json` ne remplacent jamais ce recalcul.

Les résistances constantes nulles et positives ont un chemin analytique sans
certificat : zéro état, `L=[[sqrt(D)]]`, `J=[]`, `g=[]`. L'identité est analytique ;
l'arrondi de la racine et sa reconstruction native restent audités. Un terme
constant négatif est actif. Les systèmes généraux D=0, pôles marginaux,
certificats incomplets et transformations non représentables sont **hors du
domaine accepté**, sans affirmation d'impossibilité ou d'activité. Les tests
natifs marginaux ne sont pas modifiés. Un résidu nul n'est pas supprimé : le
stockage peut être refusé pour non-minimalité ou non-représentabilité.

## Réalisation, normalisation et signes

On conserve `H(s)=D+C((s/omega_ref)I-A)^(-1)B`, `Z=Zscale*H`.
Un bloc réel est `[a]`. Une paire utilise
`A=[[a,-b],[b,a]], B=[1,0], C=[2u,-2v]` avant un changement diagonal de coordonnées
par amplitudes de résidus. Le changement n'altère pas l'expression scalaire.

La construction suit la LMI et la normalisation de Mehrmann & Van Dooren,
[Optimal robustness of port-Hamiltonian systems, v1, §2, équations 3,7,8](https://arxiv.org/html/1904.13326v1#S2).
On cherche P>0 et
`W(P)=[[-A.T P-P A,C.T-P B],[C-B.T P,2D]]>=0`.
Avec `P=T.T T`, on forme `At=T A T^-1`, `Bt=T B`, `Ct=C T^-1`.
La convention native impose `J=skew(At)`, `R=-sym(At)`,
`k=(Ct.T-Bt)/2`, `g=(Ct.T+Bt)/2`, `Wn=[[R,k],[k.T,D]]=L L.T`.
Le moteur reconstruit exactement sa convention `A=J-R`, `B=g-k`, `C=g+k`.
La relaxation négative et le modal à direct positif vérifient les signes.

La recherche de stockage résout un problème auxiliaire sur
`A_delta=A+delta*I`. Pour U=[I;P], le Hamiltonien utilisé est
`[[F,BB.T/(2D)],[-C.T C/(2D),-F.T]]`, `F=A_delta-BC/(2D)`.
Le sous-espace stable est orthonormalisé ; dimension, séparation, conditionnement,
partie imaginaire, défaut de symétrie brut, P>0 et équation de Riccati sont
contrôlés. La symétrisation de P n'intervient qu'après un petit défaut brut
mesuré ; les vérifications suivantes portent sur ce nouveau P.

Les fractions prédéclarées de `min(-Re(p))` sont
`(.1,.03,.01,.003,.001,.0003,.0001)`. Le premier stockage admissible est choisi
**avant** audit du transfert, et chaque refus est conservé. La LMI finale et ses
résidus sont recalculés sur A original. Delta n'apparaît jamais comme une perte
ou un déplacement des pôles exportés. Aucune projection spectrale, clipping,
jitter de Cholesky ou correction de résidu n'est permis. Une admission native
ou un audit échoué après sélection donne un refus, sans retenter un stockage
pour améliorer artificiellement le transfert.

Les unités natives sont : débit et état normalisé m³/s, pression Pa,
énergie J, `omega_ref` rad/s, `Zscale` Pa.s/m³, J/g/L sans dimension.
L'énergie vaut `Zscale/(2 omega_ref) * x.T x`.
D est séparé de `DC=Zscale*(D-C solve(A,B))`.
La réponse discrète utilise toujours `s=2j*fs*tan(pi*f/fs)` ; aucune modification
de fréquence d'échantillonnage ni resampling.

## Politique de représentation et diagnostics

Politique fixée indépendamment des gates acoustiques : maxrel source/natif
≤2e-8 sur grille, DC relative ≤2e-8, direct relatif ≤2e-12,
résidus matriciels normalisés ≤2e-10, `cond(T)*eps≤1e-8`.
Les zéros exacts sont traités séparément, sans plancher physique.
Les résidus utilisent la norme de Frobenius (norme euclidienne pour vecteurs),
normalisée par le maximum des normes comparées. Riccati utilise le maximum des
normes de ses trois termes. Échelle, absolu et normalisé sont enregistrés.

Diagnostics : toutes les tentatives de delta, conditionnements du sous-espace,
de P et T, séparation du Hamiltonien, défauts bruts de P, résidu de Riccati,
minimum propre de la LMI originale, reconstruction de P/Wn, congruence et
similarité, puis matrices **effectivement reconstruites par le modèle natif**.
Les erreurs continue/discrète et DC/direct proviennent du vrai modèle.
La DC source est évaluée indépendamment en fractions exactes avant arrondi.
Un témoin séparé sauvegarde T et les échelles de coordonnées.

La grille par défaut combine 384 points géométriques et 384 linéaires dans la
bande déclarée, plus zéro. L'API peut recevoir une autre grille ≤4096 points.
Une erreur maximale de grille **n'est pas une borne continue de transfert**.
Les données R59 acquises sont utilisées sans TMM : A 0,1–3500 Hz,
B 0,1–5500 Hz. Les points en dehors sont comptés comme extrapolation, y compris
DC. Le certificat scalaire global ne prolonge pas la fidélité acoustique.

## CLI, budget et bundle

Depuis la racine, avec l'interpréteur du projet :

```bash
python -m tools.passive_realization \
  --source project_specs/examples/passive_realization/relaxation.json \
  --certificate project_specs/examples/passive_realization/relaxation-certificate.json \
  --output-dir /chemin/autorise/conversion-neuve --dry-run

# Même commande sans --dry-run : conversion explicite.
python -m tools.passive_realization \
  --source project_specs/examples/passive_realization/relaxation.json \
  --certificate project_specs/examples/passive_realization/relaxation-certificate.json \
  --output-dir /chemin/autorise/conversion-neuve --seconds 30

python -m tools.passive_realization --read-result /chemin/autorise/conversion-neuve
```

`--certificate` est omis pour `zero.json` et `resistance.json`.
`modal.json`, `mixed.json` et leurs certificats sont des exemples synthétiques
finis. `near-singular.json` illustre un refus attendu de représentabilité native.
`--dry-run` contrôle structure, identité et destination, sans recalcul scalaire,
conversion, simulation, enfant ni sortie. Il n'est pas une admission scientifique.

`run(source, certificate, output_dir, dry_run=False, seconds=175)` exige POSIX,
le thread principal et un timer public inutilisé. BLAS1 est imposé avant import
dans le CLI et vérifié dans le worker, avec 768 Mio, CPU175s et 4 Mio/fichier.
Un seul enfant calcule. Le plafond mural public démarre avant préflight et vaut
`seconds + 2s préflight + 2s collecte + 5s clôture`, au plus184s. Il couvre aussi
la vérification/publication finale. Les attentes de l'enfant sont finies.
La collecte réutilise le helper natif `_collect_child`; code effectif, collecte,
fermeture des canaux, retard et annulation restent distincts. Les limites du
worker ne sont pas appliquées au contrôleur public. Une fin partielle, un refus,
un dépassement ou une annulation ne devient jamais une admission du lecteur.

Fichiers requis : `source.json` original, `certificate.json` original (ou null),
`model.json` au format natif, `diagnostics.json`, `witness.json`, `result.json`,
`plan.json`, `stdout.txt`, `stderr.txt`, `manifest.json`, `execution.json`,
`execution.closed.json`. Un échec peut laisser `failure.json` et les données
partielles ; une annulation possède son marqueur séparé. Aucun résultat existant
n'est écrasé. Une publication incomplète reste refusée par le lecteur.

Le lecteur frais `reporting.passive_realization.read_result` n'importe pas le
producteur numérique ou le pipeline, ne convertit et ne simule rien. Il contrôle
tous les fichiers requis, schémas, provenance, certificat scalaire exact,
paramètres natifs et identités algébriques bornées avec T. Il vérifie également
DC/direct par fractions et une résolution linéaire du modèle sauvegardé.
Il n'appelle ni `PassiveState.__init__`, ni réponse fréquentielle, ni pas, ni
fit/TMM, ni producteur. Les erreurs de grille restent des diagnostics du
producteur liés par empreintes ; le lecteur ne rejoue pas cet audit.
Les fichiers d'entrée d'origine ne sont pas nécessaires.

Chaque nouveau module capture son propre chemin et SHA à l'import. Les frontières
API/CLI/worker/lecteur revérifient ces instantanés, y compris un module numérique
déjà importé avant le pipeline. Les modules absents sont seulement référencés,
pas importés pour vérification. Cette intégrité relative et l'auto-cohérence
d'un bundle **ne sont pas une authentification externe** de son auteur.

## Expériences natives et vérification

Le modèle exporté est directement utilisable par les outils existants :

```bash
python -m tools.state_port_reference \
  --model-in /chemin/autorise/conversion-neuve/model.json \
  --signal-in project_specs/examples/passive_realization/flow.json \
  --output-dir /chemin/autorise/debit-neuf --seconds 30
python -m tools.state_port_reference --read-result /chemin/autorise/debit-neuf
```

Le débit fourni est synthétique, 120 pas SI à 12000 Hz. Le vrai
`SimultaneousCoupling(model, params=DimensionedLipParameters(), rho=1.204,
v2_port_model="conjugate")` accepte ce backend, avec checkpoint natif et reprise.
Ces expériences courtes contrôlent les bilans relatifs **natifs** ; elles ne sont
pas un toot ni une trajectoire R49 et ne changent aucun paramètre labial.

Les nouveaux tests couvrent constantes, relaxation à résidu négatif, modal avec
D>0, mélange réel/conjugué, actif stable, marginal hors domaine, presque singulier,
certificats altérés/tronqués, types/NaN/doublons/taille, import/export/provenance,
identités d'import, mutations d'entrées, processus frais, timeout/annulation et
intégrité complète des bundles. Les fixtures A/B contiennent seulement les
coefficients/certificats originaux et les grilles/réponses scalaires acquises,
avec manifeste source/date/nature/empreintes ; aucun script de pilotage ou chemin
privé obligatoire. Les assertions et tolérances historiques restent intactes.
Les preuves auteur et leur statut précommit sont séparés de toute intégration.
