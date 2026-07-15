#!/usr/bin/env bash
# Live one-screen status for the claude-bt-bridge link (runs on the Linux laptop).
# Reads logs from the systemd user services via journald.
#   ./monitor.sh            # refresh every 2s, Ctrl-C to quit
#   ./monitor.sh 5          # refresh every 5s
set -u
BRIDGE_UNIT=claude-bt-bridge
PROXY_UNIT=claude-bt-proxy
INT="${1:-2}"

blog(){ journalctl --user -u "$BRIDGE_UNIT" -o cat --no-pager "$@" 2>/dev/null; }
plog(){ journalctl --user -u "$PROXY_UNIT"  -o cat --no-pager "$@" 2>/dev/null; }
g(){ printf '\033[32m%s\033[0m' "$1"; }
r(){ printf '\033[31m%s\033[0m' "$1"; }
hr(){ printf '%s\n' "──────────────────────────────────────────────────────"; }

trap 'printf "\033[?25h"; exit 0' INT TERM
printf '\033[?25l'

while true; do
  clear
  printf "claude-bt-bridge   %s   (every %ss, Ctrl-C to quit)\n" "$(date '+%H:%M:%S')" "$INT"
  hr

  systemctl --user is-active "$PROXY_UNIT"  >/dev/null 2>&1 && P=$(g UP) || P=$(r DOWN)
  systemctl --user is-active "$BRIDGE_UNIT" >/dev/null 2>&1 && B=$(g UP) || B=$(r DOWN)
  printf "reverse proxy : %s\t\tbt bridge : %s\n" "$P" "$B"

  BR=$(blog -n 300)
  # link state from the RFCOMM open/close events (bluetoothctl 'Connected' lies for idle SPP)
  LAST=$(grep -aE '\[mux\] link (up|down)|\[bt\] (connection|disconnect)' <<<"$BR" | tail -1)
  case "$LAST" in
    *"link up"*|*connection*)   LINK=$(g UP) ;;
    *"link down"*|*disconnect*) LINK=$(r DOWN) ;;
    *)                          LINK="?" ;;
  esac
  printf "BT link       : %s   (last: %s)\n" "$LINK" "${LAST:-none}"

  # compression (response/download direction; Anthropic gzips responses, so this runs low)
  COMP=$(grep -a '\[stats\]' <<<"$BR" | tail -1 | sed 's/.*\[stats\] //')
  printf "compression   : %s\n" "${COMP:-<no traffic yet>}   [download dir; requests compress ~2x, unshown]"
  hr

  PR=$(plog)
  tot=$(grep -ac 'v1/messages' <<<"$PR"); ok=$(grep -aEc 'v1/messages.*-> 2' <<<"$PR"); bad=$(grep -aEc 'v1/messages.*-> (4|5)' <<<"$PR")
  printf "API calls (total): %s   %s   %s\n" "$tot" "$(g "2xx:$ok")" "$(r "4xx/5xx:$bad")"
  hr
  echo "recent requests:"
  plog -n 8 | sed 's/^/  /'

  sleep "$INT"
done
