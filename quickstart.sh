#!/usr/bin/env bash
#
# One-command quickstart for the WDIO Report Viewer.
#
# It will:
#   1. make sure Docker is installed and the daemon is running
#      (starting Docker Desktop for you on macOS and waiting for it),
#   2. build and start the app with docker compose,
#   3. wait until the app is actually serving,
#   4. open the UI in your browser.
#
# Usage:
#   ./quickstart.sh                       # start + open (watches ./reports)
#   ./quickstart.sh /path/to/report.json  # track THAT file live, in place
#   ./quickstart.sh /path/to/reports/     # watch a whole folder live
#   ./quickstart.sh down                  # stop, remove container/image, quit Docker Desktop
#   ./quickstart.sh logs                  # follow the app logs
#
set -euo pipefail

cd "$(dirname "$0")"

URL="http://localhost:8501"
HEALTH="${URL}/_stcore/health"
REPORTS_DIR="reports"

info()  { printf '\033[0;36m•\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m✔\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m!\033[0m %s\n' "$*"; }
die()   { printf '\033[0;31m✖\033[0m %s\n' "$*" >&2; exit 1; }

open_url() {
  if [[ "$(uname)" == "Darwin" ]]; then
    open "$1" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$1" >/dev/null 2>&1 || true
  else
    info "Open this in your browser: $1"
  fi
}

# --- optional subcommands ----------------------------------------------------
compose_cmd() {
  if docker compose version >/dev/null 2>&1; then echo "docker compose";
  elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose";
  else die "docker compose isn't available. Install/upgrade Docker."; fi
}

quit_docker_desktop() {
  # Only Docker Desktop (macOS) is something we start, so it's the only thing we
  # stop. On Linux the daemon is managed by the OS — leave it alone.
  [[ "$(uname)" == "Darwin" ]] || return 0
  pgrep -x "Docker Desktop" >/dev/null 2>&1 || pgrep -x Docker >/dev/null 2>&1 || return 0
  info "Quitting Docker Desktop…"
  osascript -e 'quit app "Docker Desktop"' >/dev/null 2>&1 \
    || osascript -e 'quit app "Docker"' >/dev/null 2>&1 \
    || killall "Docker Desktop" >/dev/null 2>&1 \
    || killall Docker >/dev/null 2>&1 \
    || warn "Couldn't quit Docker Desktop automatically — quit it from the menu bar."
}

teardown() {
  local compose; compose="$(compose_cmd)"
  if docker info >/dev/null 2>&1; then
    info "Stopping the app and removing its container, network, and built image…"
    # --rmi local drops the image we built; --volumes/--remove-orphans clear the
    # rest. The ./reports folder is a host bind mount, so it is never touched.
    $compose down --rmi local --volumes --remove-orphans || warn "compose down reported an issue."
  else
    warn "Docker daemon isn't running — nothing to stop."
  fi
  quit_docker_desktop
  ok "Torn down. Everything we started is stopped."
}

case "${1:-up}" in
  down) teardown; exit 0 ;;
  logs) exec $(compose_cmd) logs -f ;;
  up)   TARGET="${2:-}" ;;                     # ./quickstart.sh up [file|dir]
  -*)   die "Unknown option: $1 (use: up [file|dir] | down | logs)" ;;
  *)    TARGET="$1" ;;                          # ./quickstart.sh <file|dir>
esac

# --- 1. Docker present -------------------------------------------------------
command -v docker >/dev/null 2>&1 || die \
  "Docker isn't installed. Get Docker Desktop: https://www.docker.com/products/docker-desktop/"

# --- 2. Docker daemon running ------------------------------------------------
if ! docker info >/dev/null 2>&1; then
  info "Docker daemon isn't running."
  if [[ "$(uname)" == "Darwin" ]]; then
    info "Starting Docker Desktop…"
    open -a Docker >/dev/null 2>&1 || die \
      "Couldn't launch Docker Desktop. Start it manually, then re-run ./quickstart.sh"
  else
    die "Start the Docker daemon, then re-run ./quickstart.sh"
  fi

  printf '  waiting for Docker to be ready'
  for _ in $(seq 1 90); do
    if docker info >/dev/null 2>&1; then printf ' ready\n'; break; fi
    printf '.'; sleep 2
  done
  docker info >/dev/null 2>&1 || die \
    "Docker didn't come up in time. Open Docker Desktop, let it finish starting, then re-run."
fi
ok "Docker is running."

# --- 3. Decide what to track, then build + start -----------------------------
COMPOSE="$(compose_cmd)"
if [[ -n "${TARGET:-}" ]]; then
  if [[ -f "$TARGET" ]]; then
    # Track a single file live. We mount its *folder* (not the file itself) so
    # that atomic rewrites — write-temp-then-rename, which most test runners do —
    # are still seen, and point REPORT_FILE at it inside the container.
    REPORTS_HOST_DIR="$(cd "$(dirname "$TARGET")" && pwd)"
    REPORT_FILE_IN_CONTAINER="/data/$(basename "$TARGET")"
    export REPORTS_HOST_DIR REPORT_FILE_IN_CONTAINER
    info "Tracking this file live, in place: $REPORTS_HOST_DIR/$(basename "$TARGET")"
  elif [[ -d "$TARGET" ]]; then
    REPORTS_HOST_DIR="$(cd "$TARGET" && pwd)"
    export REPORTS_HOST_DIR
    info "Watching this folder live, in place: $REPORTS_HOST_DIR"
  else
    die "Not a file or folder: $TARGET"
  fi
else
  mkdir -p "$REPORTS_DIR"
  REPORTS_HOST_DIR="$(cd "$REPORTS_DIR" && pwd)"
  export REPORTS_HOST_DIR
  info "Watching the default folder live: $REPORTS_HOST_DIR"
fi
info "Building and starting the app (first run pulls/builds, so give it a moment)…"
$COMPOSE up --build -d

# --- 4. Wait for the app to serve -------------------------------------------
printf '  waiting for the app'
ready=""
for _ in $(seq 1 60); do
  if curl -fsS "$HEALTH" >/dev/null 2>&1; then ready=1; printf ' ready\n'; break; fi
  printf '.'; sleep 1
done
if [[ -z "$ready" ]]; then
  warn "The app didn't answer the health check yet — it may still be starting."
  warn "Check logs with: $COMPOSE logs -f"
fi

# --- 5. Open the browser -----------------------------------------------------
open_url "$URL"

echo
ok "Running at $URL"
if [[ -n "${REPORT_FILE_IN_CONTAINER:-}" ]]; then
  echo "    • Tracking file live: $REPORTS_HOST_DIR/$(basename "$REPORT_FILE_IN_CONTAINER")"
  echo "      As your tests rewrite that file, the viewer auto-refreshes."
else
  echo "    • Watching live: $REPORTS_HOST_DIR"
  echo "      Update report JSONs there and the viewer auto-refreshes — pick one in the sidebar."
fi
echo "    • Track a specific file: ./quickstart.sh /path/to/other-repo/reports/exec.json"
echo "    • Follow logs:  ./quickstart.sh logs"
echo "    • Stop + clean: ./quickstart.sh down   (removes the container/image and quits Docker Desktop)"
