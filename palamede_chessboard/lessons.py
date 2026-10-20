"""Carnet cumulé des leçons extraites des analyses manuelles CD.

BT-006 V3 (palamede-chessboard#11) : CD produit manuellement — via sa
compétence ``analyse-partie-echecs`` — un fichier ``.md`` par partie
dans ``/Users/Shared/games/analyses/``. Chaque analyse contient une
section ``## Leçons`` où chaque leçon est préfixée d'un tag
``[joueur, coup]``. Ce module agrège ces sections dans un fichier
cumulé consultable (``lessons_cumulative.md`` par défaut) pour que les
joueurs puissent relire d'un coup d'œil les leçons apprises partie
après partie.

Contraintes CD (comment issue palamede#2) :

1. **Pas de pipeline hook** — reconstruction périodique + idempotente.
   :func:`aggregate_lessons` peut être rejoué sans dupliquer : chaque
   partie déjà présente dans l'output (marqueur ``## <game_id>``) est
   sautée.
2. **Parties sans résultat ignorées** — fair-play, pas d'analyse moteur
   en cours. Si le résultat extrait du ``.md`` est ``*`` (ou introuvable),
   toute la partie est skippée.
3. **Format tag strict** — chaque leçon doit matcher
   :data:`LESSON_LINE_RE` (``- [joueur, coup] texte``), sinon la ligne
   est ignorée (format libre non supporté, le carnet reste requêtable).

Design :

* :class:`Lesson` dataclass immuable (``game_id, player, move, text,
  source_md``).
* :func:`extract_lessons` lit un seul ``.md`` et renvoie ses leçons.
* :func:`aggregate_lessons` scanne un répertoire d'analyses, append
  idempotent, retourne le nombre de nouvelles leçons.
* L'extraction du ``game_id`` passe d'abord par l'URL viewer
  (``?game=<id>``) qui est la source la plus fiable du format .md
  (ligne « Visionneuse: ... »), avec fallback sur le stem du fichier
  si l'URL est absente.
* Le résultat fait foi **dans le PGN** de la partie (#46) quand
  ``games_dir`` est fourni et que le PGN existe : une analyse qui
  annonce « 1-0 » sur une partie encore en cours reste ignorée. Sans
  PGN, on retombe sur le ``.md`` : mention explicite ``Résultat <X>``,
  sinon le token de résultat à la fin de la section ``## Partie
  complète``.
* Robustesse (#46) : une analyse illisible (non UTF-8) est sautée avec
  un avertissement au lieu d'interrompre tout l'agrégat ; un carnet
  cumulé illisible lève :class:`LessonsError` (le relire comme vide
  ferait tout réajouter en double) ; lecture et ajout se font sous
  ``flock`` pour que deux exécutions simultanées ne dupliquent rien.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Résultats PGN qui comptent comme « partie terminée » — tout le reste
#: (notamment ``*``) fait skipper l'analyse entière, conformément à la
#: règle CD « pas d'extraction de leçons sur partie en cours ».
TERMINAL_RESULTS: frozenset[str] = frozenset({"1-0", "0-1", "1/2-1/2"})

#: Regex d'une ligne de leçon dans la section ``## Leçons``.
#: Format strict : ``- [joueur, coup] texte``.
#: - ``player`` : identifiant agent (``\w[\w-]*``, ex. ``junior``,
#:   ``claude-desktop``, ``tous``).
#: - ``move`` : chaîne libre jusqu'au ``]`` (ex. ``21...Db6``,
#:   ``chess_board_facts``).
#: - ``text`` : contenu de la leçon jusqu'à la fin de ligne.
LESSON_LINE_RE: re.Pattern[str] = re.compile(
    r"^- \[(?P<player>\w[\w-]*), (?P<move>[^\]]+)\] (?P<text>.+)$"
)

#: En-tête de la section « Leçons ». Markdown H2.
LESSONS_HEADING: str = "## Leçons"

#: Préfixe de toute en-tête H2 Markdown — utilisé pour détecter la fin
#: de la section « Leçons ».
H2_PREFIX: str = "## "

#: Regex d'extraction du ``game_id`` depuis l'URL de la visionneuse.
#: Ex. ``https://chess.alliancejr.eu/?game=cd-vs-junior-20261010&viewer=...``.
GAME_ID_URL_RE: re.Pattern[str] = re.compile(r"[?&]game=([A-Za-z0-9_-]+)")

#: Regex d'extraction du résultat d'une mention « Résultat X ».
#: Capture ``1-0``, ``0-1``, ``1/2-1/2`` ou ``*``.
RESULT_INLINE_RE: re.Pattern[str] = re.compile(r"[Rr]ésultat\s+(1-0|0-1|1/2-1/2|\*)")

#: Regex de dernier recours : le token terminal PGN en fin de section
#: « Partie complète » (last match sur toute la ligne).
RESULT_FALLBACK_RE: re.Pattern[str] = re.compile(r"\b(1-0|0-1|1/2-1/2)\b")

#: Nom par défaut du fichier cumulé.
DEFAULT_OUTPUT_FILENAME: str = "lessons_cumulative.md"


class LessonsError(RuntimeError):
    """The cumulative file cannot be read : nothing is appended."""


@dataclass(frozen=True)
class Lesson:
    """Une leçon unique extraite d'une analyse .md.

    Immuable (``frozen=True``) pour pouvoir être utilisée dans des
    ``set`` / ``dict`` sans surprise. Les cinq champs capturent tout ce
    que le carnet cumulé a besoin de rendre :

    * ``game_id`` : identifiant de la partie (ex. ``cd-vs-junior-20261010``).
      Sert aussi de clé d'idempotence côté :func:`aggregate_lessons`.
    * ``player`` : joueur à qui la leçon est adressée (``junior``,
      ``claude-desktop``, ``tous``, …) — forme brute extraite du tag.
    * ``move`` : coup / outil / situation visé par la leçon (``21...Db6``,
      ``chess_board_facts``, …).
    * ``text`` : contenu libre de la leçon.
    * ``source_md`` : chemin du fichier .md d'où la leçon provient,
      utile pour revenir à l'analyse complète depuis le carnet.
    """

    game_id: str
    player: str
    move: str
    text: str
    source_md: Path


def _extract_game_id(md_text: str, md_path: Path) -> str:
    """Return the game id for an analysis .md, with URL-first fallback.

    Priority :

    1. URL de la visionneuse (``?game=<id>``) — forme la plus stable,
       posée explicitement par CD dans la section Méthode.
    2. Stem du fichier .md — fallback silencieux ; le stem n'est PAS
       égal au ``game_id`` canonique (ex. stem ``analyse_claude-desktop
       -junior_20261010_093251`` vs id ``cd-vs-junior-20261010``) mais
       il reste unique et déterministe, donc utilisable comme clé
       d'idempotence si l'URL manque.
    """
    match = GAME_ID_URL_RE.search(md_text)
    if match:
        return match.group(1)
    return md_path.stem


def _extract_result(md_text: str) -> str:
    """Return the PGN result token for an analysis .md, or ``"*"`` if absent.

    CD rule 2 : parties sans résultat (``*``) sont ignorées. On
    cherche :

    1. Une mention inline ``Résultat 1-0`` (format stable des analyses
       CD, posé dans les premières lignes du .md).
    2. En dernier recours, un token terminal (``1-0`` / ``0-1`` /
       ``1/2-1/2``) sur la **dernière** ligne non vide — correspond à
       la fin de la section « Partie complète ».

    Retourne ``"*"`` si rien ne matche : :func:`extract_lessons` skippe
    alors l'analyse par convention.
    """
    inline = RESULT_INLINE_RE.search(md_text)
    if inline:
        return inline.group(1)
    for line in reversed(md_text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        fallback = RESULT_FALLBACK_RE.search(stripped)
        if fallback:
            return fallback.group(1)
        break
    return "*"


def _iter_lesson_lines(md_text: str) -> list[str]:
    """Return the raw lines contained in the ``## Leçons`` section.

    The section runs from :data:`LESSONS_HEADING` (exclusive) to the
    next H2 heading (exclusive) or end-of-file. Lines are kept as-is
    (no strip) so :data:`LESSON_LINE_RE` can anchor on ``- [``.
    Returns ``[]`` if the heading is missing.
    """
    lines = md_text.splitlines()
    try:
        start = lines.index(LESSONS_HEADING)
    except ValueError:
        return []
    section: list[str] = []
    for raw in lines[start + 1 :]:
        if raw.startswith(H2_PREFIX):
            break
        section.append(raw)
    return section


def _pgn_result(game_id: str, games_dir: Path) -> str | None:
    """Result tag of the game's PGN, or ``None`` if there is no such game.

    Imported lazily so ``import palamede_chessboard`` does not load the
    whole game-state stack (#46).
    """
    from palamede_chessboard.chessboard_state import GameState
    from palamede_chessboard.errors import ChessboardError

    try:
        return GameState.load(game_id, games_dir=games_dir).metadata.result
    except (ChessboardError, OSError, ValueError):
        return None


def extract_lessons(md_path: Path, games_dir: Path | None = None) -> list[Lesson]:
    """Return the list of :class:`Lesson` carried by one analysis ``.md``.

    * Returns ``[]`` if the file is missing, cannot be decoded, has no
      ``## Leçons`` section, or if its result is non-terminal (``*``) —
      CD rule 2. With ``games_dir``, the result comes from the game's
      PGN when it exists ; the ``.md`` is only the fallback.
    * Each line of the Leçons section is tested against
      :data:`LESSON_LINE_RE`. Non-matching lines (blank, prose,
      alternative formats) are silently skipped — the strict tag format
      is a feature, not a bug : it keeps the carnet grep-friendly.
    """
    try:
        md_text = md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        log.warning("lessons : %s n'est pas en UTF-8, ignoré (%s)", md_path, exc)
        return []
    except OSError:
        return []
    game_id = _extract_game_id(md_text, md_path)
    result = _pgn_result(game_id, games_dir) if games_dir is not None else None
    if result is None:
        result = _extract_result(md_text)
    if result not in TERMINAL_RESULTS:
        return []
    out: list[Lesson] = []
    for raw_line in _iter_lesson_lines(md_text):
        match = LESSON_LINE_RE.match(raw_line)
        if match is None:
            continue
        out.append(
            Lesson(
                game_id=game_id,
                player=match.group("player"),
                move=match.group("move"),
                text=match.group("text"),
                source_md=md_path,
            )
        )
    return out


#: Marqueur H2 d'une partie dans le fichier cumulé. ``aggregate_lessons``
#: scanne les lignes qui commencent par ce préfixe pour construire
#: l'ensemble des parties déjà consignées (clé d'idempotence).
GAME_HEADER_PREFIX: str = "## "


def _read_existing_game_ids(output: Path) -> set[str]:
    """Return the set of ``game_id`` already present in the cumulative file.

    An existing file is parsed line-by-line : every ``## <game_id>``
    heading contributes one entry. Returns ``set()`` if the file does
    not exist yet. A file that exists but cannot be read raises
    :class:`LessonsError` (#46) : reading it as empty would make the
    next append duplicate every game already in it.
    """
    if not output.exists():
        return set()
    try:
        text = output.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LessonsError(f"{output} est illisible ({exc}) : rien n'est ajouté") from exc
    seen: set[str] = set()
    for line in text.splitlines():
        if line.startswith(GAME_HEADER_PREFIX):
            seen.add(line[len(GAME_HEADER_PREFIX) :].strip())
    return seen


def _format_game_block(game_id: str, lessons: list[Lesson]) -> str:
    """Serialise a game's lessons as a Markdown block.

    Shape : ``## <game_id>\\n\\n- [player, move] text\\n...\\n``.
    All lessons share the same ``game_id`` by construction — the caller
    groups them before calling.
    """
    lines = [f"{GAME_HEADER_PREFIX}{game_id}", ""]
    for lesson in lessons:
        lines.append(f"- [{lesson.player}, {lesson.move}] {lesson.text}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _group_by_game(lessons: list[Lesson]) -> dict[str, list[Lesson]]:
    """Group lessons by ``game_id`` while preserving insertion order."""
    grouped: dict[str, list[Lesson]] = {}
    for lesson in lessons:
        grouped.setdefault(lesson.game_id, []).append(lesson)
    return grouped


@contextlib.contextmanager
def _locked(output: Path) -> Iterator[None]:
    """Exclusive ``flock`` on ``<output>.lock`` for the read + append (#46)."""
    lock_path = output.with_name(output.name + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def aggregate_lessons(analyses_dir: Path, output: Path, games_dir: Path | None = None) -> int:
    """Scan ``analyses_dir`` for ``*.md`` and append new lessons to ``output``.

    Idempotent — a second invocation without new ``.md`` files writes
    nothing and returns ``0``. The idempotence key is the ``game_id``
    extracted from each analysis : if a ``## <game_id>`` heading
    already lives in ``output``, that analysis is skipped. This
    deliberately avoids per-lesson diffs so a .md edit (fixing a typo
    in a lesson) does NOT propagate — the user would need to delete
    the corresponding block from the cumulative file and re-run.

    * ``.md`` files are scanned in sorted order so the cumulative file
      grows deterministically.
    * ``games_dir`` : where the PGN live, so the result is read there
      (see :func:`extract_lessons`).
    * Parent directory of ``output`` is created if needed. Reading the
      existing game ids and appending happen under one ``flock``.
    * Returns the count of newly appended lessons (0 if nothing to do).
    """
    if not analyses_dir.exists():
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with _locked(output):
        return _aggregate_locked(analyses_dir, output, games_dir)


def _aggregate_locked(analyses_dir: Path, output: Path, games_dir: Path | None) -> int:
    """Body of :func:`aggregate_lessons`, run under the output lock."""
    existing_game_ids = _read_existing_game_ids(output)
    new_lessons: list[Lesson] = []
    for md_path in sorted(analyses_dir.glob("*.md")):
        lessons = extract_lessons(md_path, games_dir)
        if not lessons:
            continue
        # Every lesson from a single .md shares the same game_id by
        # construction (extract_lessons stamps it uniformly).
        game_id = lessons[0].game_id
        if game_id in existing_game_ids:
            continue
        new_lessons.extend(lessons)
        existing_game_ids.add(game_id)
    if not new_lessons:
        return 0
    blocks = [
        _format_game_block(game_id, group) for game_id, group in _group_by_game(new_lessons).items()
    ]
    with output.open("a", encoding="utf-8") as f:
        for block in blocks:
            f.write(block)
    return len(new_lessons)
