# syntax=docker/dockerfile:1
# palamede-chessboard — image unique (BT-008, #28).
#
# Les services de la maquette (MCP Streamable HTTP, viewer ; arbitre plus tard)
# partagent cette image et ne diffèrent que par leur commande.
# Dépendances figées sur poetry.lock (pas de résolution « au plus récent »).
# Utilisateur non root ; parties dans le volume /data/games.

FROM python:3.11-slim-bookworm

# Stockfish de Debian : /usr/games/stockfish (hors PATH par défaut).
RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml poetry.lock README.md LICENSE ./
COPY palamede_chessboard ./palamede_chessboard

# Contraintes = versions exactes de poetry.lock, puis installation du paquet.
RUN python - <<'EOF' > /tmp/constraints.txt
import tomllib
for p in tomllib.load(open("poetry.lock", "rb"))["package"]:
    print(f"{p['name']}=={p['version']}")
EOF
RUN pip install --no-cache-dir --constraint /tmp/constraints.txt . \
    && rm -rf /src /tmp/constraints.txt

RUN useradd --create-home --uid 10001 palamede \
    && mkdir -p /data/games && chown palamede /data/games

ENV MCP_CHESSBOARD_GAMES_DIR=/data/games \
    MCP_CHESSBOARD_STOCKFISH=/usr/games/stockfish \
    PYTHONUNBUFFERED=1

USER palamede
WORKDIR /home/palamede
VOLUME ["/data/games"]

# Par défaut : serveur MCP en Streamable HTTP (le compose surcharge par service).
ENV MCP_CHESSBOARD_TRANSPORT=http \
    MCP_CHESSBOARD_HTTP_HOST=0.0.0.0 \
    MCP_CHESSBOARD_HTTP_PORT=8770
EXPOSE 8770 8765
CMD ["palamede-chessboard-server"]
