# Vue d’ensemble du squelette Python V1

## But

Le squelette Python sert à fixer :
- les modules,
- les types de données,
- l’ordre d’exécution,
- les points où brancher plus tard la physique linéaire, l’optimisation, puis le non-linéaire.

## Modules principaux

- `config/` — lecture et validation de la config.
- `materials/` — base matériaux, variantes, incertitude.
- `geometry/` — segments et designs.
- `acoustics/` — noyau linéaire.
- `player/` — profils joueur et tractus vocal.
- `nonlinear/` — lèvres et régimes.
- `optimization/` — espace de recherche, objectifs, Pareto, estimation temps.
- `reporting/` — classement, export, résumés.
- `pipeline/` — orchestration.

## Ordre minimal de codage

1. `config`
2. `materials`
3. `geometry`
4. `acoustics`
5. `optimization`
6. `reporting`
7. `player`
8. `nonlinear`

## Convention recommandée

- garder des fonctions pures pour les calculs physiques,
- limiter les effets de bord,
- sérialiser tous les résultats intermédiaires importants,
- versionner les formats YAML et JSON.
