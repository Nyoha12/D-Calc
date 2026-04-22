# README_HANDOFF_V2.md

## But du pack
Ce pack sert de **relais de conversation** pour repartir proprement dans une nouvelle conversation, sans perdre le contexte utile ni devoir tout redemander.

Il contient :
- les fichiers de base déjà utilisés comme références du projet ;
- les **mises à jour utiles** consolidées depuis la conversation ;
- une version en **arborescence** ;
- une version **à plat** avec noms uniques pour upload facile dans un Project ;
- une mise à jour des instructions de travail pour GPT-5.4 Thinking.

## Règle de lecture conseillée dans une nouvelle conversation
Lire d’abord, dans cet ordre :
1. `PROJECT_HANDOFF_STATUS_V2.md`
2. `INSTRUCTIONS_PROJECT_GPT54_THINKING_V2.md`
3. `PROGRAM_SPEC_V1.md`
4. `PHYSICS_AND_METRICS.md`
5. `MATERIALS_POLICY_AND_UNCERTAINTY.md`
6. `IMPLEMENTATION_SPRINTS_V1.md`
7. `MODULE_TODOS_ULTRA_CONCRETS_V1.md`
8. `VALIDATION_BENCH_AE_V1.md`

## Statut des fichiers
### Source de vérité actuelle
- `PROGRAM_SPEC_V1.md`
- `PHYSICS_AND_METRICS.md`
- `MATERIALS_POLICY_AND_UNCERTAINTY.md`
- `IMPLEMENTATION_SPRINTS_V1.md`
- `MODULE_TODOS_ULTRA_CONCRETS_V1.md`
- `VALIDATION_BENCH_AE_V1.md`
- `INSTRUCTIONS_PROJECT_GPT54_THINKING_V2.md`
- `PROJECT_HANDOFF_STATUS_V2.md`

### Version de travail
- `CONFIG_TEMPLATE_V1.yaml`
- `materials_base_v1.yaml`
- `wood_variant_rules_v1.yaml`
- `PYTHON_SKELETON_COMBINED.md`

### À calibrer / compléter plus tard
- coefficients matériaux fins (`beta`, `porosity_leak`, `wall_loss`) quand le moteur de sensibilité/robustesse sera branché.

## Ce qu'il ne faut pas faire au redémarrage
- ne pas redemander le périmètre général ;
- ne pas reproposer de nouveaux modules de conception si le code n'a pas encore commencé ;
- ne pas mettre à jour les fichiers du Project à chaque tour ;
- ne pas traiter les coefficients matériaux provisoires comme des données précises.

## Première action recommandée au redémarrage
Commencer directement par le **bloc Sprint 1** :
- `materials/*`
- `geometry/*`
- `acoustics/losses.py`
- `acoustics/radiation.py`
- `acoustics/transfer_matrix.py`
- `acoustics/peaks.py`
- `acoustics/features.py`
- `optimization/objectives.py`
- `pipeline/evaluate_linear.py`
