#!/bin/bash
# ==============================================================================
# Antigravity & Jules — Hardening Deployment Script
# OS: Ubuntu (idempotent)
# Revision: 2.1 — Bugfixes: nft destroy-Syntax, forward-Chain-Regelreihenfolge,
#            CGNAT/Link-Local-Isolation, Secret-Sanitization, Key-Validierung,
#            nft Syntax-Check vor Aktivierung
# ==============================================================================
set -euo pipefail

# --- Privilegien prüfen ---
if [ "$EUID" -ne 0 ]; then
  echo "[!] Dieses Script muss als root ausgeführt werden (sudo)."
  exit 1
fi

TARGET_USER=${SUDO_USER:-root}
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
WORKSPACE_DIR="$TARGET_HOME/antigravity_workspace"
SECRETS_DIR="$WORKSPACE_DIR/secrets"

# --- WireGuard-Key-Validierung ---
validate_wg_key() {
  local key="$1"
  local label="$2"
  if [[ ! "$key" =~ ^[A-Za-z0-9+/]{43}=$ ]]; then
    echo "[!] Ungültiger WireGuard-Key für '$label' (erwartet: 44-stelliger Base64-String)."
    exit 1
  fi
}

echo "============================================================"
echo " Physisches Netzwerk & Agenten-Isolation Deployment v2.1"
echo "============================================================"

# 1. Sichere Eingabe der Secrets
echo "[*] Bitte NordVPN Verbindungsdaten eingeben:"
read -r -s -p "    NordLynx Private Key: " NORD_PRIV
echo
validate_wg_key "$NORD_PRIV" "NordLynx Private Key"

read -r -p "    NordLynx Public Key: " NORD_PUB
validate_wg_key "$NORD_PUB" "NordLynx Public Key"

