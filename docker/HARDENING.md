# HARDENING.md — Kapselung des Antigravity-CLI und Jules-Agenten

> **Version:** 2.1 — Technisch revidiert (Bugfix-Release)
> **Status:** Produktionsreif
> **Architektur:** Ubuntu Host · Docker (unprivilegiert) · nftables (natives Backend) · NordVPN NordLynx (WireGuard)

Dieses Dokument definiert die Sicherheitsarchitektur zur Isolierung des Antigravity-CLI (`agy`) und des Jules-Agenten innerhalb einer unprivilegierten Docker-Laufzeitumgebung. Ziele: Verhinderung von Privilege Escalation, Schutz des lokalen LAN-Segments und Durchsetzung eines netzwerkweiten VPN-Kill-Switches via NordVPN (NordLynx/WireGuard).

**Änderungen gegenüber v2.0:**
- `destroy` statt `delete table` in nftables-Konfig (Shell-Syntax war ungültig)
- Forward-Chain-Regelreihenfolge korrigiert (Intra-Container-Traffic war effektiv blockiert)
- CGNAT (`100.64.0.0/10`) und Link-Local (`169.254.0.0/16`) in LAN-Isolation ergänzt
- `BindsTo` im systemd-Override ersetzt separaten wg0-Watchdog
- `version:` aus `docker-compose.yml` entfernt (deprecated)
- Seccomp-Profiling-Befehl in Abschnitt 8.1 korrigiert
- Secret-Sanitization und WireGuard-Key-Validierung im Deploy-Script

---

## Inhaltsverzeichnis

