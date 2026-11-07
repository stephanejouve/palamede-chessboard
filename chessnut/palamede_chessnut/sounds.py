"""The board's sound code (#186, owner 07/11) : a few beeps say what was understood.

The Air+ has no colour to command (essays v1 to v4, 07/11) : the green of
« validated » became a code of beeps, with the buzzer of Chessnut's own SDK
(EasyLinkSDK, MIT, ``ChessLink::beep``) : ``0b 04`` then the frequency (Hz)
and the duration (ms), each on two bytes, high byte first.

* two high beeps : OK, the gesture or the action is understood ;
* two low beeps : no, refused ;
* a low then a high beep : a question, a confirmation is awaited.

1000 and 2000 Hz, an octave apart, below where the buzzer merges near
notes (essay v6) ; 80 ms each, 20 ms being still heard (essay v5 bis). The
table holds a frequency and a duration per beep : the duration is a lever
too (owner, 07/11).
"""

from __future__ import annotations

BEEP_HEADER = bytes([0x0B, 0x04])
LOW, HIGH = 1000, 2000
SHORT = 80

#: Series → its beeps, ``(Hz, ms)`` each.
SERIES: dict[str, tuple[tuple[int, int], ...]] = {
    "ok": ((HIGH, SHORT), (HIGH, SHORT)),
    "no": ((LOW, SHORT), (LOW, SHORT)),
    "question": ((LOW, SHORT), (HIGH, SHORT)),
}

#: Ticks of the bridge's loop (0.1 s) from one beep to the next : 80 ms of
#: sound, then silence, so that two beeps are heard as two.
_TICKS_APART = 2


def beep(frequency: int, duration: int) -> bytes:
    """The buzzer's command : ``0b 04``, frequency and duration, big-endian."""
    return BEEP_HEADER + frequency.to_bytes(2, "big") + duration.to_bytes(2, "big")


def frames(series: str) -> list[bytes | None]:
    """One entry per tick : a beep to send, or ``None`` to wait."""
    out: list[bytes | None] = []
    for frequency, duration in SERIES[series]:
        out += [beep(frequency, duration)] + [None] * (_TICKS_APART - 1)
    return out[: len(out) - (_TICKS_APART - 1)]
