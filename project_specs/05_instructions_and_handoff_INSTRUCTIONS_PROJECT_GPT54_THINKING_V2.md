# INSTRUCTIONS_PROJECT_GPT54_THINKING_V2.md

## But
Travailler dans ce Project comme contexte principal, sans supposer que tous les fichiers du Project sont à jour à chaque étape, et sans reposer les décisions déjà stabilisées.

## Règles minimales

### 1. Utiliser le Project comme contexte principal
- Considérer les chats, fichiers et instructions du Project comme le contexte principal de travail.
- Ne pas supposer qu'un fichier du Project est automatiquement la version la plus récente.

### 2. Toujours expliciter la référence utilisée
Quand une réponse s'appuie sur des fichiers du Project, préciser au minimum :
- le ou les fichiers utilisés ;
- leur statut :
  - `source de vérité actuelle`
  - `version de travail`
  - `version possiblement obsolète`
  - `à vérifier`

### 3. Priorité en cas de conflit
Priorité par défaut :
1. décision explicite la plus récente dans la conversation,
2. fichier explicitement déclaré `source de vérité actuelle`,
3. autres fichiers du Project,
4. hypothèses temporaires.

Toujours signaler les conflits au lieu de les masquer.

### 4. Mises à jour de fichiers
Ne pas proposer de mise à jour de fichier à chaque tour.
La proposer seulement si au moins un de ces cas est vrai :
- une décision de conception est stabilisée ;
- une source de vérité a changé ;
- plusieurs tours ont modifié un même point important ;
- un futur calcul ou codage risque d'utiliser une version périmée ;
- un export intermédiaire clair devient utile.

### 5. Pour les tâches complexes
Commencer par un cadrage bref :
- objectif courant ;
- fichiers réellement utilisés ;
- éventuels risques d'obsolescence ;
- puis avancer sans multiplier les demandes de confirmation inutiles.

### 6. Pour ce projet en particulier
- Ne pas redemander des informations déjà présentes dans le Project ou la conversation.
- Distinguer explicitement ce qui est `sourced`, `inferred` et `to_calibrate`.
- Ne jamais présenter comme précisément établi un coefficient provisoire.
- Ne pas continuer à créer de nouveaux modules de conception si l'implémentation n'a pas démarré.
- Après lecture des fichiers centraux, passer en priorité au **bloc d'implémentation linéaire**.

### 7. Fichiers centraux de référence
Sauf indication plus récente dans la conversation, les fichiers centraux sont :
- `PROJECT_HANDOFF_STATUS_V2.md`
- `PROGRAM_SPEC_V1.md`
- `PHYSICS_AND_METRICS.md`
- `CONFIG_TEMPLATE_V1.yaml`
- `MATERIALS_POLICY_AND_UNCERTAINTY.md`
- `materials_base_v1.yaml`
- `wood_variant_rules_v1.yaml`
- `IMPLEMENTATION_SPRINTS_V1.md`
- `MODULE_TODOS_ULTRA_CONCRETS_V1.md`
- `VALIDATION_BENCH_AE_V1.md`

### 8. Règle finale
Toujours privilégier :
- la traçabilité ;
- la clarté des sources utilisées ;
- la détection des fichiers potentiellement obsolètes ;
- et le maintien d'un petit nombre de fichiers réellement utiles.
