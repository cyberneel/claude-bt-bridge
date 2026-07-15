#!/usr/bin/env bash
# Live one-screen status for the claude-bt-bridge link (runs on the Linux laptop).
#   ./monitor.sh            # refresh every 2s, Ctrl-C to quit
#   ./monitor.sh 5          # refresh every 5s
# Env:
#   BT_LOG_DIR  where the proxy/bridge logs live (default ~/.local/state/claude-bt-bridge)
set -u
LOGDIR="${BT_LOG_DIR:-$HOME/.local/state/claude-bt-bridge}"
PROXY_LOG="$LOGDIR/revproxy.log"
BRIDGE_LOG="$LOGDIR/btbridge.log"
INT="${1:-2}"

g(){ printf '\033[32m%s\033[0m' "$1"; }   # green
r(){ printf '\033[31m%s\033[0m' "$1"; }   # red
hr(){ printf '%s\n' "──────────────────────────────────────────────────────"; }

trap 'printf "\033[?25h"; exit 0' INT TERM   # restore cursor on quit
printf '\033[?25l'                           # hide cursor

while true; do
  clear
  printf "claude-bt-bridge   %s   (every %ss, Ctrl-C to quit)\n" "$(date '+%H:%M:%S')" "$INT"
  hr

  ss -tlnH 2>/dev/null | grep -q '127.0.0.1:8080' && P=$(g UP) || P=$(r DOWN)
  pgrep -f '[b]t-bridge-linux' >/dev/null 2>&1 && B=$(g UP) || B=$(r DOWN)
  printf "reverse proxy : %s\t\tbt bridge : %s\n" "$P" "$B"

  # Derive link state from the bridge log. bluetoothctl 'Connected' lies for idle
  # SPP (the ACL tears down between bursts), so trust the RFCOMM open/close events.
  LAST=$(tail -n 200 "$BRIDGE_LOG" 2>/dev/null | grep -aE '\[mux\] link (up|down)|\[bt\] (connection|disconnect)' | tail -1)
  case "$LAST" in
    *"link up"*|*connection*)   LINK=$(g UP) ;;
    *"link down"*|*disconnect*) LINK=$(r DOWN) ;;
    *)                          LINK="?" ;;
  esac
  printf "BT link       : %s   (last: %s)\n" "$LINK" "${LAST:-none}"

  # compression ratio, from the bridge's periodic [stats] line
  COMP=$(tail -n 200 "$BRIDGE_LOG" 2>/dev/null | grep -a '\[stats\]' | tail -1 | sed 's/.*\[stats\] //')
  printf "compression   : %s\n" "${COMP:-<no traffic yet>}"
  hr

  if [ -f "$PROXY_LOG" ]; then
    # cumulative totals (whole file). Fine while the log stays modest; if you ever
    # make logging durable and it grows huge, switch these to a `tail -n N` window.
    tot=$(grep -ac 'v1/messages' "$PROXY_LOG" 2>/dev/null || echo 0)
    ok=$(grep -aEc 'v1/messages.*-> 2' "$PROXY_LOG" 2>/dev/null || echo 0)
    bad=$(grep -aEc 'v1/messages.*-> (4|5)' "$PROXY_LOG" 2>/dev/null || echo 0)
    printf "API calls (total): %s   %s   %s\n" "$tot" "$(g "2xx:$ok")" "$(r "4xx/5xx:$bad")"
    hr
    echo "recent requests:"
    tail -n 8 "$PROXY_LOG" 2>/dev/null | sed 's/^/  /'
  else
    printf "no proxy log at %s\n" "$PROXY_LOG"
  fi

  sleep "$INT"
done
