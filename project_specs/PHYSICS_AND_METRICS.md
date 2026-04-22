# Physique et métriques retenues

## Variables centrales

- `S(x)` : section interne le long de l’instrument.
- `Z_in(f)` : impédance d’entrée.
- `Z_R(f)` : impédance de radiation.
- `k(f)` : nombre d’onde complexe.

## Modèle linéaire de base

### Impédance caractéristique

`Zc = rho * c / S`

### Matrice de transfert d’un segment uniforme

Pour un segment de longueur `l` et nombre d’onde `k` :

- `A = cos(k l)`
- `B = j Zc sin(k l)`
- `C = j (1/Zc) sin(k l)`
- `D = cos(k l)`

### Pertes internes

Forme pratique initiale :

`alpha = 1e-5 * beta * sqrt(omega) / d`

`k_complex = omega / c - j * alpha`

### Correction d’extrémité simplifiée

`DeltaL ≈ 0.613 a`

## Limite de validité 1D

Fréquence de coupure transverse simplifiée :

`f10 ≈ 1.84 * c / (2*pi*a)`

Le programme doit calculer un indicateur de confiance quand la sortie devient trop large dans la bande analysée.

## Indicateurs à extraire

### Drone

- `f0`
- qualité du pic fondamental

### Pics d’impédance

- fréquences
- hauteurs
- facteurs de qualité `Q`

### Harmonicité

Erreur par rapport à `n * f0`.

### Backpressure

Proxy à partir de `|Z_in(f0)|` et de la pente locale.

### Brillance / rayonnement

Mesures par bande :
- 0.5–1 kHz
- 1–2 kHz
- 1–3 kHz
- 2–4 kHz

### Toot

- fréquence du premier mode supérieur jouable
- ratio `f_toot / f0`
- hauteur et Q associés

### Contrôle vocal

Sensibilité des bandes 1–3 kHz aux variations du tractus vocal.

### Transitoires

Proxy à partir des hautes fréquences rayonnées, des pertes et des discontinuités.

## Non-linéaire

### Modèle lèvres minimal

Équation générique :

- dynamique d’ouverture `h(t)`
- débit non-linéaire `u(t)`
- pression acoustique renvoyée par le résonateur

## Règle d’analyse

Le moteur doit sortir à la fois :

- des valeurs nominales,
- des résultats robustes,
- et un indicateur de confiance si certains paramètres sont fortement incertains.