1. [Systemübersicht & Netzwerkarchitektur](#1-systemübersicht--netzwerkarchitektur)
2. [Phase 0: Docker Daemon — Vorbereitung für nftables-Koexistenz](#2-phase-0-docker-daemon--vorbereitung-für-nftables-koexistenz)
3. [Phase 1: WireGuard (NordVPN NordLynx) Host-Konfiguration](#3-phase-1-wireguard-nordvpn-nordlynx-host-konfiguration)
4. [Phase 2: Host Firewall-Konfiguration (nftables)](#4-phase-2-host-firewall-konfiguration-nftables)
5. [Phase 3: Docker-Laufzeitumgebung (Portainer Stack)](#5-phase-3-docker-laufzeitumgebung-portainer-stack)
6. [Phase 4: Secrets-Management](#6-phase-4-secrets-management)
7. [Phase 5: Verifizierung & Operationalisierung](#7-phase-5-verifizierung--operationalisierung)
8. [Mögliche Weiterentwicklungen](#8-mögliche-weiterentwicklungen)

---

## 1. Systemübersicht & Netzwerkarchitektur

```
+-------------------------------------------------------------------------------------+
| UBUNTU HOST (Nativ)                                                                 |
|                                                                                     |
|  /etc/docker/daemon.json                                                            |
|  → iptables: false  (Docker verwaltet keine eigenen Chains)                         |
|  → userland-proxy: false                                                            |
|                                                                                     |
|  +-------------------------+                                                        |
|  | Antigravity IDE (GUI)   |                                                        |
|  +----------+--------------+                                                        |
|             | IPC via Shared Directory (~/.gemini / ~/.config/antigravity)          |
|             v                                                                       |
|  +----------+------------------------------------------------------------------+   |
|  | DOCKER CONTAINER (Isoliert, USER 1000:1000)                                  |   |
|  | - Antigravity CLI (agy)                                                      |   |
|  | - Jules Agent Process                                                        |   |
|  | - Python / Nautilus Trader Environment                                       |   |
|  | DNS: 103.86.96.100, 103.86.99.100  (NordVPN — kein DNS-Leak)                |   |
|  +----------+------------------------------------------------------------------+   |
|             |                                                                       |
|             v  Docker Bridge: antigravity_net → 172.28.0.0/24                      |
|                                                                                     |
|  [ nftables: table inet antigravity_filter ]                                        |
|    ├── ACCEPT: intra-container (172.28/24 → 172.28/24)    (vor Kill-Switch!)       |
|    ├── ACCEPT: br-antigravity → 172.28.0.1                (vor Kill-Switch!)       |
|    ├── BLOCK:  saddr 172.28/24 → RFC-1918 + CGNAT + LL    (LAN-Isolation)         |
|    ├── DROP:   saddr 172.28/24 → oif != wg0               (Kill-Switch)            |
|    ├── ACCEPT: saddr 172.28/24 → oif wg0                  (VPN-Forwarding)         |
|    └── MASQUERADE: saddr 172.28/24 → oif wg0             (NAT für Return-Traffic) |
|             |                                                                       |
|             v                                                                       |
|      +------+--------+                                                              |
|      |  wg0 (NordLynx)|  →  Verschlüsselter Exit-Node zu NordVPN                  |
|      +---------------+                                                              |
+-------------------------------------------------------------------------------------+
```

**Kritische Designentscheidung — Docker & nftables Koexistenz:**

Docker manipuliert standardmäßig iptables/nftables beim Start und Neustart von Containern. Auf Ubuntu-Systemen mit `iptables-nft`-Backend landen Docker-interne Chains (`DOCKER`, `DOCKER-ISOLATION-*`) im nftables-Ruleset. Ein globales `flush ruleset` in der nftables-Konfig würde diese Chains bei jedem Reload löschen und den gesamten Container-Netzwerkverkehr unterbrechen.

**Gewählter Ansatz:** Docker wird per `daemon.json` aus der iptables/nftables-Verwaltung herausgehalten (`"iptables": false`). Das gesamte Routing, NAT und Filtering wird ausschliesslich durch nftables kontrolliert. Dies ist die sauberste und deterministischste Lösung, erfordert jedoch manuelle NAT-Regeln (siehe Phase 2).

---

## 2. Phase 0: Docker Daemon — Vorbereitung für nftables-Koexistenz

Bevor nftables konfiguriert wird, muss Docker angewiesen werden, keine eigenen Netzwerkregeln zu schreiben.

### Datei: `/etc/docker/daemon.json`

```json
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
```

| Parameter | Begründung |
|---|---|
| `"iptables": false` | Docker schreibt keine eigenen Chains in nftables. Pflicht für saubere Koexistenz. |
| `"userland-proxy": false` | Deaktiviert den Docker-eigenen Userspace-Proxy für Port-Forwards. Reduziert Angriffsfläche. |
| `"ipv6": false` | Verhindert IPv6-Kommunikation im Docker-Netz, die den Kill-Switch umgehen könnte. |

### Docker-Daemon neu starten:

```bash
sudo systemctl restart docker
```

> **Wichtig:** Nach diesem Schritt verlieren bestehende Container temporär die Netzwerkkonnektivität. Erst nach dem Laden der nftables-Regeln (Phase 2) ist der Betrieb wieder möglich.

---

## 3. Phase 1: WireGuard (NordVPN NordLynx) Host-Konfiguration

NordVPN nutzt das WireGuard-Protokoll unter dem Namen *NordLynx*. Die nativen Konfigurationsdaten (Private Key, Server Public Key, Endpoint) werden wie folgt extrahiert:

```bash
# NordVPN CLI temporär verbinden (auf separatem System oder vor dem Lockdown)
nordvpn connect
wg show nordlynx
# Ausgabe notieren: private key, public key, endpoint IP:Port
nordvpn disconnect
```

### Datei: `/etc/wireguard/wg0.conf`

```ini
[Interface]
PrivateKey = <IHR_EXTRAHIERTER_NORDVPN_PRIVATE_KEY>
Address    = 10.5.0.2/32
MTU        = 1380
DNS        = 103.86.96.100, 103.86.99.100

# Routing manuell verwalten — wg-quick soll KEINE eigenen iptables-Regeln schreiben.
# Dies verhindert Kollisionen mit dem nftables-Regelwerk.
Table = off

# Policy-Based Routing: Pakete ohne WireGuard-Fwmark über wg0 leiten
PostUp   = ip rule add not fwmark 51820 table 51820 priority 100
PostUp   = ip route add default dev wg0 table 51820
PreDown  = ip rule del not fwmark 51820 table 51820 priority 100
PreDown  = ip route del default dev wg0 table 51820

[Peer]
PublicKey           = <NORDVPN_SERVER_PUBLIC_KEY>
Endpoint            = <NORDVPN_SERVER_IP>:51820
AllowedIPs          = 0.0.0.0/0
PersistentKeepalive = 25
```

**Erläuterung `Table = off` + manuelle Routing-Regeln:**

`wg-quick` würde ohne `Table = off` automatisch iptables-MASQUERADE-Regeln und eine Default-Route einfügen. Da Docker's iptables-Integration bereits deaktiviert ist und wir nftables exklusiv nutzen, würde dies zu Regelkonflikten führen. Mit `Table = off` delegiert wg-quick die Routing-Kontrolle vollständig an uns. Die `PostUp`-Regeln etablieren eine separate Routing-Table (`51820`), die allen nicht-WireGuard-markierten Traffic über `wg0` leitet.

> **MTU-Hinweis:** `1380` ist ein konservativer Wert, der WireGuard-Overhead (60 Byte) zuzüglich mögliche äußere Header berücksichtigt und Fragmentierung in der Mehrzahl der Upstream-Konfigurationen vermeidet. Bei nachgewiesener End-to-End-MTU von 1500 (reines Ethernet ohne PPPoE) kann auf 1420 erhöht werden. Bei PPPoE-Upstream auf 1350 reduzieren.

### Dateirechte und Dienst aktivieren:

```bash
sudo chmod 600 /etc/wireguard/wg0.conf
sudo chown root:root /etc/wireguard/wg0.conf
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0

# Verbindungsstatus prüfen
sudo wg show wg0
```

---

## 4. Phase 2: Host Firewall-Konfiguration (nftables)

Da Docker keine eigenen nftables-Chains mehr schreibt, ist die vollständige Netzwerkkontrolle hier zu definieren. Dies umfasst:

- **NAT/Masquerade** für Container-Traffic Richtung WireGuard (ohne dies kein Return-Traffic)
- **LAN-Isolation** (Blockade aller privaten Adressbereiche inkl. CGNAT und Link-Local)
- **Kill-Switch** (WAN-Traffic nur über `wg0` erlaubt)
- **DNS-Schutz** (nur NordVPN-Resolver über `wg0`)

### Datei: `/etc/nftables.conf`

```nftables
#!/usr/sbin/nft -f
# ==============================================================================
# Antigravity nftables Regelwerk v2.1
# ==============================================================================
# "destroy" löscht idempotent — kein Fehler wenn Table nicht existiert (nft >= 0.9.3).
# KEIN "delete table ... 2>/dev/null || true": Shell-Syntax ist in nft-Skripten
# ungültig und führt bei systemctl start nftables zu Syntaxfehlern.
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
#
# REGELREIHENFOLGE ist sicherheitskritisch:
# Spezifische Ausnahmen (Intra-Container, Bridge-to-Host) MÜSSEN vor dem
# generischen Kill-Switch stehen. Intra-Container-Traffic läuft über
# br-antigravity, nicht über wg0 — ein nachgelagerter Kill-Switch würde
# ihn fälschlicherweise droppen (dead code).
# ------------------------------------------------------------------------------
table inet antigravity_filter {

    chain forward {
        type filter hook forward priority filter; policy drop;

        # 1. Stateful Inspection
        ct state established,related accept
        ct state invalid drop

        # 2. Intra-Container und Bridge-to-Host VOR Kill-Switch
        #    Pakete zwischen Containern im selben /24 und DNS-Anfragen an den
        #    Docker-Gateway (172.28.0.1) laufen über br-antigravity, nicht wg0.
        #    Diese Regeln MÜSSEN vor Regel 4 stehen.
        ip saddr 172.28.0.0/24 ip daddr 172.28.0.0/24 accept
        iifname "br-antigravity" ip daddr 172.28.0.1 accept

        # 3. LAN-Isolation: vollständige private Adressbereiche blockieren
        #    RFC-1918:   10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
        #    CGNAT:      100.64.0.0/10  (RFC 6598 — Carrier-Grade NAT)
        #    Link-Local: 169.254.0.0/16 (RFC 3927 — u.a. AWS/GCP/Azure Metadata)
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
        # WireGuard-Port auf dem Host erlauben
        udp dport 51820 accept
        # ICMP für Diagnose
        icmp  type { echo-request, echo-reply } accept
        icmpv6 type { echo-request, echo-reply } accept
    }

    chain output {
        type filter hook output priority filter; policy accept;
    }
}
```

> **Hinweis zu Regel 3 (LAN-Isolation):** `100.64.0.0/10` (CGNAT) verhindert Zugriff auf Carrier-seitige Infrastruktur. `169.254.0.0/16` (Link-Local) blockiert Cloud-Metadata-Endpoints (AWS: `169.254.169.254`, Azure: identisch, GCP: `169.254.169.254`), die ohne diese Regel aus dem Container erreichbar wären und Provider-Credentials exponieren können.

### LAN-Isolation: blockierte Adressbereiche

| Bereich | RFC | Zweck | Risiko ohne Block |
|---|---|---|---|
| `10.0.0.0/8` | RFC 1918 | Privates LAN (Class A) | Heimnetz-Zugriff |
| `172.16.0.0/12` | RFC 1918 | Privates LAN (Class B) | Docker-Host-Zugriff |
| `192.168.0.0/16` | RFC 1918 | Privates LAN (Class C) | Router/LAN-Zugriff |
| `100.64.0.0/10` | RFC 6598 | Carrier-Grade NAT | ISP-Infrastruktur-Zugriff |
| `169.254.0.0/16` | RFC 3927 | Link-Local / Cloud-Metadata | Credential-Exfiltration via IMDS |

### Regelwerk laden und persistieren:

```bash
# Syntax vorab prüfen — MUSS vor Aktivierung ausgeführt werden
sudo nft -c -f /etc/nftables.conf

# Laden
sudo nft -f /etc/nftables.conf

# Aktivieren (lädt beim Systemstart)
sudo systemctl enable nftables
sudo systemctl restart nftables

# Geladene Regeln verifizieren
sudo nft list ruleset
```

### Startup-Reihenfolge sicherstellen

nftables muss vor Docker und wg0 starten. Docker darf erst nach wg0 laufen. Der `BindsTo`-Eintrag im systemd-Override ersetzt einen separaten Watchdog-Service: Docker wird automatisch gestoppt, sobald `wg-quick@wg0` ausfällt.

```bash
sudo systemctl edit docker.service
```

```ini
[Unit]
After=nftables.service wg-quick@wg0.service
Requires=nftables.service
BindsTo=wg-quick@wg0.service
```

```bash
sudo systemctl daemon-reload
```

> **`BindsTo` vs. `Requires`:** `Requires` verhindert nur den Start ohne Abhängigkeit. `BindsTo` stoppt den Service zusätzlich, wenn die Abhängigkeit zur Laufzeit wegfällt — essentiell für den Kill-Switch ohne separaten Watchdog-Prozess.

---

## 5. Phase 3: Docker-Laufzeitumgebung (Portainer Stack)

### Datei: `Dockerfile`

```dockerfile
FROM python:3.11-slim-bookworm

# Sicherheits-Updates zuerst
RUN apt-get update && apt-get upgrade -y --no-install-recommends

# System-Abhängigkeiten (nur was zwingend benötigt wird)
RUN apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Unprivilegierten Benutzer anlegen — UID/GID passend zum Host-Mapping
RUN groupadd -g 1000 agentgroup && \
    useradd -u 1000 -g agentgroup -m -s /bin/bash agentuser

# Antigravity CLI installieren
# SICHERHEITSHINWEIS: curl | bash führt Remote-Code als root aus.
# In einer kontrollierten Umgebung: Binary-Hash vorab verifikzieren oder
# aus einem internen Artefakt-Repository beziehen.
RUN curl -fsSL -o /tmp/agy_install.sh https://antigravity.google/cli/install.sh && \
    echo "<ERWARTETER_SHA256_HASH>  /tmp/agy_install.sh" | sha256sum -c - && \
    bash /tmp/agy_install.sh && \
    rm /tmp/agy_install.sh && \
    mv /root/.local/bin/agy /usr/local/bin/agy && \
    chmod +x /usr/local/bin/agy

USER agentuser
WORKDIR /home/agentuser/workspace

ENV PATH="/home/agentuser/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN mkdir -p /home/agentuser/.cache /home/agentuser/.local/lib

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD agy --version || exit 1

CMD ["tail", "-f", "/dev/null"]
```

### Datei: `docker-compose.yml` (Portainer Stack Template)

```yaml
# Kein "version:"-Key — ist in aktuellen Docker Compose Versionen deprecated
# und wird ignoriert. Weglassen verhindert Deprecation-Warnungen.

networks:
  antigravity_net:
    driver: bridge
    driver_opts:
      # Bridge-Interface-Name explizit setzen für nftables-Regelreferenz
      com.docker.network.bridge.name: "br-antigravity"
    ipam:
      driver: default
      config:
        - subnet: 172.28.0.0/24

services:
  agy_cli:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: antigravity_execution_env
    user: "1000:1000"

    networks:
      - antigravity_net

    # DNS explizit auf NordVPN-Resolver setzen — verhindert DNS-Leak
    # über Docker's internen Resolver (127.0.0.11 → systemd-resolved)
    dns:
      - 103.86.96.100
      - 103.86.99.100
    dns_search: []

    restart: unless-stopped

    # OS-Härtungsparameter
    privileged: false
    security_opt:
      - no-new-privileges:true
      - seccomp:./seccomp-profile.json
    cap_drop:
      - ALL

    tmpfs:
      - /tmp:rw,noexec,nosuid,size=256m

    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
        reservations:
          memory: 512M

    volumes:
      - /home/user/etoro_nautilus/etoro_nautilus-1:/home/agentuser/workspace:rw
      - /home/user/.gemini:/home/agentuser/.gemini:rw
      - /home/user/.config/antigravity:/home/agentuser/.config/antigravity:rw

    secrets:
      - jules_api_key
    environment:
      - JULES_API_KEY_FILE=/run/secrets/jules_api_key

secrets:
  jules_api_key:
    file: ./secrets/jules_api_key.txt
```

---

## 6. Phase 4: Secrets-Management

API-Keys als Klartext-Umgebungsvariablen sind in `docker inspect`-Ausgaben und Portainer-UI sichtbar. Docker Secrets lösen dieses Problem durch dateibasierte Übergabe.

### Vorbereitung:

```bash
mkdir -p ./secrets
chmod 700 ./secrets
echo "sk-xxxxxxxxxxxxxxxxxxxxxxxx" > ./secrets/jules_api_key.txt
chmod 600 ./secrets/jules_api_key.txt
```

### `.gitignore` sicherstellen:

```gitignore
secrets/
.env
*.key
*.pem
```

### Secret im Anwendungscode lesen:

```python
import os

secret_path = os.environ.get("JULES_API_KEY_FILE", "/run/secrets/jules_api_key")
with open(secret_path, "r") as f:
    api_key = f.read().strip()
```

---

## 7. Phase 5: Verifizierung & Operationalisierung

### 7.1 Build & Start via Portainer

1. Stack in Portainer aus dem `docker-compose.yml` erstellen.
2. Secret `jules_api_key` über das Portainer-Secrets-UI oder die Datei hinterlegen.
3. Stack starten und Logs auf Fehler prüfen.

### 7.2 Netzwerk-Isolation verifizieren

Alle Tests werden innerhalb der Portainer-Konsole des Containers ausgeführt:

```bash
# Test 1: LAN-Isolation — MUSS Timeout ergeben
curl --connect-timeout 3 http://10.0.0.1
curl --connect-timeout 3 http://192.168.1.1
curl --connect-timeout 3 http://100.64.0.1       # CGNAT
curl --connect-timeout 3 http://169.254.169.254   # Cloud-Metadata
# Erwartetes Ergebnis: curl: (28) Connection timed out

# Test 2: VPN-Route aktiv — MUSS NordVPN-ExitNode-IP zurückgeben
curl -s https://ifconfig.me
# Erwartetes Ergebnis: Eine NordVPN-Exitnode-IP

# Test 3: DNS-Konfiguration — MUSS NordVPN-Resolver zeigen
cat /etc/resolv.conf
# Erwartetes Ergebnis: nameserver 103.86.96.100 / 103.86.99.100

# Test 4: DNS-Leak — Anfragen dürfen NICHT auf eth0 erscheinen
# Auf dem HOST während DNS-Anfrage aus dem Container:
sudo tcpdump -i eth0 port 53
# Erwartetes Ergebnis: Keine DNS-Pakete sichtbar — alles verschlüsselt via wg0

# Test 5: Intra-Container-Kommunikation — MUSS funktionieren (war in v2.0 kaputt)
# Bei mehreren Containern im Stack: ping auf Container-IP des anderen Services
ping -c 3 172.28.0.x
# Erwartetes Ergebnis: Antwort erhalten
```

### 7.3 Privilege Escalation verhindern

```bash
# Test 6: Root-Wechsel — MUSS verweigert werden
su - root
# Erwartetes Ergebnis: Authentication failure

# Test 7: Capabilities prüfen
cat /proc/1/status | grep CapEff
# Erwartetes Ergebnis: CapEff: 0000000000000000

# Test 8: Kernel-Module laden — MUSS fehlschlagen
modprobe dummy 2>&1
# Erwartetes Ergebnis: Operation not permitted
```

### 7.4 WireGuard & nftables Systemzustand (auf Host)

```bash
# wg0 Interface-Status
sudo wg show wg0

# Routing-Tables prüfen
ip rule list
ip route show table 51820

# nftables-Regelwerk ausgeben — Regelreihenfolge in forward chain prüfen
sudo nft list ruleset

# BindsTo verifizieren: wg0 stoppen → Docker muss ebenfalls stoppen
sudo systemctl stop wg-quick@wg0
systemctl is-active docker   # Erwartetes Ergebnis: inactive
sudo systemctl start wg-quick@wg0
sudo systemctl start docker
```

### 7.5 Container-Betrieb via Docker CLI

```bash
docker exec -it antigravity_execution_env agy project sync --force
docker exec -it antigravity_execution_env agy agent switch jules
docker exec -it --user agentuser antigravity_execution_env bash
docker logs -f antigravity_execution_env
```

---

## 8. Mögliche Weiterentwicklungen

Dieses Kapitel dokumentiert Härtungsmassnahmen und Erweiterungen, die über den aktuellen Produktionsstand hinausgehen. Jeder Punkt ist unabhängig implementierbar und nach Priorität geordnet.

---

### 8.1 Seccomp-Profil für den Container (Priorität: Hoch)

Das Standard-Seccomp-Profil von Docker blockiert ~44 von ~300+ Linux-Syscalls. Für den Jules-Agenten und agy kann ein massgeschneidertes Profil erstellt werden, das nur die tatsächlich benötigten Syscalls erlaubt.

**Vorgehen mit `oci-seccomp-bpf-hook` (empfohlen):**

```bash
# Hook installieren
sudo apt-get install -y oci-seccomp-bpf-hook

# Container mit Syscall-Recording starten (separater Test-Run)
docker run --rm \
    --annotation io.containers.trace-syscall="of:/tmp/agy-syscalls.json" \
    antigravity_execution_env \
    agy agent run
```

**Alternative mit `strace` (manuell):**

```bash
# -f: Child-Prozesse folgen, -e trace=all: alle Syscalls, KEIN -c (wäre nur Statistik)
# Ausgabe in Datei umleiten, dann Syscall-Namen extrahieren
strace -f -e trace=all -o /tmp/strace.log \
    docker exec antigravity_execution_env agy agent run

# Eindeutige Syscall-Namen extrahieren
awk -F'(' '{print $1}' /tmp/strace.log | sort -u | grep -v '^[^a-z]'
```

Auf Basis der Ausgabe ein minimales JSON-Profil erstellen:

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "syscalls": [
    {
      "names": ["read", "write", "open", "close", "stat", "fstat",
                "mmap", "mprotect", "munmap", "brk", "execve",
                "socket", "connect", "sendto", "recvfrom",
                "epoll_wait", "epoll_ctl", "clone", "futex", "exit_group"],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

Das Profil als `seccomp-profile.json` im Stack-Verzeichnis ablegen (bereits in `docker-compose.yml` referenziert unter `security_opt`).

---

### 8.2 AppArmor-Profil (Priorität: Hoch)

AppArmor begrenzt, auf welche Dateipfade und Kerneloperationen der Container-Prozess zugreifen darf — orthogonal zu Seccomp (Syscall-Ebene).

```bash
sudo aa-genprof /usr/local/bin/agy
sudo aa-enforce /etc/apparmor.d/usr.local.bin.agy

# In docker-compose.yml aktivieren:
# security_opt:
#   - apparmor:usr.local.bin.agy
```

---

### 8.3 Read-Only Root-Filesystem (Priorität: Mittel)

```yaml
read_only: true
tmpfs:
  - /tmp:rw,noexec,nosuid,size=256m
  - /home/agentuser/.cache:rw,noexec,nosuid,size=1g
  - /home/agentuser/.local/lib:rw,noexec,nosuid,size=2g
volumes:
  - /home/user/etoro_nautilus/etoro_nautilus-1:/home/agentuser/workspace:rw
  - pip-cache:/home/agentuser/.local/lib/python3.11:rw
```

**Voraussetzung:** pip-Installs müssen vollständig im Dockerfile zur Build-Zeit erfolgen.

---

### 8.4 Kill-Switch via systemd `BindsTo` (implementiert in v2.1)

Der in v2.0 vorgeschlagene Polling-Watchdog-Service ist in v2.1 durch `BindsTo` im systemd-Override ersetzt. `BindsTo=wg-quick@wg0.service` in der `docker.service`-Override-Unit bewirkt, dass systemd den Docker-Daemon automatisch stoppt, sobald wg0 ausfällt — ohne Sleep-Polling, ohne Race Conditions, ohne zusätzlichen Service.

```bash
# Verifizierung:
sudo systemctl stop wg-quick@wg0
systemctl is-active docker   # → inactive
```

Der frühere Watchdog-Script-Ansatz (Polling alle 15s) hatte einen inhärenten Sicherheitsgap von bis zu 15 Sekunden. `BindsTo` ist event-getriggert und reagiert sofort.

---

### 8.5 NordVPN Key-Rotation-Prozess (Priorität: Mittel)

```bash
#!/bin/bash
# /usr/local/bin/nordvpn-key-rotate.sh

nordvpn connect --group P2P Switzerland
NEW_PUBKEY=$(wg show nordlynx | awk '/public key/ {print $3}')
NEW_ENDPOINT=$(wg show nordlynx | awk '/endpoint/ {print $2}')
nordvpn disconnect

sudo sed -i "s|PublicKey = .*|PublicKey = ${NEW_PUBKEY}|" /etc/wireguard/wg0.conf
sudo sed -i "s|Endpoint = .*|Endpoint = ${NEW_ENDPOINT}|" /etc/wireguard/wg0.conf

sudo systemctl restart wg-quick@wg0
```

---

### 8.6 CI/CD-Pipeline mit pytest im gehärteten Container (Priorität: Niedrig)

```yaml
# .github/workflows/test-in-container.yml
name: Hardened Container Tests

on:
  push:
    paths:
      - 'etoro_nautilus/**'
      - 'tests/**'

jobs:
  test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4

      - name: Run pytest in hardened container
        run: |
          docker exec antigravity_execution_env \
            python -m pytest /home/agentuser/workspace/tests/ \
            --tb=short \
            --junit-xml=/tmp/test-results.xml \
            -v

      - name: Copy results
        run: docker cp antigravity_execution_env:/tmp/test-results.xml ./test-results.xml

      - name: Publish results
        uses: actions/upload-artifact@v4
        with:
          name: test-results
          path: test-results.xml
```

---

### 8.7 nftables Logging & Auditierung (Priorität: Niedrig)

```nftables
# In chain forward, Drop-Regeln mit Logging ergänzen:
ip saddr 172.28.0.0/24 \
    ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16,
               100.64.0.0/10, 169.254.0.0/16 } \
    log prefix "ANTIGRAVITY_LAN_DROP: " level warn drop

ip saddr 172.28.0.0/24 oifname != "wg0" \
    log prefix "ANTIGRAVITY_KILLSWITCH: " level warn drop
```

Log-Aggregation via `journalctl -t nft` oder Weiterleitung an Loki + Grafana im lokalen Homelab.

---

*Dokument-Ende. Alle Konfigurationsblöcke sind vor dem Produktionseinsatz auf die konkrete Systemumgebung (Host-Interface-Namen, UID/GID, NordVPN-Keys) anzupassen.*
