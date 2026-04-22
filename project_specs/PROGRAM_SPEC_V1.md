# Spécification V1 du programme d’optimisation de didgeridoo

## Finalité

Le programme doit optimiser, comparer et classer des didgeridoos paramétriques en faisant varier :

- la géométrie interne (segments, cônes, cloche, embouchure),
- les matériaux par segment,
- des topologies optionnelles (branches, résonateurs de Helmholtz),
- des objectifs acoustiques et de jouabilité entièrement configurables.

## Sorties attendues

- 1 design final.
- Top 20 alternatives avec scores détaillés.
- Courbes : impédance d’entrée, métriques de rayonnement, éventuellement résultats non-linéaires.
- Estimation du temps avant lancement.
- Indice de confiance du modèle.

## Objectifs disponibles

Chaque objectif doit être activable ou désactivable indépendamment.

- Drone / fréquence fondamentale `f0`
- Pics d’impédance `(fn, |Zn|, Qn)`
- Harmonicité / odd-only
- Backpressure
- Brillance / rayonnement
- Facilité du toot
- Contrôle vocal / formants
- Transitoires / bruit utile
- Robustesse débutant
- Robustesse expert
- Seuil d’oscillation
- Stabilité de régime
- Simplicité de fabrication
- Simplicité matériaux
- Efficacité calculatoire

## Principes de modélisation

### Noyau linéaire

- Modèle 1D de type ligne de transmission / matrices de transfert.
- Pertes internes par nombre d’onde complexe.
- Radiation en sortie via impédance de radiation.
- Extraction des pics et indicateurs sur `Z_in(f)`.

### Raffinement non-linéaire

- Modèle lèvres minimal : oscillateur + Bernoulli + couplage au résonateur.
- Utilisé seulement sur les meilleurs candidats après tri linéaire.

## Matériaux

La base matériaux doit distinguer :

- matériaux de base,
- variantes bois,
- incertitude,
- niveau de confiance,
- priorité de calibration.

## Règle clé

Ne jamais traiter comme “précis” un coefficient matériau qui n’est pas réellement sourcé ou calibré.

## Pipeline recommandé

1. Génération de candidats.
2. Validation géométrique.
3. Évaluation linéaire.
4. Scoring multi-objectif.
5. Robustesse joueur / matériaux.
6. Raffinement non-linéaire top-N.
7. Sélection finale.
8. Rapport.
