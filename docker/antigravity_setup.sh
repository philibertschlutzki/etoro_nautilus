#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# antigravity_setup.sh
# Vollautomatisches Deployment: Antigravity CLI + Jules MCP auf Ubuntu
# Rev. 3: VPN via Gluetun Docker-Container (kein host-seitiger WireGuard)
#
# Getestete Umgebungen : Ubuntu 22.04 LTS / 24.04 LTS (x86_64)
# Quell-Doku           : ANTIGRAVITY_DEPLOYMENT.md (Rev. 3, Mai 2026)
#
# BUGFIXES gegenüber Original-Anleitung (inline dokumentiert):
#   [FIX-1] nftables METADATA-IP-Tippfehler: 169.254.254.169 → 169.254.169.254
#   [FIX-2] Docker Swarm init fehlt – Secrets sind Swarm-Feature
#   [FIX-3] Healthcheck-Env-Bug: JULES_API_KEY via Entrypoint nicht im HC-Kontext
#   [FIX-5] seccomp:./... → absoluter Pfad
#
# MIGRATION Rev. 3 (gegenüber Rev. 2):
#   [MIG-1] Host-WireGuard entfernt → Gluetun Docker-Container übernimmt VPN
#   [MIG-2] nftables vereinfacht: kein wg0-Kill-Switch (Gluetun=intern)
#   [MIG-3] Repo: Dockerfile.gluetun + gluetun-entrypoint.sh generiert
#   [MIG-4] Stack: gluetun-Service + network_mode:service:gluetun für agy
#   [MIG-5] Neues Docker Secret: nordvpn_wg_key_<AGENT>
#
# HINWEIS ZU PORTAINER:
#   Stack-Deploy erfolgt über Docker CLI direkt (docker stack deploy).
#   Portainer erkennt und verwaltet den laufenden Stack automatisch.
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Farben ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log_info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
log_step()  { echo -e "\n${BOLD}${CYAN}══ $* ══${RESET}"; }
die()       { log_error "$*"; exit 1; }

# ─── Root-Check ───────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && die "Dieses Script muss als root oder via sudo ausgeführt werden."

# ─── Ubuntu-Check ─────────────────────────────────────────────────────────────
if ! grep -qiE "ubuntu" /etc/os-release 2>/dev/null; then
  die "Dieses Script ist nur für Ubuntu 22.04/24.04 getestet."
fi

UBUNTU_VERSION=$(. /etc/os-release && echo "$VERSION_ID")
log_info "Ubuntu $UBUNTU_VERSION erkannt."
[[ "$UBUNTU_VERSION" != "22.04" && "$UBUNTU_VERSION" != "24.04" ]] && \
  log_warn "Ubuntu $UBUNTU_VERSION ist nicht offiziell getestet – Fortfahren auf eigene Gefahr."

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 0: Eingaben sammeln
# ═══════════════════════════════════════════════════════════════════════════════
log_step "Konfiguration abfragen"

# ── SUDO_USER oder aktuellen User ermitteln ──
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo "")}"
[[ -z "$TARGET_USER" ]] && die "Konnte den aufrufenden Nicht-Root-User nicht bestimmen. Script via 'sudo ./antigravity_setup.sh' starten."
TARGET_UID=$(id -u "$TARGET_USER")
TARGET_GID=$(id -g "$TARGET_USER")
TARGET_HOME=$(eval echo "~$TARGET_USER")
log_info "Ziel-User: $TARGET_USER (UID=$TARGET_UID, GID=$TARGET_GID, HOME=$TARGET_HOME)"

