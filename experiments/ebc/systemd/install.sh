#!/usr/bin/env bash
# Installiert die EBC-Timer als systemd-User-Units (wie deploy/systemd/install.sh des Bots). Aufruf: bash install.sh [--uninstall]
set -euo pipefail
D="$HOME/.config/systemd/user"; H="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$D" "$HOME/ebc_exp/logs"
if [ "${1:-}" = "--uninstall" ]; then
  systemctl --user disable --now ebc-stage-b.timer ebc-spreads.timer || true
  rm -f "$D"/ebc-stage-b.* "$D"/ebc-spreads.*; systemctl --user daemon-reload; exit 0
fi
cp "$H"/ebc-*.service "$H"/ebc-*.timer "$D"/
systemctl --user daemon-reload
systemctl --user enable --now ebc-stage-b.timer ebc-spreads.timer
systemctl --user list-timers 'ebc-*' --no-pager
