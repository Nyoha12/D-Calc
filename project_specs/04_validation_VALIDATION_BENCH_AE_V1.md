# VALIDATION_BENCH_AE_V1.md

## Principe
Le banc A–E doit être stabilisé avant l'optimisation automatique. Il valide les tendances physiques minimales du moteur linéaire.

## Cas A — cylindre de référence
- 140 cm
- diamètre 3.8 cm
- matériau `pvc_pressure`
- variantes : longer, shorter, narrower, wider

### Assertions minimales
- `f0_hz` existe
- `peak_count > 2`
- plus long => `f0_hz` plus bas
- match analytique acceptable sur les premiers pics
- bonne confiance modèle

## Cas B — cône tronqué
- 140 cm
- entrée 3.0 cm
- sortie 7.0 cm
- matériau `pvc_pressure`

### Assertions minimales
- `toot_ratio` existe
- `toot_ratio` dans une plage plausible
- `odd_only_score` et `harmonicity_error` existent
- plusieurs pics exploitables

## Cas C — cylindre + cloche
- cylindre 120 cm @ 3.8 cm
- cloche 20 cm, 3.8 → 12 cm

### Assertions minimales
- brillance plus élevée que sans cloche
- métriques HF plus élevées
- confiance 1D plus basse pour très grande sortie

## Cas D — multi-segments avec marches
- plusieurs segments cylindriques à diamètres alternés

### Assertions minimales
- plusieurs pics exploitables
- différence locale de structure des pics par rapport à une version lissée
- harmonicité différente de la version lissée

## Cas E — irrégulier dissipatif
- profil élargi progressivement, matériau bois plus dissipatif
- variantes : référence plus lisse, version epoxy-linée

### Assertions minimales
- `Q` plus faible sur la version dissipative
- nombre de pics non supérieur à la version lisse
- effet matériaux visible sur la netteté des pics

## Tendances physiques obligatoires
- longueur ↑ ⇒ `f0` ↓
- pertes ↑ ⇒ `Q` ↓
- cloche ↑ ⇒ radiation HF ↑ et confiance 1D ↓ si sortie très large
- cylindre ≈ comportement plus odd-only qu'un cône tronqué
- multi-segments ⇒ sculpture locale de `Z(f)`
