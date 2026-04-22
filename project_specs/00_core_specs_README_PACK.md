# Didgeridoo Optimizer Project Pack

Ce pack regroupe les fichiers de travail prêts à être stockés dans un Project ChatGPT ou en local.

## Contenu

- `PROGRAM_SPEC_V1.md` — spécification générale du programme.
- `PHYSICS_AND_METRICS.md` — noyau physique, métriques, limites de validité.
- `CONFIG_TEMPLATE_V1.yaml` — gabarit de configuration complet.
- `MATERIALS_POLICY_AND_UNCERTAINTY.md` — politique matériaux, variantes, incertitude, calibration.
- `materials_base_v1.yaml` — base matériaux V1 (40 matériaux de base).
- `wood_variant_rules_v1.yaml` — règles de génération des variantes bois.
- `PYTHON_SKELETON_OVERVIEW.md` — vue d’ensemble du squelette Python.
- `python_skeleton/` — package Python minimal avec classes et signatures.
- `REFERENCES_WORKING.md` — références de travail à conserver avec le projet.

## Statut général

- Les structures de données, la config, les règles de variantes et l’architecture logicielle sont considérées comme **prêtes à l’emploi**.
- Les champs acoustiques fins `beta`, `porosity_leak` et `wall_loss` sont fournis comme **valeurs initiales plausibles** et marqués `sourced`, `inferred` ou `to_calibrate` selon les cas.
- Les variantes bois doivent être générées automatiquement à partir de `materials_base_v1.yaml` + `wood_variant_rules_v1.yaml`.

## Ordre conseillé pour la suite

1. Charger `CONFIG_TEMPLATE_V1.yaml`.
2. Charger `materials_base_v1.yaml`.
3. Générer les variantes bois via `wood_variant_rules_v1.yaml`.
4. Implémenter le noyau linéaire : géométrie -> TMM -> Z_in(f) -> pics -> métriques.
5. Ajouter optimisation + estimation du temps.
6. Ajouter robustesse et non-linéaire.
