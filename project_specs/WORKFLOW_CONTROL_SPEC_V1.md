# Workflow Control Spec V1

## 1. But

Cette specification formalise le minimum de controle de workflow necessaire pour accelerer D-Calc sans demander a l'humain de relire tout le code a chaque bloc.

Le principe central est le controle par preuves synthetiques: Codex agit dans un scope borne, produit un diff lisible, lance les validations adaptees, puis rapporte les risques et la prochaine action.

## 2. Noyau du workflow actuel

| Role | Responsabilite |
|---|---|
| ChatGPT | Cadre la strategie, les objectifs, les blocs et les decisions sensibles. |
| Codex | Inspecte le repo, implemente les blocs bornes, teste, commit, push et prepare les PR quand le scope le permet. |
| GitHub | Garde l'historique canonique, les branches, les PR et les merges. |
| Tests deterministes | Bornent les regressions et fournissent la preuve minimale avant commit ou PR. |
| PRs bornees | Isolent les changements par objectif coherent et rendent la review rapide. |
| Humain | Supervise les exceptions, les decisions sensibles et les merges tant que la politique ne dit pas autrement. |

## 3. Classification des blocs

| Classe | Intention | Controle humain |
|---|---|---|
| Vert | Changement faible risque, scope clair, validation locale simple. | Intervention minimale; rapport synthetique suffisant. |
| Orange | Changement produit ou comportemental borne, avec risque utilisateur ou compatibilite. | Review humaine avant merge. |
| Rouge | Changement sensible scientifique, validation, securite, historique ou workflow. | Planning/read-only par defaut sauf accord explicite. |

## 4. Blocs verts

Dans un bloc vert, Codex peut inspecter, modifier dans le scope approuve, tester, commit, push et creer une PR avec intervention humaine minimale.

Exemples:
- tests-only;
- docs-only;
- petits rapports ou specs de controle;
- corrections non fonctionnelles;
- cleanup de warning sans changement comportemental.

Conditions:
- un scope fichier explicite;
- aucun changement sensible;
- tests ou validation adaptes OK;
- diff court et coherent;
- pas de push direct sur `main`;
- merge humain explicite pour l'instant.

## 5. Blocs orange

Dans un bloc orange, Codex peut implementer et ouvrir une PR, mais ne doit pas merger sans review humaine explicite.

Exemples:
- comportement CLI;
- exports et payloads;
- contrats utilisateur;
- modification de tests existants qui changent la couverture attendue;
- compatibilite config/report;
- automatisation de smoke tests avec execution reelle.

Controle attendu:
- expliquer le changement de comportement;
- montrer les fichiers touches;
- lancer les tests pertinents;
- signaler les risques de compatibilite;
- attendre l'accord humain avant merge.

## 6. Blocs rouges

Dans un bloc rouge, Codex reste en planning ou read-only sauf accord explicite et borne.

Inclut:
- materiaux et coefficients;
- calibration;
- formules acoustiques;
- validation A-E;
- validation policy;
- promotion de patch materiau;
- replay lourd ou global;
- dependances;
- suppression ou affaiblissement de tests;
- workflow GitHub, permissions, regles Codex ou secrets.

Regle: un bloc rouge peut etre prepare par analyse, plan, matrice de risques ou PR de documentation, mais l'action sensible demande un accord separe.

## 7. Rapport minimal Codex

Format court attendu:

```text
Bloc:
Classe: vert | orange | rouge
Fichiers touches:
Diff resume:
Tests lances:
Resultat:
Risques:
Action recommandee:
```

Le rapport doit etre suffisant pour decider sans relire tout le code, mais assez concret pour pointer le diff et les validations.

## 8. Criteres commit / PR / merge

| Action | Regle |
|---|---|
| Commit | Possible sur bloc vert si scope respecte, diff coherent et validation OK. |
| PR | Possible sur bloc vert ou orange si la branche est propre et le rapport est clair. |
| Merge | Accord humain explicite pour l'instant. |
| Push `main` | Interdit. Passer par PR. |
| Orange / rouge | Pas de merge sans accord humain explicite. |
| Scope inattendu | Stopper, rapporter, attendre decision. |

## 9. Relation avec AGENTS.md et dcalc.rules

| Element | Role |
|---|---|
| `AGENTS.md` | Regles projet versionnees dans le repo. |
| `dcalc.rules` | Permissions runtime locales Codex, hors repo. |
| Cette spec | Politique projet vert/orange/rouge pour accelerer les blocs bornes. |

Ce bloc ne modifie pas `dcalc.rules`, les permissions locales, les secrets, ni le workflow GitHub.

## 10. Evolution future

Evolutions possibles seulement si elles reduisent vraiment la charge humaine ou augmentent les preuves:
- CI pour rejouer automatiquement les validations rapides;
- reviewers specialises par zone sensible;
- Agents SDK pour workflows repetables avec garde-fous;
- Agent Builder comme cockpit de supervision;
- templates de PR par classe de bloc.

Ne pas transformer cette spec en manuel lourd ni en architecture multi-agents complete sans besoin concret.
