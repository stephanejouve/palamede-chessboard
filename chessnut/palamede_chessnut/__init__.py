"""Chessnut Air+ electronic board, played over Bluetooth LE without the vendor app.

Pure layers first, I/O last :

* :mod:`.protocol` — frame decoding (piece placement) and LED frames ;
* :mod:`.moves` — which legal move turns the game position into the one
  observed on the physical board ;
* :mod:`.leds` — LED animations (board ready, game over, resignation) ;
* :mod:`.viewer` — the viewer's HTTP API, standard library only ;
* :mod:`.bridge` — the loop between the board and a game served by the
  viewer (SSE stream in, ``POST /api/games/<id>/move`` and ``/resign`` out) ;
* :mod:`.cli` — the ``palamede-chessnut`` command.

The protocol was observed on an Air+ (2026-10-23) ; it matches what the
community documented for the Air. This package installs on its own
(``chess`` and ``bleak``), without the palamède server stack ; ``bleak``
is imported only when the board is reached.
"""
