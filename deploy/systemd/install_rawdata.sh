#!/usr/bin/env bash
# Installiert Rohdaten-Rekorder und Load-Manager als systemd-User-Timer. Aufruf: bash install_rawdata.sh [--uninstall]
set -euo pipefail
D="$HOME/.config/systemd/user"; H="$(cd "$(dirname "$0")" && pwd)"
U="etoro-raw-rates etoro-raw-candles etoro-loadmgr etoro-catalog-fill"
mkdir -p "$D" "$HOME/etoro_nautilus/logs" "$HOME/etoro_nautilus/results/loadmgr"
if [ "${1:-}" = "--uninstall" ]; then
  for u in $U; do systemctl --user disable --now "$u.timer" || true; rm -f "${D:?}/${u:?}.service" "${D:?}/${u:?}.timer"; done
  systemctl --user daemon-reload; exit 0
fi
for u in $U; do cp "$H/$u.service" "$H/$u.timer" "$D/"; done
systemctl --user daemon-reload
for u in $U; do systemctl --user enable --now "$u.timer"; done
systemctl --user list-timers 'etoro-raw-*' 'etoro-loadmgr*' 'etoro-catalog-fill*' --no-pager
