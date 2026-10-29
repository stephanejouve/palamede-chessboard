"""Maia bot for palamède (#93, PR 2).

Plays the moves of the ``bot-maia-<elo>`` accounts : lc0 with the Maia
weights of that level, one node per move, sent through the viewer API with
the account's own token. A separate process and image : palamède embeds
nothing of lc0 nor of the weights (GPL-3.0).
"""

__version__ = "0.1.0"  # x-release-please-version
