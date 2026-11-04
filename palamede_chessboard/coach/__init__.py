"""The coach (#95, step 1) : what a human got wrong in the games they finished.

A process apart from the viewer and the MCP server walks the games
directory, diagnoses each finished game a human played in, and keeps a
report per game under ``<games_dir>/.coach/``. Nothing is hooked into the
five paths that end a game : like the Elo cache and the lessons notebook,
the coach is idempotent and catches up on its next round.

* :mod:`~palamede_chessboard.coach.store` — where reports and exercises
  live, and the key that says a game was already diagnosed.
* :mod:`~palamede_chessboard.coach.motifs` — why a move was a mistake.
* :mod:`~palamede_chessboard.coach.exercises` — the positions before the
  mistakes that make an exercise, checked with the engine.
* :mod:`~palamede_chessboard.coach.diagnosis` — the engine walk, the
  report, and one round over the games directory.
"""

#: How the coach's calls appear in the engine log.
CALLER = "coach"
