#!/usr/bin/env bash
# Idempotent install du service `palamede-chess-arbiter-daemon` en
# LaunchAgent macOS, pour une partie donnée (``--game-id``) avec sa
# config JSON (``--config``).
#
# But : lancer l'arbitre out-of-session, sans dépendre d'une session
# Claude active. Le service redémarre automatiquement au login + reste
# vivant tant que l'utilisateur est logué (``KeepAlive`` sous launchd).
#
# Actions :
#   1. Preflight : vérifie que `palamede-chess-arbiter-daemon` est dans
#      PATH (installé via `poetry install` ou `pipx install`).
#   2. Génère un plist dans ~/Library/LaunchAgents/
#      com.magma.palamede-chess-arbiter-daemon.<game_id>.plist à partir
#      du template examples/launchagents/.
#   3. `launchctl bootstrap gui/<uid>` du plist (bootout + bootstrap
#      pour idempotence : rejouer == recharger la nouvelle config).
#
# Note (CD msg 57628) : le raccord `point d'entrée MCP server` est hors
# scope BT-005 et sera posé après le merge BT-004 Admin.
#
# Usage :
#   install-chess-arbiter-daemon.sh --game-id admin-vs-leader-004 \
#       --config /Users/Shared/games/arbiter-004.config.json
#   install-chess-arbiter-daemon.sh --game-id ... --config ... --dry-run
#   install-chess-arbiter-daemon.sh --uninstall --game-id ...
#
# Idempotence : relancer avec les mêmes args = ``launchctl bootstrap -k``
# qui kill + reload. Pas de duplication côté plist.
#
# Exit codes :
#   0 = OK
#   1 = argument invalide
#   2 = preflight failed (daemon binary not in PATH)
#   3 = launchctl bootstrap failed

set -euo pipefail

# --- defaults & args ---------------------------------------------------------
GAME_ID=""
CONFIG_PATH=""
DRY_RUN=0
UNINSTALL=0
DAEMON_BIN="${DAEMON_BIN:-$(command -v palamede-chess-arbiter-daemon || true)}"
LAUNCHAGENTS_DIR="${LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd -P)}"
TEMPLATE="${TEMPLATE:-$REPO_ROOT/examples/launchagents/com.magma.palamede-chess-arbiter-daemon.plist}"

usage() {
  cat <<'EOF'
usage: install-chess-arbiter-daemon.sh --game-id <id> --config <path>
       install-chess-arbiter-daemon.sh --uninstall --game-id <id>
       install-chess-arbiter-daemon.sh [...] --dry-run

Install (par défaut) : génère un plist LaunchAgent et bootstrap le
service palamede-chess-arbiter-daemon pour la partie <game-id>, en
pointant vers le fichier de configuration <config>.

Options :
  --game-id <id>   Identifiant de partie (obligatoire). Sert à nommer
                   le plist et à distinguer plusieurs instances.
  --config <path>  Chemin vers le fichier JSON de config du daemon
                   (obligatoire sauf en --uninstall).
  --dry-run        Affiche le plist qui serait écrit, sans toucher
                   au FS ni au launchctl.
  --uninstall      bootout + rm du plist pour <game-id>.
  --help           Affiche cet usage.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --game-id) GAME_ID="$2" ; shift 2 ;;
    --config) CONFIG_PATH="$2" ; shift 2 ;;
    --dry-run) DRY_RUN=1 ; shift ;;
    --uninstall) UNINSTALL=1 ; shift ;;
    --help|-h) usage ; exit 0 ;;
    *) echo "install-chess-arbiter-daemon: option inconnue: $1" >&2 ; usage >&2 ; exit 1 ;;
  esac
done

if [ -z "$GAME_ID" ]; then
  echo "install-chess-arbiter-daemon: --game-id obligatoire" >&2
  usage >&2
  exit 1
fi

PLIST_LABEL="com.magma.palamede-chess-arbiter-daemon.${GAME_ID}"
PLIST_PATH="${LAUNCHAGENTS_DIR}/${PLIST_LABEL}.plist"
UID_VAL="$(id -u)"

if [ "$UNINSTALL" -eq 1 ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] launchctl bootout gui/${UID_VAL}/${PLIST_LABEL}"
    echo "[dry-run] rm -f ${PLIST_PATH}"
    exit 0
  fi
  launchctl bootout "gui/${UID_VAL}/${PLIST_LABEL}" 2>/dev/null || true
  rm -f "$PLIST_PATH"
  echo "install-chess-arbiter-daemon: uninstalled ${PLIST_LABEL}"
  exit 0
fi

if [ -z "$CONFIG_PATH" ]; then
  echo "install-chess-arbiter-daemon: --config obligatoire pour install" >&2
  exit 1
fi

if [ -z "$DAEMON_BIN" ]; then
  echo "install-chess-arbiter-daemon: palamede-chess-arbiter-daemon introuvable dans PATH" >&2
  echo "  installez-le d'abord : cd palamede-chessboard && poetry install" >&2
  echo "  ou : pipx install palamede-chessboard" >&2
  exit 2
fi

if [ ! -f "$TEMPLATE" ]; then
  echo "install-chess-arbiter-daemon: template introuvable: $TEMPLATE" >&2
  exit 2
fi

PLIST_CONTENT="$(sed \
    -e "s|__LABEL__|${PLIST_LABEL}|g" \
    -e "s|__DAEMON_BIN__|${DAEMON_BIN}|g" \
    -e "s|__CONFIG_PATH__|${CONFIG_PATH}|g" \
    -e "s|__HOME__|${HOME}|g" \
    "$TEMPLATE")"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] plist path: ${PLIST_PATH}"
  echo "[dry-run] plist content:"
  echo "${PLIST_CONTENT}"
  exit 0
fi

mkdir -p "$LAUNCHAGENTS_DIR"
printf '%s\n' "$PLIST_CONTENT" > "$PLIST_PATH"

launchctl bootout "gui/${UID_VAL}/${PLIST_LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_VAL}" "$PLIST_PATH"

echo "install-chess-arbiter-daemon: installed ${PLIST_LABEL} → ${PLIST_PATH}"
echo "  config: ${CONFIG_PATH}"
echo "  daemon: ${DAEMON_BIN}"
echo "  logs  : ~/Library/Logs/${PLIST_LABEL}.out.log + .err.log"