read -r -p "    NordLynx Endpoint (IP:Port): " NORD_EP
if [[ ! "$NORD_EP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]{1,5}$ ]]; then
  echo "[!] Ungültiges Endpoint-Format. Erwartet: IP:Port (z. B. 185.93.1.2:51820)"
  exit 1
fi

read -r -s -p "    Jules API Key (sk-...): " JULES_KEY
echo
echo

# 2. System-Abhängigkeiten installieren
echo "[*] Installiere/Aktualisiere Systemkomponenten (jq, wireguard, nftables)..."
apt-get update -qq
apt-get install -y -qq jq wireguard wireguard-tools nftables ca-certificates curl

# 3. Docker Daemon konfigurieren (Idempotent via jq — Deep-Merge, kein Key-Verlust)
echo "[*] Konfiguriere Docker Daemon für nftables-Koexistenz..."
mkdir -p /etc/docker
if [ -f /etc/docker/daemon.json ]; then
  # "*" statt "+" für rekursiven Merge — verhindert Verlust verschachtelter Keys
  jq '. * {"iptables": false, "userland-proxy": false, "ipv6": false}' \
    /etc/docker/daemon.json > /tmp/daemon.json
  mv /tmp/daemon.json /etc/docker/daemon.json
else
  cat > /etc/docker/daemon.json <<'EOF'
{
  "iptables": false,
  "userland-proxy": false,
  "ipv6": false,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
fi

# 4. Systemd Boot-Reihenfolge erzwingen
echo "[*] Erstelle Systemd-Overrides für Service-Verkettung..."
mkdir -p /etc/systemd/system/docker.service.d
# BindsTo: Docker stoppt automatisch wenn wg0 ausfällt (kein separater Watchdog nötig)
cat > /etc/systemd/system/docker.service.d/override.conf <<'EOF'
[Unit]
After=nftables.service wg-quick@wg0.service
Requires=nftables.service
BindsTo=wg-quick@wg0.service
EOF
systemctl daemon-reload

# 5. WireGuard konfigurieren
echo "[*] Schreibe WireGuard Konfiguration..."
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
PrivateKey = ${NORD_PRIV}
Address    = 10.5.0.2/32
MTU        = 1380
DNS        = 103.86.96.100, 103.86.99.100
Table      = off

PostUp     = ip rule add not fwmark 51820 table 51820 priority 100
PostUp     = ip route add default dev wg0 table 51820
PreDown    = ip rule del not fwmark 51820 table 51820 priority 100
PreDown    = ip route del default dev wg0 table 51820

[Peer]
PublicKey           = ${NORD_PUB}
Endpoint            = ${NORD_EP}
AllowedIPs          = 0.0.0.0/0
PersistentKeepalive = 25
EOF
chmod 600 /etc/wireguard/wg0.conf
chown root:root /etc/wireguard/wg0.conf

# Secrets aus dem Prozess-Environment entfernen (nicht mehr benötigt)
unset NORD_PRIV

# 6. nftables konfigurieren
echo "[*] Erstelle nftables Regelwerk..."
cat > /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
# ==============================================================================
# Antigravity nftables Regelwerk v2.1
# "destroy" löscht idempotent — kein Fehler wenn Table nicht existiert (nft >= 0.9.3)
# ==============================================================================
destroy table ip  antigravity_nat
destroy table inet antigravity_filter

# ------------------------------------------------------------------------------
# NAT TABLE — Masquerade für Container-Traffic über wg0
# Ohne diese Regel kein Return-Traffic aus dem Internet.
# ------------------------------------------------------------------------------
table ip antigravity_nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr 172.28.0.0/24 oifname "wg0" masquerade
    }
}

# ------------------------------------------------------------------------------
# FILTER TABLE — Forwarding-Kontrolle und Kill-Switch
# Regelreihenfolge ist bewusst: Ausnahmen VOR dem generischen Kill-Switch.
# ------------------------------------------------------------------------------
table inet antigravity_filter {

    chain forward {
        type filter hook forward priority filter; policy drop;

        # 1. Stateful Inspection — bestehende Verbindungen durchlassen
        ct state established,related accept
        ct state invalid drop

        # 2. Intra-Container-Traffic und Bridge-to-Host VOR Kill-Switch
        #    MUSS vor Regel 4 stehen: Intra-Container-Pakete laufen über
        #    br-antigravity, nicht über wg0 — würden sonst vom Kill-Switch gedropt.
        ip saddr 172.28.0.0/24 ip daddr 172.28.0.0/24 accept
        iifname "br-antigravity" ip daddr 172.28.0.1 accept

        # 3. LAN-Isolation: vollständige private Adressbereiche blockieren
        #    RFC-1918: 10/8, 172.16/12, 192.168/16
        #    CGNAT:    100.64/10  (RFC 6598 — Carrier-Grade NAT)
        #    Link-Local: 169.254/16 (RFC 3927 — u.a. Cloud-Metadata-Endpoints)
        ip saddr 172.28.0.0/24 \
            ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16,
                       100.64.0.0/10, 169.254.0.0/16 } drop

        # 4. Kill-Switch: WAN-Traffic NUR über wg0 erlaubt
        ip saddr 172.28.0.0/24 oifname != "wg0" drop

        # 5. VPN-Traffic explizit akzeptieren
        ip saddr 172.28.0.0/24 oifname "wg0" accept
    }

    chain input {
        type filter hook input priority filter; policy accept;
        udp dport 51820 accept
        icmp  type { echo-request, echo-reply } accept
        icmpv6 type { echo-request, echo-reply } accept
    }

    chain output {
        type filter hook output priority filter; policy accept;
    }
}
EOF

# nftables Syntax-Check vor Aktivierung
echo "[*] Verifiziere nftables-Syntax (Dry-Run)..."
if ! nft -c -f /etc/nftables.conf; then
  echo "[!] nftables Syntaxfehler — Deployment abgebrochen."
  exit 1
fi
echo "[+] nftables-Syntax OK."

# 7. Workspace und Secrets vorbereiten
echo "[*] Generiere Workspace unter ${WORKSPACE_DIR}..."
mkdir -p "$SECRETS_DIR"
echo "$JULES_KEY" > "$SECRETS_DIR/jules_api_key.txt"
chmod 700 "$SECRETS_DIR"
chmod 600 "$SECRETS_DIR/jules_api_key.txt"
chown -R "$TARGET_USER":"$TARGET_USER" "$WORKSPACE_DIR"

# Jules Key aus Environment entfernen
unset JULES_KEY

# 8. Dienste aktivieren und neu starten (Reihenfolge: nftables → wg0 → docker)
echo "[*] Wende System-Konfigurationen an..."
systemctl enable --now nftables
systemctl enable --now wg-quick@wg0
systemctl restart docker

echo "============================================================"
echo "[+] Deployment erfolgreich abgeschlossen."
echo "[+] WireGuard-Status:"
wg show wg0 | grep -E "interface|peer|endpoint" || echo "Kein wg0 Interface gefunden."
echo "============================================================"
echo "Workspace: ${WORKSPACE_DIR}"
echo "Nächster Schritt: cd ${WORKSPACE_DIR} && docker compose up -d"
