# Règles de conception

Ce document rassemble les décisions déjà prises par le propriétaire sur la conception de palamede et sur la façon d'y contribuer. Chaque règle renvoie à l'issue ou à la PR qui l'a posée. Une règle nouvelle entre ici par PR, avec cette référence.

Une règle convenue entre contributeurs, sans décision écrite du propriétaire, est marquée *(pratique d'équipe)*. Elle entre ici avec sa référence et cède devant une décision du propriétaire.

## 1. Une opération, les mêmes règles sur tous les médias (#328)

Partie, exercice et cours sont des **types de session**. Ce type ne dépend pas de l'interface qui l'a ouverte. Chaque interface a sa modalité :

| Média | Modalité |
|---|---|
| Tablier | **toujours une entrée de menu**, même sans échiquier connecté ; en plus, un bouton dans sa fenêtre quand c'est pertinent, et un geste au plateau quand l'échiquier est connecté (#335) |
| viewer | un bouton ou un menu |
| agent (LLM) | un outil MCP |

Toutes déclenchent **la même opération serveur**, avec les mêmes règles et les mêmes refus. Exemple : le lancement de la pendule passe par la touche du viewer, par `chess_clock_start`, et, au Tablier, par le menu « Partie ▸ Lancer la pendule », puis par le bouton et le geste (#330, #335).

En pratique :

- La règle vit dans le domaine (`chessboard_state.py`, `clock_moves.py`, `coach/`…), jamais dans une façade. Le routeur HTTP du viewer et le handler MCP appellent la même fonction et relaient son résultat. Exemple : `create_lesson`, appelée par `chess_create_lesson` et par `POST /api/lessons` (#329).
- Une fonction nouvelle dit, pour chaque média, comment elle s'y déclenche, ou pourquoi elle n'y est pas.
- Un test de parité vérifie que deux façades d'une même opération donnent le même résultat et les mêmes refus, mot pour mot.
- *(Pratique d'équipe, revue de #342.)* Une PR qui ne couvre qu'une partie des médias le dit, et renvoie à l'issue de suite pour les autres (par exemple #330 pour le serveur et le viewer, #335 pour le Tablier).
- Une exception propre à un média est une **transition**, pas une règle durable : elle porte la référence de l'issue qui la retire. Ainsi, les parties `tablier.*` gardent le départ de la pendule au premier coup jusqu'à l'étape 1 de #335 (l'entrée de menu).
- Le préfixe `tablier.` mêle aujourd'hui l'interface d'origine et des règles de jeu. Le découplage est ouvert dans #328 et sa forme n'est pas encore décidée. Ce document ne la fixe pas.

## 2. Les refus : un texte pour le lecteur, un code pour les programmes (#332, #340)

Un refus porte deux choses :

- **un texte** pour le joueur ou l'agent :
  - en français, en notation française (R, D, T, F, C) ;
  - sans UCI, sans numéro de demi-coup, sans détail interne ;
  - le même sur tous les médias (`detail` en HTTP, `reason` en MCP) ;
- **un code stable** pour les programmes et les tests. Un programme teste le code, **jamais le texte**.
  - En MCP, chaque refus porte son `code`.
  - En HTTP, l'en-tête `X-Refusal-Code` n'est posé aujourd'hui que par les refus d'identité (jeton absent ou invalide, pas votre trait, pas votre partie, registre illisible). Les autres refus du viewer, comme le 409 d'une partie terminée ou le 422 d'un coup illégal, n'ont que leur statut HTTP. L'en-tête n'est pas encore étendu à ces refus, et cette extension n'est pas encore décidée.

Le détail technique (raison de l'arbitre, demi-coup, camp) va au journal.

Les textes vivent dans `palamede_chessboard/refusals.py`, et le domaine les lève tels quels. Ajouter un refus, c'est ajouter son texte là, avec le test de parité de chaque média qui l'atteint.

**Un refus guide vers le bon geste.** Un coup légal mal écrit (SAN ambigu, prise sans `x`, échec sans `+`, mat sans `#`, ou l'inverse) n'est **jamais** dit « n'est pas possible » : l'agent jouerait autre chose au lieu de corriger sa notation. Le texte nomme la faute et, quand l'arbitre le sait, ce qu'il faut écrire (« Cd2 est ambigu : précisez la case de départ (Cfd2, Cbd2) »). « n'est pas possible » est réservé aux coups illégaux ou illisibles (#340).

## 3. Le découpage des PR (décision du propriétaire, 11/11)

Chaque PR coûte la même chose, quelle que soit sa taille : grille des dix points, double revue, smoke, release. D'où trois règles :

1. Les retouches cosmétiques ou mineures **attendent le prochain lot de leur zone**. Elles ne partent pas seules.
2. Une PR regroupe les issues ou sous-issues **d'un même thème** (même module, même média). Elle reste dans le budget de taille du dépôt (job `size`) et n'appelle qu'un seul smoke. **Pas de PR qui mélange serveur, viewer et Tablier.**
3. **Partent seuls** : un correctif d'anomalie ou urgent, la sécurité, une fonction décidée par le propriétaire.

Quand plusieurs auteurs travaillent en parallèle, chaque lot a un seul auteur, et la frontière entre lots se fait par fichier. Un recouvrement se signale avant de coder.

## 4. La revue

Tout APPROVE suit la grille des dix points d'outillages, [`docs/review-cheatsheet.md`](https://github.com/stephanejouve/outillages/blob/main/docs/review-cheatsheet.md) :

- chaque point est écrit sous son intitulé exact, avec ce qui a été fait et sa preuve ;
- un point non applicable est justifié ;
- un point qui hésite donne COMMENT ou REQUEST_CHANGES, pas APPROVE.

Quand un média n'est pas atteint par les tests, la revue le dit et le sonde. C'était le cas du viewer, qui envoie de l'UCI et n'atteint pas l'arbitre SAN strict (#340).

## 5. Les issues

Les modèles de `.github/ISSUE_TEMPLATE/` demandent, pour une anomalie comme pour une fonction, chaque média : MCP, viewer, l'entrée de menu du Tablier (toujours), et son bouton ou son geste quand c'est pertinent. Chaque case est cochée, ou déclarée hors champ avec la raison (règle 1).
