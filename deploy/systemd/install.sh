#!/usr/bin/env bash
# Installiert die systemd-User-Units (idempotent, ohne sudo). Aktiviert nur den Wächter-Timer; den Bot-Dienst
# aktivieren Sie bewusst selbst (siehe docs/SYSTEMD_BOT.md, Schritt 3). Nichts wird gelöscht.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$dest"
for f in etoro-paper-bot.service etoro-ops-guard.service etoro-ops-guard.timer; do
  install -m 0644 "$here/$f" "$dest/$f"
  echo "installiert: $dest/$f"
done
systemctl --user daemon-reload
systemctl --user enable --now etoro-ops-guard.timer
echo
echo "Wächter-Timer aktiv. Bot-Dienst installiert, aber NICHT gestartet."
echo "Linger (Dienste laufen auch ohne Anmeldung/nach Neustart) ist: $(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo unbekannt)"
echo "Falls 'no': einmalig  sudo loginctl enable-linger $USER"