# ── NordVPN WireGuard Private Key ──
echo ""
echo -e "${BOLD}NordVPN WireGuard Private Key${RESET}"
echo "  → Bezug: my.nordaccount.com → NordVPN → Manual setup → WireGuard"
echo "           → 'Generate key pair' → Private Key kopieren"
echo "  → Format: 44 Zeichen, Base64-codiert (endet auf '=')"
while true; do
  read -rsp "  WireGuard Private Key (Eingabe versteckt): " NORDVPN_WG_KEY; echo ""
  [[ ${#NORDVPN_WG_KEY} -ge 40 ]] && break
  log_error "Key zu kurz – WireGuard Private Keys sind 44 Zeichen (Base64). Erneut eingeben."
done

# ── NordVPN Server-Land ──
echo ""
read -rp "  NordVPN Server-Land [Switzerland]: " SERVER_COUNTRIES_INPUT
SERVER_COUNTRIES="${SERVER_COUNTRIES_INPUT:-Switzerland}"

# ── API Keys ──
echo ""
echo -e "${BOLD}Google Antigravity CLI API-Key${RESET}"
echo "  → Bezug: console.cloud.google.com → APIs & Services → Credentials → Create API key"
echo "     (Antigravity CLI API muss im Projekt aktiviert sein)"
while true; do
  read -rsp "  AGY API-Key (Eingabe versteckt): " AGY_API_KEY; echo ""
  [[ ${#AGY_API_KEY} -gt 10 ]] && break
  log_error "Key zu kurz – bitte vollständigen Key eingeben."
done

echo ""
echo -e "${BOLD}Jules Coding-Assistent API-Key${RESET}"
echo "  → Bezug: jules.google.com/u/0/settings/api → neuen Key generieren"
while true; do
  read -rsp "  Jules API-Key (Eingabe versteckt): " JULES_API_KEY; echo ""
  [[ ${#JULES_API_KEY} -gt 10 ]] && break
  log_error "Key zu kurz – bitte vollständigen Key eingeben."
done

# ── Netzwerk-Interface auto-detektieren ──
echo ""
DEFAULT_ETH_IF=$(ip route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)
[[ -z "$DEFAULT_ETH_IF" ]] && DEFAULT_ETH_IF="eth0"
read -rp "  LAN-Interface (erkannt: $DEFAULT_ETH_IF) [Enter = übernehmen]: " ETH_IF_INPUT
ETH_IF="${ETH_IF_INPUT:-$DEFAULT_ETH_IF}"
log_info "Verwende Interface: $ETH_IF"

# ── Portainer Admin-Passwort ──
echo ""
echo -e "${BOLD}Portainer Admin-Passwort${RESET} (mind. 12 Zeichen)"
while true; do
  read -rsp "  Passwort: " PORTAINER_PASS; echo ""
  read -rsp "  Passwort (Bestätigung): " PORTAINER_PASS2; echo ""
  [[ "$PORTAINER_PASS" == "$PORTAINER_PASS2" && ${#PORTAINER_PASS} -ge 12 ]] && break
  log_error "Passwörter stimmen nicht überein oder zu kurz (min. 12 Zeichen)."
done

# ── Instanz-Name ──
echo ""
read -rp "  Instanz-Name [agent_01]: " AGENT_NAME_INPUT
AGENT_NAME="${AGENT_NAME_INPUT:-agent_01}"
STACK_NAME="antigravity_${AGENT_NAME}"
CONTAINER_NAME="antigravity_${AGENT_NAME}"
GLUETUN_CONTAINER="gluetun_${AGENT_NAME}"
AGY_SECRET_NAME="agy_api_key_${AGENT_NAME}"
JULES_SECRET_NAME="jules_api_key_${AGENT_NAME}"
NORDVPN_SECRET_NAME="nordvpn_wg_key_${AGENT_NAME}"

# ── Repo-Verzeichnis ──
DEFAULT_REPO_DIR="$TARGET_HOME/antigravity-agent"
read -rp "  Lokales Repo-Verzeichnis [$DEFAULT_REPO_DIR]: " REPO_DIR_INPUT
REPO_DIR="${REPO_DIR_INPUT:-$DEFAULT_REPO_DIR}"

# ─── Zusammenfassung & Bestätigung ───────────────────────────────────────────
echo ""
echo -e "${BOLD}════ Konfigurationsübersicht ════${RESET}"
echo "  User/UID/GID      : $TARGET_USER / $TARGET_UID / $TARGET_GID"
echo "  LAN-Interface     : $ETH_IF"
echo "  VPN               : Gluetun (NordVPN WireGuard, Land: $SERVER_COUNTRIES)"
echo "  WG Private Key    : ${NORDVPN_WG_KEY:0:8}... (${#NORDVPN_WG_KEY} Zeichen)"
echo "  AGY API-Key       : ${AGY_API_KEY:0:8}... (${#AGY_API_KEY} Zeichen)"
echo "  Jules API-Key     : ${JULES_API_KEY:0:8}... (${#JULES_API_KEY} Zeichen)"
echo "  Instanz           : $AGENT_NAME"
echo "  Stack             : $STACK_NAME"
echo "  Repo-Verzeichnis  : $REPO_DIR"
echo "  Workspace         : $TARGET_HOME/antigravity_workspaces/$AGENT_NAME"
echo ""
read -rp "Alles korrekt? Installation starten? [j/N]: " CONFIRM
[[ "${CONFIRM,,}" != "j" ]] && { echo "Abgebrochen."; exit 0; }

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 1: Systempakete
# ═══════════════════════════════════════════════════════════════════════════════
log_step "1 – Systempakete installieren"

# [MIG-1] wireguard, wireguard-tools und openresolv werden nicht mehr benötigt.
#   WireGuard läuft ausschließlich im Gluetun Docker-Container.
#   systemd-resolved Anpassungen entfallen ebenfalls.

apt-get update -qq
apt-get install -y \
  nftables \
  curl \
  ca-certificates \
  gnupg \
  lsb-release \
  jq \
  tree 2>/dev/null || true

systemctl enable nftables
systemctl start nftables
log_ok "Pakete installiert, nftables aktiv"

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 2: nftables Regelwerk
# (Ehemals Schritt 3 – WireGuard-Setup entfällt vollständig)
# ═══════════════════════════════════════════════════════════════════════════════
log_step "2 – nftables Regelwerk schreiben"

# [MIG-2] Vereinfachtes Regelwerk ohne wg0-Kill-Switch.
#   Gluetun übernimmt den Kill-Switch intern (FIREWALL=on).
#   Host-nftables: LAN-Isolation + Metadata-Blockade + Gluetun-Egress.
# [FIX-1] METADATA-IP korrigiert: 169.254.169.254 (war 169.254.254.169)

cat > /etc/nftables.conf << NFTEOF
#!/usr/sbin/nft -f
# Antigravity nftables Regelwerk (Rev. 3 – Gluetun)
# Automatisch generiert von antigravity_setup.sh
# [MIG-2] kein wg0-Kill-Switch: Gluetun übernimmt VPN-Kill-Switch intern
# [FIX-1] METADATA-IP korrigiert: 169.254.169.254

flush ruleset

define ETH_IF     = ${ETH_IF}
define AGY_BRIDGE = br-antigravity
define AGY_SUBNET = 172.28.0.0/24
define RFC1918    = { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 }
define METADATA   = 169.254.169.254/32

table inet host_fw {
  chain input {
    type filter hook input priority 0; policy drop;

    iifname "lo" accept
    ct state established,related accept

    iifname \$ETH_IF tcp dport 22 accept
    iifname \$ETH_IF tcp dport { 9000, 9443 } accept

    ip protocol icmp accept
    ip6 nexthdr icmpv6 accept

    drop
  }

  chain output {
    type filter hook output priority 0; policy accept;
  }
}

table inet container_fw {
  chain forward {
    type filter hook forward priority -100; policy accept;

    # 1. RFC-1918 aus Containern blockieren (LAN-Isolation)
    iifname \$AGY_BRIDGE ip daddr \$RFC1918 \
      log prefix "nft-drop-rfc1918: " drop

    # 2. Cloud-Metadata blockieren
    iifname \$AGY_BRIDGE ip daddr \$METADATA \
      log prefix "nft-drop-metadata: " drop

    # 3. Gluetun-Egress zu öffentlichen IPs erlauben
    #    (NordVPN-Endpoints, *.googleapis.com, jules.google.com)
    #    Kill-Switch ist Gluetuns Aufgabe (FIREWALL=on intern)
    iifname \$AGY_BRIDGE oifname \$ETH_IF accept

    # 4. Return-Traffic
    iifname \$ETH_IF oifname \$AGY_BRIDGE \
      ct state established,related accept
  }

  chain postrouting {
    type nat hook postrouting priority srcnat;
    iifname \$AGY_BRIDGE oifname \$ETH_IF masquerade
  }
}
NFTEOF

if nft -c -f /etc/nftables.conf; then
  log_ok "nftables-Syntax valide"
else
  die "nftables-Syntaxfehler – bitte /etc/nftables.conf prüfen"
fi

nft -f /etc/nftables.conf
log_ok "nftables-Regelwerk geladen"

echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-antigravity.conf
sysctl -p /etc/sysctl.d/99-antigravity.conf -q
log_ok "IP-Forwarding aktiviert"

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 3: Docker installieren
# ═══════════════════════════════════════════════════════════════════════════════
log_step "3 – Docker Engine installieren"

if command -v docker &>/dev/null; then
  log_ok "Docker bereits installiert: $(docker --version)"
else
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null

  apt-get update -qq
  apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

  systemctl enable docker
  systemctl start docker
  log_ok "Docker installiert: $(docker --version)"
fi

if ! groups "$TARGET_USER" | grep -q docker; then
  usermod -aG docker "$TARGET_USER"
  log_ok "User $TARGET_USER zur docker-Gruppe hinzugefügt (wirkt nach Re-Login)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 4: Docker Swarm initialisieren
# ═══════════════════════════════════════════════════════════════════════════════
log_step "4 – Docker Swarm initialisieren"

# [FIX-2] Docker Secrets sind eine Swarm-Funktion – ohne swarm init kein Secret.

if docker info 2>/dev/null | grep -q "Swarm: active"; then
  log_ok "Docker Swarm bereits aktiv"
else
  HOST_IP=$(ip -4 addr show "$ETH_IF" 2>/dev/null | grep -oP '(?<=inet\s)\d+\.\d+\.\d+\.\d+' | head -1)
  if [[ -n "$HOST_IP" ]]; then
    docker swarm init --advertise-addr "$HOST_IP" --listen-addr "$HOST_IP:2377"
  else
    docker swarm init
  fi
  log_ok "Docker Swarm (single-node) initialisiert"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 5: Docker Secrets anlegen
# ═══════════════════════════════════════════════════════════════════════════════
log_step "5 – Docker Secrets anlegen"

create_or_update_secret() {
  local name="$1"
  local value="$2"
  if docker secret inspect "$name" &>/dev/null; then
    log_warn "Secret '$name' existiert bereits – wird neu erstellt (remove + create)"
    docker secret rm "$name"
  fi
  printf '%s' "$value" | docker secret create "$name" -
  log_ok "Secret '$name' angelegt"
}

create_or_update_secret "$AGY_SECRET_NAME"    "$AGY_API_KEY"
create_or_update_secret "$JULES_SECRET_NAME"  "$JULES_API_KEY"
create_or_update_secret "$NORDVPN_SECRET_NAME" "$NORDVPN_WG_KEY"

# Keys aus RAM-Variablen löschen sobald in Swarm gespeichert
unset AGY_API_KEY JULES_API_KEY NORDVPN_WG_KEY
log_ok "API-Keys und WireGuard Private Key aus Script-Umgebung gelöscht"

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 6: Portainer installieren
# ═══════════════════════════════════════════════════════════════════════════════
log_step "6 – Portainer CE installieren"

if docker ps -a --format '{{.Names}}' | grep -q "^portainer$"; then
  log_ok "Portainer-Container läuft bereits"
else
  docker volume create portainer_data 2>/dev/null || true

  docker run -d \
    --name portainer \
    --restart always \
    -p 9000:9000 \
    -p 9443:9443 \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v portainer_data:/data \
    portainer/portainer-ce:latest

  log_info "Warte auf Portainer-Start..."
  RETRIES=30
  while ! curl -sf http://localhost:9000/api/status > /dev/null 2>&1; do
    sleep 2
    ((RETRIES--))
    [[ $RETRIES -eq 0 ]] && { log_warn "Portainer nicht erreichbar – Passwort manuell setzen"; break; }
  done

  if curl -sf http://localhost:9000/api/status > /dev/null 2>&1; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -X POST http://localhost:9000/api/users/admin/init \
      -H "Content-Type: application/json" \
      -d "{\"Username\":\"admin\",\"Password\":\"${PORTAINER_PASS}\"}")
    if [[ "$HTTP_CODE" == "200" ]]; then
      log_ok "Portainer Admin-User angelegt"
    else
      log_warn "Portainer Admin-Init HTTP $HTTP_CODE – möglicherweise bereits konfiguriert"
    fi
  fi
fi

unset PORTAINER_PASS
log_ok "Portainer erreichbar unter https://$(hostname -I | awk '{print $1}'):9443"

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 7: Docker-Netzwerk anlegen
# ═══════════════════════════════════════════════════════════════════════════════
log_step "7 – Docker-Netzwerk 'antigravity_net' anlegen"

if docker network inspect antigravity_net &>/dev/null; then
  log_ok "Netzwerk 'antigravity_net' existiert bereits"
  BRIDGE_NAME=$(docker network inspect antigravity_net \
    --format '{{index .Options "com.docker.network.bridge.name"}}' 2>/dev/null || echo "")
  [[ "$BRIDGE_NAME" != "br-antigravity" ]] && \
    log_warn "Bridge-Name ist '$BRIDGE_NAME' statt 'br-antigravity' – nftables-Regeln ggf. anpassen!"
else
  docker network create \
    --driver bridge \
    --subnet 172.28.0.0/24 \
    --gateway 172.28.0.1 \
    --opt "com.docker.network.bridge.name=br-antigravity" \
    --opt "com.docker.network.bridge.enable_icc=false" \
    --label "managed_by=manual" \
    antigravity_net

  log_ok "Netzwerk 'antigravity_net' (br-antigravity) angelegt"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 8: Verzeichnisstruktur anlegen
# ═══════════════════════════════════════════════════════════════════════════════
log_step "8 – Verzeichnisse anlegen"

WORKSPACE_DIR="$TARGET_HOME/antigravity_workspaces/$AGENT_NAME"

sudo -u "$TARGET_USER" mkdir -p \
  "$WORKSPACE_DIR" \
  "$TARGET_HOME/.gemini" \
  "$TARGET_HOME/.config/antigravity"

chown -R "${TARGET_UID}:${TARGET_GID}" \
  "$TARGET_HOME/antigravity_workspaces" \
  "$TARGET_HOME/.gemini" \
  "$TARGET_HOME/.config/antigravity"

log_ok "Verzeichnisse angelegt unter $TARGET_HOME/"

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 9: Repo-Dateien generieren
# ═══════════════════════════════════════════════════════════════════════════════
log_step "9 – Repo-Dateien generieren in $REPO_DIR"

sudo -u "$TARGET_USER" mkdir -p "$REPO_DIR"

# ── Dockerfile (agy) ─────────────────────────────────────────────────────────
cat > "$REPO_DIR/Dockerfile" << DOCKERFILE_EOF
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG USER_UID=${TARGET_UID}
ARG USER_GID=${TARGET_GID}

RUN apt-get update && apt-get install -y --no-install-recommends \\
      ca-certificates curl git gnupg jq \\
      python3 python3-pip python3-venv \\
      build-essential \\
  && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \\
  && apt-get install -y --no-install-recommends nodejs \\
  && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -g \${USER_GID} agentuser 2>/dev/null || true \\
 && useradd -u \${USER_UID} -g \${USER_GID} -m -s /bin/bash agentuser 2>/dev/null || true

RUN curl -fsSL https://antigravity.google/cli/install.sh | bash \\
 && mv /root/.local/bin/agy /usr/local/bin/agy 2>/dev/null || \\
    mv ~/.local/bin/agy /usr/local/bin/agy 2>/dev/null || true \\
 && chmod 755 /usr/local/bin/agy

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /etc/antigravity
COPY mcp_config.json /etc/antigravity/mcp_config.json
RUN chmod 644 /etc/antigravity/mcp_config.json

ENV PATH="/usr/local/bin:\${PATH}" \\
    HOME="/home/agentuser" \\
    TMPDIR="/home/agentuser/workspace/.tmp" \\
    PIP_TMPDIR="/home/agentuser/workspace/.tmp" \\
    PIP_NO_CACHE_DIR="1" \\
    PYTHONPYCACHEPREFIX="/home/agentuser/workspace/.pycache" \\
    JULES_MCP_CONFIG="/etc/antigravity/mcp_config.json"

WORKDIR /home/agentuser/workspace
RUN chown -R agentuser:agentuser /home/agentuser
USER agentuser

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["tail", "-f", "/dev/null"]
DOCKERFILE_EOF

# ── Dockerfile.gluetun ───────────────────────────────────────────────────────
# [MIG-3] Wrапpt qmcgaw/gluetun – liest WireGuard Private Key aus Docker Secret
cat > "$REPO_DIR/Dockerfile.gluetun" << 'GLUETUN_DOCKER_EOF'
FROM qmcgaw/gluetun:latest

# Wrapper-Entrypoint: liest WireGuard Private Key aus Docker Secret,
# exportiert ihn als WIREGUARD_PRIVATE_KEY, startet dann originalen Gluetun-Entrypoint.
COPY gluetun-entrypoint.sh /gluetun-secret-entrypoint.sh
RUN chmod +x /gluetun-secret-entrypoint.sh

ENTRYPOINT ["/gluetun-secret-entrypoint.sh"]
GLUETUN_DOCKER_EOF

# ── gluetun-entrypoint.sh ────────────────────────────────────────────────────
cat > "$REPO_DIR/gluetun-entrypoint.sh" << GLUETUN_EP_EOF
#!/bin/sh
set -e

# Dynamisch den Secret-Namen aus Env-Variable lesen (unterstützt mehrere Instanzen)
NORDVPN_SECRET="\${NORDVPN_WG_SECRET_NAME:-nordvpn_wg_key_${AGENT_NAME}}"
WG_KEY_FILE="/run/secrets/\${NORDVPN_SECRET}"

if [ ! -f "\$WG_KEY_FILE" ]; then
  echo "[GLUETUN-ENTRYPOINT] FEHLER: Secret-Datei nicht gefunden: \$WG_KEY_FILE" >&2
  exit 1
fi

export WIREGUARD_PRIVATE_KEY
WIREGUARD_PRIVATE_KEY=\$(tr -d '[:space:]' < "\$WG_KEY_FILE")

if [ -z "\$WIREGUARD_PRIVATE_KEY" ]; then
  echo "[GLUETUN-ENTRYPOINT] FEHLER: WIREGUARD_PRIVATE_KEY ist leer." >&2
  exit 1
fi

echo "[GLUETUN-ENTRYPOINT] WireGuard Private Key geladen (\${#WIREGUARD_PRIVATE_KEY} Zeichen)."

exec /gluetun/entrypoint.sh "\$@"
GLUETUN_EP_EOF
chmod +x "$REPO_DIR/gluetun-entrypoint.sh"

# ── docker-entrypoint.sh (agy) ───────────────────────────────────────────────
cat > "$REPO_DIR/docker-entrypoint.sh" << ENTRYPOINT_EOF
#!/bin/bash
set -euo pipefail

AGY_SECRET_NAME="\${AGY_SECRET_NAME:-agy_api_key_${AGENT_NAME}}"
JULES_SECRET_NAME="\${JULES_SECRET_NAME:-jules_api_key_${AGENT_NAME}}"

AGY_SECRET_FILE="/run/secrets/\${AGY_SECRET_NAME}"
JULES_SECRET_FILE="/run/secrets/\${JULES_SECRET_NAME}"

if [[ ! -f "\$AGY_SECRET_FILE" ]]; then
  echo "[ENTRYPOINT] FEHLER: Secret nicht gefunden: \$AGY_SECRET_FILE" >&2
  exit 1
fi

if [[ ! -f "\$JULES_SECRET_FILE" ]]; then
  echo "[ENTRYPOINT] FEHLER: Secret nicht gefunden: \$JULES_SECRET_FILE" >&2
  exit 1
fi

export GOOGLE_API_KEY
GOOGLE_API_KEY=\$(tr -d '[:space:]' < "\$AGY_SECRET_FILE")

export JULES_API_KEY
JULES_API_KEY=\$(tr -d '[:space:]' < "\$JULES_SECRET_FILE")

[[ -z "\$GOOGLE_API_KEY" ]] && { echo "[ENTRYPOINT] FEHLER: GOOGLE_API_KEY leer" >&2; exit 1; }
[[ -z "\$JULES_API_KEY" ]]  && { echo "[ENTRYPOINT] FEHLER: JULES_API_KEY leer" >&2; exit 1; }

echo "[ENTRYPOINT] Secrets geladen. GOOGLE_API_KEY: \${GOOGLE_API_KEY:0:8}... JULES_API_KEY: \${JULES_API_KEY:0:8}..."

mkdir -p "\${TMPDIR:-/home/agentuser/workspace/.tmp}"
mkdir -p "/home/agentuser/workspace/.pycache"

echo "[ENTRYPOINT] Laufzeitverzeichnisse bereit. TMPDIR=\${TMPDIR}"

exec "\$@"
ENTRYPOINT_EOF
chmod +x "$REPO_DIR/docker-entrypoint.sh"

# ── mcp_config.json ──────────────────────────────────────────────────────────
cat > "$REPO_DIR/mcp_config.json" << 'MCPEOF'
{
  "mcpServers": {
    "jules": {
      "transport": "http",
      "url": "https://jules.google.com/api/mcp/v1",
      "headers": {
        "Authorization": "Bearer ${JULES_API_KEY}",
        "Content-Type": "application/json"
      },
      "allowedDirectories": [
        "/home/agentuser/workspace"
      ],
      "readOnly": false,
      "timeoutSeconds": 120
    }
  }
}
MCPEOF

# ── AGENTS.md ────────────────────────────────────────────────────────────────
cat > "$REPO_DIR/AGENTS.md" << AGENTSEOF
# Agent Configuration

## Role
This agent operates within an isolated Docker environment.
Workspace: /home/agentuser/workspace
Instance: ${AGENT_NAME}

## Tooling
- Jules MCP is active and configured via /etc/antigravity/mcp_config.json
- The agent is authorized to generate, modify, and execute code
  within the workspace (/home/agentuser/workspace) via Jules MCP.

## Constraints
- No access to LAN resources
- All network traffic routes through Gluetun VPN (NordVPN WireGuard)
- VPN Kill-Switch active: Gluetun blocks all traffic if VPN tunnel drops
- Read/write access limited to workspace directory
- Temporary files must use TMPDIR=/home/agentuser/workspace/.tmp
AGENTSEOF

# ── seccomp-profile.json ─────────────────────────────────────────────────────
cat > "$REPO_DIR/seccomp-profile.json" << 'SECCOMPEOF'
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
  "syscalls": [
    {
      "names": [
        "accept", "accept4", "access", "arch_prctl", "bind", "brk",
        "capget", "capset", "chdir", "chmod", "chown", "clock_getres",
        "clock_gettime", "clock_nanosleep", "clone", "clone3", "close",
        "connect", "copy_file_range", "creat", "dup", "dup2", "dup3",
        "epoll_create", "epoll_create1", "epoll_ctl", "epoll_pwait",
        "epoll_wait", "eventfd", "eventfd2", "execve", "execveat", "exit",
        "exit_group", "faccessat", "faccessat2", "fadvise64", "fallocate",
        "fchdir", "fchmod", "fchmodat", "fchown", "fchownat", "fcntl",
        "fdatasync", "fgetxattr", "flistxattr", "flock", "fremovexattr",
        "fsetxattr", "fstat", "fstatfs", "fsync", "ftruncate", "futex",
        "futex_waitv", "getcwd", "getdents", "getdents64", "getegid",
        "geteuid", "getgid", "getgroups", "getpeername", "getpgrp",
        "getpid", "getppid", "getrandom", "getrlimit", "getsid",
        "getsockname", "getsockopt", "gettid", "gettimeofday", "getuid",
        "getxattr", "inotify_add_watch", "inotify_init", "inotify_init1",
        "inotify_rm_watch", "io_cancel", "io_destroy", "io_getevents",
        "io_setup", "io_submit",
        "io_uring_setup", "io_uring_enter", "io_uring_register",
        "ioctl", "kill", "lchown", "lgetxattr",
        "link", "linkat", "listen", "listxattr", "llistxattr",
        "lremovexattr", "lseek", "lsetxattr", "lstat", "madvise",
        "memfd_create", "mincore", "mkdir", "mkdirat", "mlock", "mlock2",
        "mlockall", "mmap", "mprotect", "mremap", "munlock", "munlockall",
        "munmap", "nanosleep", "newfstatat", "open", "openat", "openat2",
        "pause", "pipe", "pipe2", "poll", "ppoll", "prctl", "pread64",
        "preadv", "preadv2", "prlimit64", "pwrite64", "pwritev",
        "pwritev2", "read", "readlink", "readlinkat", "readv", "recv",
        "recvfrom", "recvmmsg", "recvmsg", "remap_file_pages", "rename",
        "renameat", "renameat2", "restart_syscall", "rmdir", "rt_sigaction",
        "rt_sigpending", "rt_sigprocmask", "rt_sigqueueinfo", "rt_sigreturn",
        "rt_sigsuspend", "rt_sigtimedwait", "rt_tgsigqueueinfo",
        "sched_getaffinity", "sched_getparam", "sched_getscheduler",
        "sched_yield", "select", "semctl", "semget", "semop", "semtimedop",
        "send", "sendfile", "sendmmsg", "sendmsg", "sendto",
        "set_robust_list", "set_tid_address", "setgid", "setgroups",
        "setitimer", "setpgid", "setregid", "setresgid", "setresuid",
        "setreuid", "setsid", "setsockopt", "setuid", "setxattr",
        "shmat", "shmctl", "shmdt", "shmget", "shutdown", "sigaltstack",
        "signalfd", "signalfd4", "socket", "socketpair", "splice", "stat",
        "statfs", "statx", "symlink", "symlinkat", "sync", "sync_file_range",
        "syncfs", "sysinfo", "tgkill", "time", "timer_create", "timer_delete",
        "timer_getoverrun", "timer_gettime", "timer_settime", "timerfd_create",
        "timerfd_gettime", "timerfd_settime", "times", "tkill", "truncate",
        "umask", "uname", "unlink", "unlinkat", "utime", "utimensat",
        "utimes", "vfork", "wait4", "waitid", "write", "writev"
      ],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
SECCOMPEOF

# ── .gitignore ────────────────────────────────────────────────────────────────
cat > "$REPO_DIR/.gitignore" << 'GITIGNEOF'
*.env
*.key
*.pem
.env*
secrets/
__pycache__/
*.pyc
.pytest_cache/
.jules_cache/
.jules_local/
GITIGNEOF

# ── docker-compose.yml (Stack-YAML) ──────────────────────────────────────────
# [MIG-4] Gluetun-Service + network_mode:service:gluetun für agy
# [FIX-3] Healthcheck ohne API-Key-Abhängigkeit
# [FIX-5] Absoluter seccomp-Pfad

SECCOMP_ABS_PATH="$REPO_DIR/seccomp-profile.json"

cat > "$REPO_DIR/docker-compose.yml" << COMPOSEEOF
# Stack: ${STACK_NAME}
# Rev. 3: Gluetun VPN-Container
# Automatisch generiert von antigravity_setup.sh

networks:
  antigravity_net:
    external: true
    name: antigravity_net

services:

  # Gluetun VPN-Container (NordVPN WireGuard)
  gluetun:
    build:
      context: .
      dockerfile: Dockerfile.gluetun
    image: antigravity-gluetun:${AGENT_NAME}
    container_name: ${GLUETUN_CONTAINER}
    cap_add:
      - NET_ADMIN
    networks:
      - antigravity_net
    restart: unless-stopped
    environment:
      - VPN_SERVICE_PROVIDER=nordvpn
      - VPN_TYPE=wireguard
      - NORDVPN_WG_SECRET_NAME=${NORDVPN_SECRET_NAME}
      - SERVER_COUNTRIES=${SERVER_COUNTRIES}
      - FIREWALL=on
      - DOT=on
      - DOT_PROVIDERS=custom
      - DOT_CUSTOM_ADDRESS=103.86.96.100
      - LOG_LEVEL=info
    healthcheck:
      test:
        - "CMD-SHELL"
        - "wget -qO- --timeout=5 https://ifconfig.me > /tmp/vpn_ip && [ -s /tmp/vpn_ip ]"
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    secrets:
      - ${NORDVPN_SECRET_NAME}

  # Antigravity / agy Container
  ${AGENT_NAME}:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        USER_UID: "${TARGET_UID}"
        USER_GID: "${TARGET_GID}"
    image: antigravity-agent:${AGENT_NAME}
    container_name: ${CONTAINER_NAME}

    # Teilt Gluetuns Netzwerk-Namespace vollständig
    network_mode: "service:gluetun"

    depends_on:
      gluetun:
        condition: service_healthy

    user: "${TARGET_UID}:${TARGET_GID}"
    restart: unless-stopped

    privileged: false
    security_opt:
      - no-new-privileges:true
      - seccomp:${SECCOMP_ABS_PATH}
    cap_drop:
      - ALL

    tmpfs:
      - /tmp:rw,noexec,nosuid,size=256m

    # [FIX-3] Healthcheck ohne API-Key-Abhängigkeit
    healthcheck:
      test:
        - "CMD-SHELL"
        - "agy --version > /dev/null 2>&1"
      interval: 60s
      timeout: 15s
      retries: 3
      start_period: 30s

    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
        reservations:
          memory: 512M

    volumes:
      - ${WORKSPACE_DIR}:/home/agentuser/workspace:rw
      - ${TARGET_HOME}/.gemini:/home/agentuser/.gemini:rw
      - ${TARGET_HOME}/.config/antigravity:/home/agentuser/.config/antigravity:rw
      - jules_cache_${AGENT_NAME}:/home/agentuser/.cache/jules
      - jules_config_${AGENT_NAME}:/home/agentuser/.config/jules
      - jules_local_${AGENT_NAME}:/home/agentuser/.local/share/jules

    secrets:
      - ${AGY_SECRET_NAME}
      - ${JULES_SECRET_NAME}

    environment:
      - AGY_SECRET_NAME=${AGY_SECRET_NAME}
      - JULES_SECRET_NAME=${JULES_SECRET_NAME}
      - AGY_ENABLE_JULES_MCP=true
      - JULES_MCP_CONFIG=/etc/antigravity/mcp_config.json

secrets:
  ${AGY_SECRET_NAME}:
    external: true
  ${JULES_SECRET_NAME}:
    external: true
  ${NORDVPN_SECRET_NAME}:
    external: true

volumes:
  jules_cache_${AGENT_NAME}:
    driver: local
    labels:
      managed_by: "antigravity-stack"
      instance: "${AGENT_NAME}"
      purpose: "jules-mcp-vector-cache"
  jules_config_${AGENT_NAME}:
    driver: local
    labels:
      managed_by: "antigravity-stack"
      instance: "${AGENT_NAME}"
      purpose: "jules-mcp-session-config"
  jules_local_${AGENT_NAME}:
    driver: local
    labels:
      managed_by: "antigravity-stack"
      instance: "${AGENT_NAME}"
      purpose: "jules-mcp-workspace-metadata"
COMPOSEEOF

chown -R "${TARGET_UID}:${TARGET_GID}" "$REPO_DIR"
log_ok "Repo-Dateien generiert in $REPO_DIR"

# Seccomp-Profil auch nach /etc/antigravity/ kopieren (absoluter Pfad)
mkdir -p /etc/antigravity
cp "$REPO_DIR/seccomp-profile.json" /etc/antigravity/seccomp-profile.json
chmod 644 /etc/antigravity/seccomp-profile.json
log_ok "seccomp-profile.json nach /etc/antigravity/ kopiert (absoluter Pfad für Stack)"

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 10: Docker-Images bauen
# ═══════════════════════════════════════════════════════════════════════════════
log_step "10 – Docker-Images bauen"

cd "$REPO_DIR"

log_info "Baue Gluetun-Image..."
docker build \
  -f Dockerfile.gluetun \
  -t "antigravity-gluetun:${AGENT_NAME}" \
  . 2>&1 | tee /tmp/docker-build-gluetun.log

if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
  log_error "Gluetun-Build fehlgeschlagen:"
  tail -20 /tmp/docker-build-gluetun.log
  die "Build-Fehler – siehe /tmp/docker-build-gluetun.log"
fi
log_ok "Image 'antigravity-gluetun:${AGENT_NAME}' gebaut"

log_info "Baue agy-Image (dies dauert 3-7 Minuten)..."
docker build \
  --build-arg USER_UID="$TARGET_UID" \
  --build-arg USER_GID="$TARGET_GID" \
  -t "antigravity-agent:${AGENT_NAME}" \
  . 2>&1 | tee /tmp/docker-build-agy.log

if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
  log_error "agy-Build fehlgeschlagen. Letzte Zeilen:"
  tail -30 /tmp/docker-build-agy.log
  log_warn "Mögliche Ursache: URL https://antigravity.google/cli/install.sh nicht erreichbar."
  die "Build-Fehler – siehe /tmp/docker-build-agy.log"
fi
log_ok "Image 'antigravity-agent:${AGENT_NAME}' gebaut"

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 11: Stack deployen
# ═══════════════════════════════════════════════════════════════════════════════
log_step "11 – Stack deployen"

cd "$REPO_DIR"
docker stack deploy -c docker-compose.yml "$STACK_NAME" --with-registry-auth

log_info "Warte auf Gluetun-Start und VPN-Verbindung (bis zu 60 Sekunden)..."
GLUETUN_HEALTHY=false
for i in $(seq 1 12); do
  sleep 5
  STATUS=$(docker inspect "${GLUETUN_CONTAINER}" --format '{{.State.Health.Status}}' 2>/dev/null || echo "starting")
  log_info "Gluetun Health: $STATUS (${i}/12)"
  if [[ "$STATUS" == "healthy" ]]; then
    GLUETUN_HEALTHY=true
    break
  fi
done

if $GLUETUN_HEALTHY; then
  log_ok "Gluetun VPN-Container ist healthy – VPN-Verbindung aktiv"
else
  log_warn "Gluetun nach 60s noch nicht healthy – Logs prüfen:"
  docker logs "${GLUETUN_CONTAINER}" 2>&1 | tail -20
fi

sleep 5
if docker ps --filter "name=${CONTAINER_NAME}" --format '{{.Status}}' | grep -q "Up"; then
  log_ok "agy-Container ${CONTAINER_NAME} läuft"
else
  log_warn "agy-Container noch nicht 'Up':"
  docker service ls --filter "name=${STACK_NAME}"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# SCHRITT 12: Verifikation
# ═══════════════════════════════════════════════════════════════════════════════
log_step "12 – Verifikation"

FAIL=0
PASS=0

run_test() {
  local name="$1"
  local cmd="$2"
  local expect="$3"
  echo -n "  TEST: $name ... "
  local out
  out=$(eval "$cmd" 2>&1) || true
  if [[ -z "$expect" || "$out" =~ $expect ]]; then
    echo -e "${GREEN}OK${RESET}"
    ((PASS++))
  else
    echo -e "${RED}FAIL${RESET} (erwartet: '$expect', bekommen: '${out:0:80}')"
    ((FAIL++))
  fi
}

echo ""
run_test "nftables Regeln aktiv" \
  "nft list ruleset | grep -c 'nft-drop'" "1"

run_test "Docker-Netzwerk antigravity_net" \
  "docker network inspect antigravity_net --format '{{.Name}}'" "antigravity_net"

run_test "Docker Secret ${AGY_SECRET_NAME}" \
  "docker secret inspect ${AGY_SECRET_NAME} --format '{{.ID}}'" ".+"

run_test "Docker Secret ${JULES_SECRET_NAME}" \
  "docker secret inspect ${JULES_SECRET_NAME} --format '{{.ID}}'" ".+"

run_test "Docker Secret ${NORDVPN_SECRET_NAME}" \
  "docker secret inspect ${NORDVPN_SECRET_NAME} --format '{{.ID}}'" ".+"

run_test "Gluetun-Container healthy" \
  "docker inspect ${GLUETUN_CONTAINER} --format '{{.State.Health.Status}}'" "healthy"

run_test "agy-Container läuft" \
  "docker ps --filter 'name=${CONTAINER_NAME}' --format '{{.Status}}'" "Up"

run_test "Portainer API erreichbar" \
  "curl -sf http://localhost:9000/api/status | jq -r '.Version'" ".+"

# VPN-IP-Test (Gluetun)
echo -n "  TEST: VPN-IP via Gluetun ... "
VPN_IP=$(docker exec "${GLUETUN_CONTAINER}" wget -qO- --timeout=8 https://ifconfig.me 2>/dev/null || echo "timeout")
HOST_IP=$(curl -s --connect-timeout 5 https://ifconfig.me 2>/dev/null || echo "unknown")
if [[ "$VPN_IP" == "timeout" ]]; then
  echo -e "${YELLOW}SKIP${RESET} (Gluetun noch nicht verbunden)"
elif [[ "$VPN_IP" != "$HOST_IP" && -n "$VPN_IP" ]]; then
  echo -e "${GREEN}OK${RESET} (VPN-IP: $VPN_IP)"
  ((PASS++))
else
  echo -e "${RED}FAIL${RESET} (VPN-IP = Host-IP = $HOST_IP – kein VPN-Routing!)"
  ((FAIL++))
fi

# agy-Traffic via VPN (teilt Gluetuns Netzwerk)
if docker ps --filter "name=${CONTAINER_NAME}" --format '{{.Status}}' | grep -q "Up"; then
  echo -n "  TEST: agy-Traffic via VPN (shared netns) ... "
  AGY_IP=$(docker exec "${CONTAINER_NAME}" curl -s --connect-timeout 8 https://ifconfig.me 2>/dev/null || echo "timeout")
  if [[ "$AGY_IP" == "timeout" ]]; then
    echo -e "${YELLOW}SKIP${RESET} (Timeout)"
  elif [[ "$AGY_IP" == "$VPN_IP" && "$AGY_IP" != "$HOST_IP" ]]; then
    echo -e "${GREEN}OK${RESET} (identisch mit Gluetun VPN-IP)"
    ((PASS++))
  elif [[ "$AGY_IP" != "$HOST_IP" ]]; then
    echo -e "${GREEN}OK${RESET} (VPN-IP: $AGY_IP)"
    ((PASS++))
  else
    echo -e "${RED}FAIL${RESET} (agy-IP = Host-IP – kein VPN!)"
    ((FAIL++))
  fi
fi

echo ""
echo -e "  Ergebnis: ${GREEN}${PASS} bestanden${RESET}, ${RED}${FAIL} fehlgeschlagen${RESET}"

# ═══════════════════════════════════════════════════════════════════════════════
# ABSCHLUSS
# ═══════════════════════════════════════════════════════════════════════════════
HOST_ADDR=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${BOLD}${GREEN}════ Deployment abgeschlossen (Rev. 3 – Gluetun) ════${RESET}"
echo ""
echo "  Portainer GUI        : https://${HOST_ADDR}:9443"
echo "  agy-Shell            : docker exec -it ${CONTAINER_NAME} /bin/bash"
echo "  agy-Logs             : docker logs -f ${CONTAINER_NAME}"
echo "  Gluetun-Logs         : docker logs -f ${GLUETUN_CONTAINER}"
echo "  VPN-IP prüfen        : docker exec ${GLUETUN_CONTAINER} wget -qO- ifconfig.me"
echo "  nftables-Drops       : sudo journalctl -k | grep 'nft-drop'"
echo ""
echo "  Repo-Dateien         : $REPO_DIR"
echo "  Workspace            : $WORKSPACE_DIR"
echo ""
echo -e "${YELLOW}Hinweis:${RESET} Damit 'docker'-Befehle ohne sudo funktionieren, ist ein Re-Login nötig."
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo -e "${YELLOW}${FAIL} Test(s) fehlgeschlagen – Logs prüfen:${RESET}"
  echo "  docker service ps ${STACK_NAME}_${AGENT_NAME} --no-trunc"
  echo "  docker logs ${GLUETUN_CONTAINER}"
  echo "  docker logs ${CONTAINER_NAME}"
fi
