#!/usr/bin/env bash
# Installiert den Timer der automatischen Finanzierungsmessung (ENTSCHAERFT, solange results/stage_c/fin_cycle.ARMED fehlt).
# Scharfschalten:  echo demo > ~/ebc_stagec/results/stage_c/fin_cycle.ARMED   Entschaerfen: Datei loeschen.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$dest" "$HOME/ebc_stagec/logs" "$HOME/ebc_stagec/results/stage_c"
for f in ebc-fincycle.service ebc-fincycle.timer; do install -m 0644 "$here/$f" "$dest/$f"; done
systemctl --user daemon-reload
systemctl --user enable --now ebc-fincycle.timer
systemctl --user list-timers 'ebc-fincycle.timer' --no-pager
