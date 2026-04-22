# Politique matériaux, variantes et incertitude

## Principe général

La base matériaux doit distinguer ce qui est :

- `sourced` : appuyé directement par une source fiable,
- `inferred` : déduit de manière plausible pour le modèle,
- `to_calibrate` : devra être calibré expérimentalement ou par benchmark.

## Champs centraux

- `beta`
- `porosity_leak`
- `wall_loss`
- `confidence`
- `research_priority`

## Règle de priorité

`priorité de raffinement = incertitude * sensibilité * importance objective`

## Variants bois obligatoires

### Humidité
- `very_dry`
- `dry_indoor`
- `airdry`
- `humid`
- `green`

### Finition
- `raw`
- `sanded`
- `varnished`
- `polyurethane_lined`
- `epoxy_lined`

### Qualité
- `clear`
- `standard`
- `irregular`
- `knotty`

### Densité
- `light`
- `medium`
- `dense`

## Stratégie V1

- Matériaux de base : 40 entrées.
- Variants bois générés automatiquement.
- Les coefficients exacts de pertes restent des valeurs initiales plausibles.
- La calibration est déclenchée seulement si un paramètre a un impact fort.

## Calibration future

### Étape 1
- lancer l’optimiseur avec valeurs nominales + plages.

### Étape 2
- effectuer l’analyse de sensibilité.

### Étape 3
- identifier les paramètres critiques.

### Étape 4
- calibrer les matériaux ou variants les plus sensibles.

## Pénalité matériaux

Le score doit pénaliser :
- le nombre de matériaux distincts,
- le nombre de changements de matériau,
- les combinaisons difficiles à fabriquer.
