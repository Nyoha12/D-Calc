# PROJECT_HANDOFF_STATUS_V2.md

## État du projet au moment du relais
Le **blueprint V1** est considéré comme suffisamment complet. La prochaine conversation doit éviter de continuer à produire de la conception abstraite si le code n'a pas encore démarré.

## Ce qui est verrouillé
- périmètre du programme ;
- noyau physique linéaire 1D/TMM ;
- politique matériaux et incertitude ;
- architecture logicielle générale ;
- feuille de route par sprints ;
- cas de validation A–E ;
- ordre de développement recommandé.

## Ce qui reste placeholder
- code effectif du squelette Python ;
- coefficients matériaux fins à calibrer ;
- robustesse complète ;
- non-linéaire complet ;
- fusion finale end-to-end.

## Prochaine étape prioritaire
Coder le **MVP linéaire** et le stabiliser sur les cas A–E avant de poursuivre sur optimisation automatique, robustesse et non-linéaire.

## Ordre de travail recommandé
1. Sprint 0 — socle
2. Sprint 1 — moteur linéaire nominal
3. Sprint 2 — validation A–E
4. Sprint 3 — exploration automatique
5. Sprint 4 — reporting
6. Sprint 5 — robustesse
7. Sprint 6 — non-linéaire MVP
8. Sprint 7 — intégration finale
9. Sprint 8 — calibration ciblée

## Décision importante sur les fichiers
Ne pas mettre à jour les fichiers du Project à chaque tour.
Mettre à jour seulement lorsqu'un vrai jalon est franchi, par exemple :
- MVP linéaire fonctionnel ;
- banc A–E stabilisé ;
- optimisation automatique stabilisée ;
- robustesse stabilisée ;
- non-linéaire MVP stabilisé.

## Première question que la prochaine conversation ne doit PAS reposer
- le périmètre des didgeridoos ;
- les objectifs musicaux ;
- la logique matériaux ;
- la logique d'optimisation ;
- le fait qu'il faut distinguer `sourced`, `inferred`, `to_calibrate`.

## Première tâche à engager
Passer en mode implémentation, bloc par bloc, à partir de :
- `materials/models.py`
- `materials/database.py`
- `materials/variants.py`
- `materials/uncertainty.py`
- `acoustics/losses.py`
- `acoustics/radiation.py`
