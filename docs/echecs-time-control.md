# Time control du tournoi des agents

Doc courte (chantier ELO volet 3a, 2026-10-05) sur la cadence
appliquée aux parties entre les quatre joueurs (admin, Junior,
Leader, CD).

## Décision

**Cadence par défaut = `"30+30"`** (30 min base + 30 sec Fischer
increment, format `M+S`). Cadence « classique » pour une partie
par correspondance sur plusieurs créneaux, avec un increment
suffisant pour éviter le flag-fall sur un coup lent (connexion,
réveil session).

La constante vit dans
[`palamede_chessboard/chessboard_state.py`](../palamede_chessboard/chessboard_state.py)
sous `DEFAULT_TIME_CONTROL_FORMAT`. Un changement de cadence passe
par PR (double APPROVE palamede-chessboard).

## Comportement

- `TimeControl()` sans argument retourne maintenant `"30+30"`
  (pré-volet-3a : `"unrated"`).
- `chess_create_game` (MCP) accepte toujours un paramètre
  `time_control` explicite — passer `{"format": "unrated"}` désactive
  le clock, utile pour les tests ou les parties non-tournoi.
- `GameState.load()` sur un PGN **sans** header `TimeControl` renvoie
  `"unrated"` (rétrocompat historique, 2.pgn du passé n'avaient pas
  de clock).
- Côté UI (`chessboard_viewer`) : le bloc `#clocks` reste masqué tant
  que `/api/clock` retourne `null` (= TimeControl `"unrated"`). Les
  nouvelles parties avec default `"30+30"` affichent le clock
  automatiquement avec countdown live (dérive serveur, low-time
  warning sous 30 sec).

## Autres cadences standards

Reconnues par le parser `M+S` dans `chessboard_clock.py` :

| Format    | Base   | Increment | Usage                              |
|-----------|--------|-----------|------------------------------------|
| `5+3`     | 5 min  | 3 sec     | Rapide (test, debug clock)         |
| `15+10`   | 15 min | 10 sec    | Rapide actif                       |
| `30+30`   | 30 min | 30 sec    | **Default tournoi**                |
| `60+30`   | 60 min | 30 sec    | Long correspondance                |
| `unrated` | —      | —         | Pas de clock (parties non-rated)   |

Les formats non reconnus (`"unlimited"`, `"40/7200:0+10"`, etc.)
désactivent le clock (fallback dans `ClockState.init_from_time_control`).

## En-tête PGN (#151)

Le format interne reste `M+S` en minutes. Le PGN, lui, suit la norme et
compte en **secondes** :

| Partie                    | En-têtes écrits                                          |
|---------------------------|----------------------------------------------------------|
| `15+30`                   | `[TimeControl "900+30"]` `[TimeControlUnit "seconds"]`   |
| `unrated`                 | `[TimeControl "-"]`                                      |
| `40/7200:0+10` (import)   | `[TimeControl "40/7200:0+10"]`, recopié tel quel         |

`TimeControlUnit "seconds"` est le marqueur : les parties écrites avant
#151 portent des minutes (`15+30`), et `60+0` vaut une minute dans une
forme, une heure dans l'autre. À la lecture, seul un `TimeControl`
accompagné du marqueur est lu en secondes ; sans lui, il est lu en
minutes comme avant. Pas de migration des fichiers : une partie
ancienne passe au format en secondes à sa prochaine écriture. Sans
en-tête ou avec `-`, la partie est `unrated`.

## Impact Elo

Le header `TimeControl` est **indépendant** du header `Rated`. Le
classement Elo ne regarde que `Rated "true"` pour inclure une partie
dans le calcul (cf [carnet de leçons](echecs-carnet-de-lecons.md) +
`palamede_chessboard/chess_elo/`). Une partie `TimeControl "30+30"` sans
`Rated "true"` reste hors du classement.
