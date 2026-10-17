#!/usr/bin/env bash
# Installiert und aktiviert die drei täglichen Timer (Stage-C monitor 06:50 UTC, Gesundheitscheck 06:55 UTC, Kalibrierung 07:05 UTC).
# Idempotent, ohne sudo; ändert keine bestehenden Units.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$dest"
for f in etoro-daily-health.service etoro-daily-health.timer ebc-stagec-monitor.service ebc-stagec-monitor.timer etoro-calibrate.service etoro-calibrate.timer; do
  install -m 0644 "$here/$f" "$dest/$f"
  echo "installiert: $dest/$f"
done
systemctl --user daemon-reload
systemctl --user enable --now etoro-daily-health.timer ebc-stagec-monitor.timer etoro-calibrate.timer
systemctl --user list-timers 'etoro-daily-health.timer' 'ebc-stagec-monitor.timer' 'etoro-calibrate.timer' --no-pager
