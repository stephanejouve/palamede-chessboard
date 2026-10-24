"""Chessnut Air+ electronic board, played over Bluetooth LE without the vendor app.

Three layers, only the last one does I/O :

* :mod:`.protocol` — frame decoding (piece placement) and LED frames ;
* :mod:`.moves` — which legal move turns the game position into the one
  observed on the physical board ;
* :mod:`.bridge` — the loop between the board and a game served by the
  viewer (SSE stream in, ``POST /api/games/<id>/move`` out).

The protocol was observed on an Air+ (2026-10-23) ; it matches what the
community documented for the Air. ``bleak`` is an optional dependency
(extra ``chessnut``) imported only by the bridge.
"""
