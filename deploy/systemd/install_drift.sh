#!/usr/bin/env bash
# Installiert und aktiviert den wöchentlichen Drift-Check-Timer (Sa 06:10 UTC). Idempotent, ohne sudo; ändert keine bestehenden Units.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$dest"
for f in etoro-drift.service etoro-drift.timer; do
  install -m 0644 "$here/$f" "$dest/$f"
  echo "installiert: $dest/$f"
done
systemctl --user daemon-reload
systemctl --user enable --now etoro-drift.timer
systemctl --user list-timers 'etoro-drift.timer' --no-pager
