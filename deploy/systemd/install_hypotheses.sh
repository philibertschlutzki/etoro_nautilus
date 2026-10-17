#!/usr/bin/env bash
# Installiert und aktiviert den wöchentlichen Hypothesen-Regelkreis-Timer (Sa 07:25 UTC). Idempotent, ohne sudo; ändert keine bestehenden Units.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$dest"
for f in etoro-hypotheses.service etoro-hypotheses.timer; do
  install -m 0644 "$here/$f" "$dest/$f"
  echo "installiert: $dest/$f"
done
systemctl --user daemon-reload
systemctl --user enable --now etoro-hypotheses.timer
systemctl --user list-timers 'etoro-hypotheses.timer' --no-pager
