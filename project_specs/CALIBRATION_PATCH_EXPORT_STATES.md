# États d'export des patches de calibration

Ce document fixe la lecture des artefacts d'export liés à la calibration matériau.

## Principe

Un même flux de calibration peut produire plusieurs vues d'un patch :

- proposition de patch ;
- patch rejoué ;
- patch accepté ;
- patch encore à calibrer.

Ces états ne doivent pas être confondus.

## Fichiers d'export

### 1. `materials_patch_suggestions.yaml`

Contient la **proposition de patch**.

C'est la sortie de suggestion issue de la priorisation / calibration.
Elle peut être plus large que le patch réellement rejoué.

### 2. `materials_patch_replayed.yaml`

Contient le **patch rejoué**.

C'est le patch effectivement appliqué pour la validation du flux considéré.
Dans les flux dirigés ou localisés, il peut être plus petit que la proposition.

### 3. `materials_patch_accepted.yaml`

Contient le **patch accepté**.

Il n'est rempli que si la décision du flux autorise une promotion explicite.
Typiquement :
- `accept_local_only`
- `accept_family`
- `accept_weighted`

### 4. `materials_patch_to_calibrate.yaml`

Contient le **patch encore à calibrer**.

Il est utilisé quand un patch rejoué reste informatif mais ne doit pas encore être promu.
Typiquement :
- `keep_as_to_calibrate`

### 5. `materials_patch_status.yaml`

Contient un résumé léger :
- décision ;
- validation préservée ou non ;
- matériaux présents dans chaque état.

## Règle d'interprétation

Toujours lire les artefacts dans cet ordre logique :

1. `materials_patch_status.yaml`
2. `materials_patch_replayed.yaml`
3. `materials_patch_accepted.yaml` ou `materials_patch_to_calibrate.yaml`
4. `materials_patch_suggestions.yaml`

Principe :
- la suggestion ne suffit pas à elle seule ;
- le patch rejoué décrit ce qui a réellement été testé ;
- le patch accepté décrit ce qui est promouvable ;
- le patch à calibrer décrit ce qui reste provisoire.

## Helper de backfill

Le repo contient un helper pour reconstruire ces exports à partir d'un rapport existant :

- module : `didgeridoo_optimizer.reporting.patch_exports`
- point d'entrée principal : `backfill_patch_state_exports`

Exemple PowerShell :

```powershell
python -m didgeridoo_optimizer.reporting.patch_exports results/calibration_material_semidirected
```

Ce helper est utile pour mettre à niveau des artefacts `results/` plus anciens vers le nouveau schéma d'export.

## Règle de prudence

Ne jamais présenter un patch proposé comme patch accepté.

Ne jamais présenter un coefficient matériau provisoire comme précisément établi.

Quand un doute subsiste sur la décision effective d'un flux, préférer :
- backfiller uniquement le patch rejoué ;
- ou demander un replay complet du repo si la preuve dépend réellement de l'exécution.
