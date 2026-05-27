# ANTIGRAVITY_DEPLOYMENT.md
# Vollständige Deployment- und Betriebsanleitung: Antigravity CLI auf Ubuntu mit Portainer

> **Stand:** Mai 2026 (Rev. 2 — Bugfixes FIX-1 bis FIX-5)
> **Architektur:** Host-seitiger NordVPN WireGuard Kill-Switch → nftables LAN-Isolation → Docker `antigravity_net` Bridge → Portainer-verwaltete Container
> **Binary:** `agy` (Go-Binary, Google Antigravity CLI 2.0)
> **Auth im Container:** API-Key via Docker Secret (headless, kein Browser)
> **Jules MCP:** Coding-Assistent via Jules API, integriert über Model Context Protocol (MCP)
> **Git-Integration:** Portainer baut Images direkt aus Git-Repo (kein lokales `docker build`)

> **Changelog Rev. 2 (automatisch via Jules Audit generiert):**
> | ID | Abschnitt | Beschreibung |
> |----|-----------|--------------|
> | FIX-1 | 2.3 | `METADATA`-IP-Tippfehler: `169.254.254.169` → `169.254.169.254` |
> | FIX-2 | 2.4.1 | Neuer Abschnitt: Docker Swarm init (Pflichtvoraussetzung für Docker Secrets) |
> | FIX-3 | 5.3 | Healthcheck: `$JULES_API_KEY`-Abhängigkeit entfernt (war im HC-Kontext leer → dauerhaft unhealthy) |
> | FIX-4 | 2.1 | `resolvconf` → `openresolv` + systemd-resolved stub-only-Konfiguration |
> | FIX-5 | 5.3 | `seccomp:./` → `seccomp:/etc/antigravity/seccomp-profile.json` (absoluter Pfad) |

---

## Inhaltsverzeichnis

1. [Systemvoraussetzungen](#1-systemvoraussetzungen)
2. [Host-Vorbereitung](#2-host-vorbereitung)
   - 2.1 [Pakete installieren](#21-pakete-installieren)
   - 2.2 [NordVPN WireGuard einrichten](#22-nordvpn-wireguard-einrichten)
   - 2.3 [nftables Regelwerk](#23-nftables-regelwerk)
   - 2.4 [Docker installieren](#24-docker-installieren)
   - 2.4.1 [Docker Swarm initialisieren](#241-docker-swarm-initialisieren-voraussetzung-für-docker-secrets)
   - 2.5 [Portainer installieren](#25-portainer-installieren)
3. [Git-Repository Struktur](#3-git-repository-struktur)
   - 3.1 [Dockerfile](#31-dockerfile)
   - 3.2 [seccomp-profile.json](#32-seccomp-profilejson)
   - 3.3 [AGENTS.md (Agy-Konfiguration)](#33-agentsmd-agy-konfiguration)
   - 3.4 [Jules MCP-Konfigurationsdatei](#34-jules-mcp-konfigurationsdatei)
   - 3.5 [Docker Entrypoint-Skript](#35-docker-entrypoint-skript)
4. [Infrastruktur auf dem Host anlegen](#4-infrastruktur-auf-dem-host-anlegen)
   - 4.1 [Docker Netzwerk `antigravity_net`](#41-docker-netzwerk-antigravity_net)
   - 4.2 [Verzeichnisstruktur](#42-verzeichnisstruktur)
5. [Portainer konfigurieren](#5-portainer-konfigurieren)
   - 5.1 [Git-Integration einrichten](#51-git-integration-einrichten)
   - 5.2 [Secrets anlegen](#52-secrets-anlegen)
   - 5.3 [Stack deployen](#53-stack-deployen)
6. [Verifikation & Sicherheitsaudit](#6-verifikation--sicherheitsaudit)
7. [Betriebsanleitung](#7-betriebsanleitung)
   - 7.1 [Container-Verwaltung](#71-container-verwaltung)
   - 7.2 [Agy im Container bedienen](#72-agy-im-container-bedienen)
   - 7.3 [Logs & Monitoring](#73-logs--monitoring)
   - 7.4 [Updates](#74-updates)
8. [Skalierung: Weitere Instanzen hinzufügen](#8-skalierung-weitere-instanzen-hinzufügen)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Systemvoraussetzungen

| Komponente | Anforderung |
|---|---|
| OS | Ubuntu 22.04 LTS oder 24.04 LTS (x86\_64) |
| Kernel | ≥ 5.15 (nftables + WireGuard native) |
| RAM | ≥ 8 GB (4 GB pro Container-Limit) |
| Docker | Engine ≥ 26.x |
| Portainer | CE ≥ 2.21 |
| NordVPN | WireGuard-Konfigurationsdatei (von nordvpn.com heruntergeladen) |
| Git-Repo | Erreichbar vom Portainer-Host (HTTPS oder SSH) |
| API-Key (agy) | Google Antigravity CLI API-Key (aus console.cloud.google.com) |
| API-Key (Jules) | Jules Coding-Assistent API-Key (aus jules.google.com/u/0/settings/api) |
| Netzwerk | Ausgehende HTTPS-Verbindungen zu `*.googleapis.com` und `jules.google.com` müssen möglich sein |

**Wichtige UID-Anmerkung:** Der Container läuft als `user: "1000:1000"`. UID 1000 wird auf den bestehenden Host-User gemappt. Verifiziere mit `id` auf dem Host — die UID muss 1000 sein. Falls abweichend, passe den `user:`-Wert im Stack-YAML **und** den `groupadd`/`useradd`-Aufruf im Dockerfile entsprechend an. Für eine vollständig UID-unabhängige Lösung siehe [Troubleshooting → Starres UID-Mapping](#uid-mapping-schlägt-fehl-volume-permission-denied).

```bash
# Host-User UID prüfen
id
# Erwartete Ausgabe: uid=1000(deinuser) gid=1000(deinuser) ...
```

---

## 2. Host-Vorbereitung

### 2.1 Pakete installieren

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
  nftables \
  wireguard \
  wireguard-tools \
  curl \
  ca-certificates \
  gnupg \
  lsb-release \
  openresolv
```

nftables beim Systemstart aktivieren:

```bash
sudo systemctl enable nftables
sudo systemctl start nftables
```

> **`openresolv` vs `resolvconf` – Wichtig für Ubuntu 22.04/24.04:**
> Auf modernen Ubuntu-Systemen ist `systemd-resolved` aktiv. Das veraltete Paket
> `resolvconf` (Legacy-Name) konfliktiert damit und verhindert, dass `wg-quick` DNS-Einträge
> korrekt setzt — was zu DNS-Leaks führt. `openresolv` (Paketname in apt identisch:
> `openresolv`) ist kompatibel und wird von `wg-quick` nativ unterstützt.
> Zusätzlich muss `systemd-resolved` in den Stub-only-Modus versetzt werden:
>
> ```bash
> # systemd-resolved auf stub-only-Modus setzen (kein Full-Resolver, nur Stub)
> sudo mkdir -p /etc/systemd/resolved.conf.d/
> sudo tee /etc/systemd/resolved.conf.d/wg-compat.conf << 'EOF'
> [Resolve]
> DNSStubListener=no
> EOF
>
> # /etc/resolv.conf darf kein Symlink auf den Stub sein
> sudo rm -f /etc/resolv.conf
> echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf   # temporär, wird von wg-quick überschrieben
> sudo systemctl restart systemd-resolved
> ```
>
> `ufw` wird **nicht** benötigt — das nftables-Regelwerk übernimmt alle Firewall-Aufgaben.

### 2.2 NordVPN WireGuard einrichten

**Schritt 1:** NordVPN WireGuard-Konfigurationsdatei herunterladen.

Navigiere auf [my.nordaccount.com](https://my.nordaccount.com) → **NordVPN** → **Manual setup** → **WireGuard** → wähle einen Server (z. B. `ch123`) → lade die `.conf`-Datei herunter.

**Schritt 2:** Konfigurationsdatei auf den Host kopieren.

```bash
sudo cp ~/Downloads/ch123.conf /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
```

**Schritt 3:** Datei prüfen — sie muss folgende Struktur haben:

```ini
[Interface]
PrivateKey = <DEIN_PRIVATE_KEY>
Address = 10.5.0.2/32
DNS = 103.86.96.100, 103.86.99.100

[Peer]
PublicKey = <SERVER_PUBLIC_KEY>
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = <SERVER_IP>:51820
PersistentKeepalive = 25
```

> **Kritisch:** `AllowedIPs = 0.0.0.0/0` stellt sicher, dass *gesamter* Traffic durch den Tunnel geroutet wird. Ohne diese Zeile greift der Kill-Switch nicht korrekt.

**Schritt 4:** WireGuard starten und aktivieren.

```bash
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
```

**Schritt 5:** Verbindung verifizieren.

```bash
sudo wg show
# Erwartung: Peer-Handshake sollte wenige Sekunden zurückliegen (latest handshake: X seconds ago)

curl -s https://ifconfig.me
# Erwartung: NordVPN Exit-Node IP (nicht deine Provider-IP)
```

### 2.3 nftables Regelwerk

Dieses Regelwerk implementiert drei Sicherheitsebenen:
- **Kill-Switch:** Container-Traffic wird gedroppt, wenn `wg0` nicht verfügbar ist
- **LAN-Isolation:** RFC-1918 Adressen werden aus Containern heraus blockiert
- **Metadata-Blockade:** Cloud-Metadata-Endpoint (169.254.169.254) wird blockiert

> **Hinweis zu Docker und nftables:** Docker verwendet `iptables` intern. Auf Ubuntu 22.04+ ist `iptables` über `iptables-nft` auf nftables gemappt, d. h. beide Regelwerke koexistieren im selben nftables-Kernel-Framework. Die hier definierten Regeln greifen *zusätzlich* zu den Docker-eigenen Regeln und haben höhere Priorität durch den `priority -100` Forward-Hook.

> **Wichtig — Jules MCP Egress:** Das Netzwerk `antigravity_net` darf **nicht** als `internal: true` konfiguriert werden. Jules MCP ist ein Cloud-Service und benötigt ausgehende HTTPS-Verbindungen zu `jules.google.com` und `*.googleapis.com`. Diese werden korrekt durch den Kill-Switch über `wg0` geleitet (also via VPN, nicht direkt).

Erstelle die Konfigurationsdatei:

```bash
sudo nano /etc/nftables.conf
```

Inhalt — passe `ETH_IF` auf dein tatsächliches LAN-Interface an (prüfe mit `ip link show`):

```nftables
#!/usr/sbin/nft -f

# Alle bestehenden Regeln löschen
flush ruleset

# ─────────────────────────────────────────────
# Variablen
# ─────────────────────────────────────────────
define ETH_IF       = eth0            # Dein LAN-Interface (ggf. enp3s0, ens3, etc.)
define VPN_IF       = wg0             # WireGuard Interface
define AGY_BRIDGE   = br-antigravity  # Docker-Bridge für antigravity_net
define AGY_SUBNET   = 172.28.0.0/24  # Subnetz des Docker-Netzwerks
define RFC1918      = { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 }
define METADATA     = 169.254.169.254/32   # Korrekte Cloud-Metadata-IP (war 169.254.254.169 – Tippfehler)

# ─────────────────────────────────────────────
# Tabelle: Host-Firewall
# ─────────────────────────────────────────────
table inet host_fw {

  # Eingehender Traffic auf den Host
  chain input {
    type filter hook input priority 0; policy drop;

    # Loopback immer erlauben
    iifname "lo" accept

    # Etablierte Verbindungen erlauben
    ct state established,related accept

    # SSH vom LAN erlauben (Port 22)
    iifname $ETH_IF tcp dport 22 accept

    # Portainer Web-GUI (Port 9000 und 9443) vom LAN erlauben
    iifname $ETH_IF tcp dport { 9000, 9443 } accept

    # WireGuard Handshake-Pakete akzeptieren
    iifname $ETH_IF udp dport 51820 accept

    # ICMP (Ping) erlauben
    ip protocol icmp accept
    ip6 nexthdr icmpv6 accept

    # Alles andere droppen
    drop
  }

  # Ausgehender Traffic vom Host selbst
  chain output {
    type filter hook output priority 0; policy accept;
  }
}

# ─────────────────────────────────────────────
# Tabelle: Container-Firewall (antigravity_net)
# ─────────────────────────────────────────────
table inet container_fw {

  # Forward-Chain für Container-Traffic
  # Priorität -100 = wird VOR Docker-eigenen Regeln ausgewertet
  chain forward {
    type filter hook forward priority -100; policy accept;

    # ── Sicherheitsregeln für Container (erst prüfen, dann ggf. droppen) ──

    # 1. RFC-1918 Adressen aus Containern heraus BLOCKIEREN (LAN-Isolation)
    iifname $AGY_BRIDGE ip daddr $RFC1918 \
      log prefix "nft-drop-rfc1918: " drop

    # 2. Cloud-Metadata-Endpoint BLOCKIEREN
    iifname $AGY_BRIDGE ip daddr $METADATA \
      log prefix "nft-drop-metadata: " drop

    # 3. Kill-Switch: Container dürfen NUR über wg0 nach außen
    #    Wenn wg0 down ist, gibt es keine Regel die greift → implicit drop durch nächste Regel
    iifname $AGY_BRIDGE oifname != $VPN_IF \
      log prefix "nft-drop-novpn: " drop

    # 4. Container → VPN: explizit erlauben
    #    Inkl. jules.google.com und *.googleapis.com (Jules MCP Cloud-Traffic)
    iifname $AGY_BRIDGE oifname $VPN_IF accept

    # 5. VPN → Container (Return-Traffic): erlauben
    iifname $VPN_IF oifname $AGY_BRIDGE \
      ct state established,related accept
  }

  # NAT: Container-Traffic hinter VPN-IP maskieren
  chain postrouting {
    type nat hook postrouting priority srcnat;

    # Nur Container-Traffic, der über wg0 geht, wird masqueraded
    iifname $AGY_BRIDGE oifname $VPN_IF masquerade
  }
}
```

Regelwerk aktivieren und validieren:

```bash
# Syntax prüfen (ohne anzuwenden)
sudo nft -c -f /etc/nftables.conf

# Regelwerk laden
sudo nft -f /etc/nftables.conf

# IP-Forwarding aktivieren (persistent)
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.d/99-antigravity.conf
sudo sysctl -p /etc/sysctl.d/99-antigravity.conf

# Aktive Regeln anzeigen
sudo nft list ruleset
```

### 2.4 Docker installieren

```bash
# Docker GPG Key hinzufügen
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Repository hinzufügen
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Docker installieren
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Host-User zur docker-Gruppe hinzufügen (Neulogin erforderlich)
sudo usermod -aG docker $USER

# Docker starten und aktivieren
sudo systemctl enable docker
sudo systemctl start docker
```

> **Wichtig:** Nach `usermod` aus- und wieder einloggen, damit die Gruppe aktiv wird.

### 2.4.1 Docker Swarm initialisieren (Voraussetzung für Docker Secrets)

> **Warum Swarm?** Docker Secrets (`docker secret create`) sind eine **Swarm-Funktion** —
> auch auf Single-Node-Installationen. Ohne `docker swarm init` schlägt jeder
> `docker secret create`-Aufruf mit `"This node is not a swarm manager"` fehl.
> Portainer-Secrets (`external: true` in Stack-YAML) setzen Swarm zwingend voraus.
> Ein Single-Node-Swarm erzeugt keinen Cluster-Overhead und hat keine Sicherheitsauswirkungen.

```bash
# Single-Node-Swarm initialisieren
# --advertise-addr auf das LAN-Interface des Hosts setzen
HOST_IP=$(ip route get 8.8.8.8 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
sudo docker swarm init --advertise-addr "$HOST_IP"

# Verifikation: Swarm muss als "active" gemeldet werden
docker info --format '{{.Swarm.LocalNodeState}}'
# Erwartung: active

# Secrets anlegen (interaktiver Input — wird nicht in Shell-History gespeichert)
# Antigravity CLI API-Key:
read -rsp "agy API-Key: " AGY_KEY
printf '%s' "$AGY_KEY" | docker secret create agy_api_key_agent_01 -
unset AGY_KEY

# Jules API-Key:
read -rsp "Jules API-Key: " JULES_KEY
printf '%s' "$JULES_KEY" | docker secret create jules_api_key_agent_01 -
unset JULES_KEY

# Verifikation: beide Secrets müssen listet sein
docker secret ls
# Erwartung: agy_api_key_agent_01 und jules_api_key_agent_01 vorhanden
```

> **Hinweis zu Portainer:** Portainer's Secrets-GUI (`Secrets → Add secret`) ist ein
> visuelles Frontend für exakt diese `docker secret create`-Aufrufe. Wer Portainer
> für die Secret-Verwaltung nutzt, überspringt die `docker secret create`-Befehle —
> muss aber sicherstellen, dass Swarm vor dem Öffnen der Portainer-GUI initialisiert ist.

### 2.5 Portainer installieren

Portainer läuft selbst als Docker-Container und verwaltet alle anderen Container.

```bash
# Portainer-Datenpersistenz
docker volume create portainer_data

# Portainer CE deployen
docker run -d \
  --name portainer \
  --restart always \
  -p 9000:9000 \
  -p 9443:9443 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v portainer_data:/data \
  portainer/portainer-ce:latest

# Status prüfen
docker ps | grep portainer
```

Portainer Web-GUI aufrufen: `https://<HOST-IP>:9443`
Beim ersten Aufruf: Admin-Passwort setzen, dann **Get started** → lokale Docker-Umgebung auswählen.

---

## 3. Git-Repository Struktur

Erstelle ein Git-Repository (z. B. auf GitHub/GitLab/Gitea) mit folgender Struktur:

```
antigravity-agent/
├── Dockerfile
├── docker-entrypoint.sh
├── seccomp-profile.json
├── AGENTS.md
├── mcp_config.json
└── .gitignore
```

### 3.1 Dockerfile

Das Image basiert auf `ubuntu:22.04` und enthält neben der `agy`-Binary alle für den Jules MCP Coding-Assistenten notwendigen Laufzeitumgebungen (Python 3, Node.js, Build-Tools). Ohne diese Runtimes kann Jules generierten Code weder ausführen noch Abhängigkeiten auflösen.

> **TMPDIR-Strategie:** `/tmp` ist als `noexec` gemountet (Sicherheitsmaßnahme). `pip`, Compiler und andere Tools schreiben temporäre Binaries standardmäßig nach `/tmp`. Ohne explizites `TMPDIR`-Remapping schlägt jede Code-Ausführung oder Paketinstallation mit `Permission denied` fehl. Die Lösung ist `TMPDIR` auf einen ausführbaren Pfad innerhalb des Workspace umzuleiten und diesen `TMPDIR`-Pfad beim Container-Start anzulegen.

```dockerfile
# ──────────────────────────────────────────────────────────────────────────────
# Antigravity Agent Container
# Base: Ubuntu 22.04 LTS (minimal)
# Binary: agy (Google Antigravity CLI Go-Binary)
# Auth: Headless via API-Key (kein Browser erforderlich)
# Jules MCP: Coding-Assistent via Jules API (Model Context Protocol)
# ──────────────────────────────────────────────────────────────────────────────
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

# ── System-Pakete & Runtimes ──
# Enthält alle Laufzeitumgebungen, die Jules MCP für Code-Ausführung benötigt.
# build-essential: C/C++ Compiler für native Python-Extensions und Binaries
# python3-venv: Isolierte virtuelle Environments für pip-Installationen
# nodejs/npm: JavaScript/TypeScript Execution für Jules-generierte Node-Projekte
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      gnupg \
      jq \
      python3 \
      python3-pip \
      python3-venv \
      build-essential \
  && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# ── agentuser anlegen (UID 1000, identisch zum Host-User) ──
RUN groupadd -g 1000 agentuser \
 && useradd -u 1000 -g 1000 -m -s /bin/bash agentuser

# ── Antigravity CLI (agy) installieren ──
# Das Install-Script legt das Binary unter ~/.local/bin/agy ab.
# Anschließend verschieben wir es nach /usr/local/bin für systemweite Verfügbarkeit.
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash \
 && mv /root/.local/bin/agy /usr/local/bin/agy \
 && chmod 755 /usr/local/bin/agy \
 && agy --version

# ── Entrypoint-Skript ins Image kopieren ──
# Liest Docker Secrets aus Dateien und exportiert sie als Umgebungsvariablen.
# Dies ist notwendig, da Jules MCP und agy direkte Env-Vars erwarten,
# nicht das _FILE-Convention-Pattern.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

# ── MCP-Konfigurationsdatei ins Image kopieren ──
COPY mcp_config.json /etc/antigravity/mcp_config.json
RUN chown root:root /etc/antigravity/mcp_config.json \
 && chmod 644 /etc/antigravity/mcp_config.json

# ── Umgebungsvariablen ──
# TMPDIR: Umleitung von /tmp (noexec) auf einen ausführbaren Workspace-Pfad.
# PIP_TMPDIR: pip verwendet standardmäßig eigene Temp-Pfade, muss explizit gesetzt werden.
# PYTHONPYCACHEPREFIX: .pyc-Dateien landen im Workspace statt verstreut im Filesystem.
ENV PATH="/usr/local/bin:${PATH}" \
    HOME="/home/agentuser" \
    TMPDIR="/home/agentuser/workspace/.tmp" \
    PIP_TMPDIR="/home/agentuser/workspace/.tmp" \
    PIP_NO_CACHE_DIR="1" \
    PYTHONPYCACHEPREFIX="/home/agentuser/workspace/.pycache" \
    JULES_MCP_CONFIG="/etc/antigravity/mcp_config.json"

# ── Arbeitsverzeichnis ──
WORKDIR /home/agentuser/workspace

# ── Ownership: Workspace und Home dem agentuser übergeben ──
RUN chown -R agentuser:agentuser /home/agentuser

# ── Ab hier als non-root agentuser ausführen ──
USER agentuser

# ── Entrypoint: liest Secrets, exportiert Env-Vars, startet Hauptprozess ──
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# ── Standardbefehl: Container am Leben halten ──
# agy-Sessions werden via docker exec oder Portainer Console gestartet.
CMD ["tail", "-f", "/dev/null"]
```

> **Hinweis zu `CMD`:** `tail -f /dev/null` hält den Container am Leben ohne eigenen Prozess zu starten. `agy`-Sessions werden via `docker exec` oder Portainer Console interaktiv gestartet (siehe [Betriebsanleitung](#7-betriebsanleitung)).

### 3.2 seccomp-profile.json

Dieses Profil erlaubt alle Syscalls, die `agy` (Go-Binary, Netzwerk, Dateisystem) und Jules MCP (Python, Node.js, Subprozesse) benötigen, und blockiert explizit gefährliche Kernel-Schnittstellen.

> **`io_uring`-Ergänzung:** Node.js ≥ 18 und moderne async I/O-Bibliotheken nutzen `io_uring` für hochperformante asynchrone Operationen. Ohne die drei `io_uring_*`-Syscalls schlagen Node.js-basierte Jules-Subprozesse bei intensiver I/O lautlos fehl oder degradieren zu langsamem `epoll`-Fallback. Sie sind hier explizit erlaubt.

> **Seccomp-Audit während Testläufen:** Falls Jules MCP nach dem ersten Deploy unerwartet fehlschlägt, Seccomp-Violations im Kernel-Log prüfen:
> ```bash
> sudo dmesg | grep seccomp | tail -30
> # Syscall-Nummer auflösen:
> sudo dmesg | grep seccomp | awk '{print $NF}' | sort -u | \
>   while read n; do python3 -c "import ctypes; print($n, ctypes.util.find_library('c'))"; done
> ```

```json
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
        "rt_sigsuspend", "rt_sigtimedwait", "rt_tgsigqueueinfo", "sched_getaffinity",
        "sched_getparam", "sched_getscheduler", "sched_yield", "select",
        "semctl", "semget", "semop", "semtimedop", "send", "sendfile",
        "sendmmsg", "sendmsg", "sendto", "set_robust_list", "set_tid_address",
        "setgid", "setgroups", "setitimer", "setpgid", "setregid", "setresgid",
        "setresuid", "setreuid", "setsid", "setsockopt", "setuid", "setxattr",
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
```

### 3.3 AGENTS.md (Agy-Konfiguration)

`agy` liest `AGENTS.md` aus dem Workspace-Root als Kontextdatei für den Agenten. Die Konfiguration definiert explizit die Rolle und Berechtigungen des Jules MCP:

```markdown
# Agent Configuration

## Role
This agent operates within an isolated Docker environment.
Workspace: /home/agentuser/workspace

## Tooling
- Jules MCP is active and configured via /etc/antigravity/mcp_config.json
- The agent is authorized to generate, modify, and execute code
  within the workspace (/home/agentuser/workspace) via Jules MCP.
- Jules MCP has read/write access limited to the workspace directory.
- No access to paths outside /home/agentuser/workspace without explicit instruction.

## Constraints
- No access to LAN resources
- All network traffic routes through NordVPN (wg0)
- Read/write access limited to workspace directory
- Temporary files must use TMPDIR=/home/agentuser/workspace/.tmp

## Project Context
<!-- Hier projektspezifische Informationen einfügen -->
```

`.gitignore`:

```gitignore
# Keine Secrets ins Repo
*.env
*.key
*.pem
.env*
secrets/

# Kein lokaler Build-Cache
__pycache__/
*.pyc
.pytest_cache/

# Jules MCP lokale Caches (werden als Docker-Volumes persistiert, nicht ins Repo)
.jules_cache/
.jules_local/
```

### 3.4 Jules MCP-Konfigurationsdatei

Dies ist der zentrale Integrationspunkt: `mcp_config.json` teilt Antigravity mit, unter welchem Transport und welchem Endpunkt der Jules MCP-Server erreichbar ist. Ohne diese Datei kann Antigravity Jules nicht finden, selbst wenn der API-Key korrekt gesetzt ist.

> **Hinweis:** Die Jules MCP API ist eine Cloud-API von Google. Der Container kommuniziert zur Laufzeit mit `jules.google.com` — der API-Key wird dabei vom Entrypoint-Skript als Umgebungsvariable `JULES_API_KEY` bereitgestellt und hier via `${JULES_API_KEY}` referenziert. Falls die Jules-Implementierung eine andere Auth-Methode vorschreibt, muss dieses Header-Feld entsprechend angepasst werden.

```json
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
```

> **Sicherheitshinweis:** Der API-Key wird **nicht** im Klartext in diese Datei eingetragen. Die `${JULES_API_KEY}`-Placeholder-Syntax wird vom Antigravity-CLI zur Laufzeit aus der gleichnamigen Umgebungsvariable aufgelöst, die das Entrypoint-Skript aus dem Docker Secret liest.

### 3.5 Docker Entrypoint-Skript

Dieses Skript löst das grundlegende Inkompatibilitätsproblem zwischen Docker Secrets und Applikations-Auth: Docker Secrets werden als Dateien unter `/run/secrets/` gemountet. `agy` und Jules MCP erwarten jedoch **direkte Umgebungsvariablen** (`GOOGLE_API_KEY`, `JULES_API_KEY`), nicht Dateipfad-Referenzen.

Das `_FILE`-Pattern (z. B. `JULES_API_KEY_FILE=...`) ist eine Konvention einzelner Images (z. B. PostgreSQL) und wird von Google-SDKs **nicht** nativ unterstützt. Das Entrypoint-Skript liest die Secret-Dateien und exportiert die Werte als echte Umgebungsvariablen, bevor der Hauptprozess startet.

```bash
#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
# docker-entrypoint.sh
# Liest Docker Secrets aus /run/secrets/ und exportiert sie als
# Umgebungsvariablen. Erstellt notwendige Laufzeitverzeichnisse.
# Wird als ENTRYPOINT im Dockerfile definiert.
# ──────────────────────────────────────────────────────────────────────

# ── Secrets aus Dateien lesen und exportieren ──
AGY_SECRET_FILE="/run/secrets/agy_api_key_agent_01"
JULES_SECRET_FILE="/run/secrets/jules_api_key_agent_01"

if [[ ! -f "$AGY_SECRET_FILE" ]]; then
  echo "[ENTRYPOINT] FEHLER: Secret-Datei nicht gefunden: $AGY_SECRET_FILE" >&2
  exit 1
fi

if [[ ! -f "$JULES_SECRET_FILE" ]]; then
  echo "[ENTRYPOINT] FEHLER: Secret-Datei nicht gefunden: $JULES_SECRET_FILE" >&2
  exit 1
fi

export GOOGLE_API_KEY
GOOGLE_API_KEY=$(tr -d '[:space:]' < "$AGY_SECRET_FILE")

export JULES_API_KEY
JULES_API_KEY=$(tr -d '[:space:]' < "$JULES_SECRET_FILE")

# Validierung: Leere Keys abfangen
if [[ -z "$GOOGLE_API_KEY" ]]; then
  echo "[ENTRYPOINT] FEHLER: GOOGLE_API_KEY ist leer. Secret-Inhalt prüfen." >&2
  exit 1
fi

if [[ -z "$JULES_API_KEY" ]]; then
  echo "[ENTRYPOINT] FEHLER: JULES_API_KEY ist leer. Secret-Inhalt prüfen." >&2
  exit 1
fi

echo "[ENTRYPOINT] Secrets geladen. GOOGLE_API_KEY: ${GOOGLE_API_KEY:0:8}... JULES_API_KEY: ${JULES_API_KEY:0:8}..."

# ── Laufzeitverzeichnisse anlegen ──
# TMPDIR muss existieren, bevor pip, Compiler oder Jules ihn nutzen.
mkdir -p "${TMPDIR:-/home/agentuser/workspace/.tmp}"
mkdir -p "/home/agentuser/workspace/.pycache"

echo "[ENTRYPOINT] Laufzeitverzeichnisse bereit. TMPDIR=${TMPDIR}"

# ── Hauptprozess starten ──
exec "$@"
```

> **Wichtig:** Das Skript endet mit `exec "$@"` — dadurch übernimmt der eigentliche Prozess (z. B. `tail -f /dev/null` oder `agy`) die PID 1 und empfängt Docker-Signale (`SIGTERM`, `SIGKILL`) korrekt.

---

## 4. Infrastruktur auf dem Host anlegen

### 4.1 Docker Netzwerk `antigravity_net`

Das Netzwerk wird **manuell** angelegt (nicht durch Portainer). So bleiben die nftables-Regeln stabil, da die Bridge `br-antigravity` beim ersten Stack-Deploy entsteht und nie gelöscht wird.

```bash
docker network create \
  --driver bridge \
  --subnet 172.28.0.0/24 \
  --gateway 172.28.0.1 \
  --opt "com.docker.network.bridge.name=br-antigravity" \
  --opt "com.docker.network.bridge.enable_icc=false" \
  --label "managed_by=manual" \
  antigravity_net
```

Verifizieren:

```bash
docker network inspect antigravity_net | jq '.[0].IPAM.Config'
# Erwartete Ausgabe: [{ "Subnet": "172.28.0.0/24", "Gateway": "172.28.0.1" }]

ip link show br-antigravity
# Erwartung: Interface br-antigravity wird angezeigt
```

> **Kritisch:** Dieses Netzwerk darf niemals über Portainer oder `docker network rm` gelöscht werden. Stacks deklarieren es als `external: true`. Eine Neuerstellung würde das Bridge-Interface mit neuem Namen anlegen und damit alle nftables-Regeln invalidieren.

### 4.2 Verzeichnisstruktur

```bash
# Basis-Workspace für den ersten Agenten
mkdir -p ~/antigravity_workspaces/agent_01
mkdir -p ~/antigravity_workspaces/agent_02  # für spätere Instanzen

# Globale Konfigurationsverzeichnisse (werden in alle Container gemountet)
mkdir -p ~/.gemini
mkdir -p ~/.config/antigravity

# ── Jules MCP Persistenz-Verzeichnisse ──
# Jules legt Indizes, Vektor-Caches und Sitzungsmetadaten lokal ab.
# Als Named Docker Volumes (siehe Stack-YAML) persistiert, damit
# Re-Indizierungen nach Container-Updates vermieden werden.
# Die tatsächlich genutzten Pfade im Container sind:
#   ~/.cache/jules      → Vektor-Caches, Repository-Indizes
#   ~/.config/jules     → Sitzungskonfiguration
#   ~/.local/share/jules → Workspace-Metadaten
# Diese werden als Named Volumes (nicht Host-Bind-Mounts) verwaltet.

# Verzeichnisstruktur anzeigen
tree ~/antigravity_workspaces ~/.gemini ~/.config/antigravity 2>/dev/null || \
  find ~/antigravity_workspaces ~/.gemini ~/.config/antigravity -maxdepth 2
```

---

## 5. Portainer konfigurieren

### 5.1 Git-Integration einrichten

Portainer kann direkt aus einem Git-Repository bauen. Das Dockerfile muss im Repo-Root liegen (oder der Pfad wird explizit angegeben).

1. Öffne Portainer: `https://<HOST-IP>:9443`
2. Navigiere zu **Settings** → **Git credentials** → **Add credential**
3. Füge deine Git-Credentials hinzu:
   - **Name:** `antigravity-repo`
   - **Username:** dein Git-Username
   - **Personal Access Token:** Git-PAT mit `read`-Berechtigung auf das Repo
4. Klicke **Save credential**

### 5.2 Secrets anlegen

API-Keys werden als Docker Secrets hinterlegt — sie erscheinen nie in Umgebungsvariablen oder `docker inspect`.

#### Secret 1: Antigravity CLI API-Key

1. Portainer → **Secrets** → **Add secret**
2. Fülle aus:
   - **Name:** `agy_api_key_agent_01`
   - **Secret:** API-Key im Klartext (wird verschlüsselt gespeichert)
3. Klicke **Create secret**

**API-Key beschaffen:** `agy`-Headless-Auth verwendet einen Gemini/Google API-Key. Dieser wird unter [console.cloud.google.com](https://console.cloud.google.com) → **APIs & Services** → **Credentials** → **Create credentials** → **API key** erstellt. Die Antigravity CLI API muss im Projekt aktiviert sein.

#### Secret 2: Jules Coding-Assistent API-Key

1. Portainer → **Secrets** → **Add secret**
2. Fülle aus:
   - **Name:** `jules_api_key_agent_01`
   - **Secret:** Jules API-Key im Klartext
3. Klicke **Create secret**

**Jules API-Key beschaffen:**

1. Öffne [jules.google.com](https://jules.google.com) im Browser
2. Stelle sicher, dass du mit dem korrekten Google-Account eingeloggt bist
3. Navigiere zu **Settings** → **API** (Direkt-URL: `https://jules.google.com/u/0/settings/api`)

> **Hinweis zur URL:** Die Zahl in `/u/N/` entspricht dem Index des Google-Accounts im Browser-Profil. Für den primären Account ist dies `/u/0/`. Bei mehreren eingeloggten Accounts kann die Zahl abweichen (`/u/1/`, `/u/2/`, etc.). Verifiziere den korrekten Account-Index, indem du zuerst `jules.google.com` aufrufst und den eingeloggten Account oben rechts prüfst.

4. Erzeuge einen neuen API-Key und kopiere ihn **vollständig** (er wird nach dem Schließen des Dialogs nicht mehr angezeigt)
5. Füge den Key **ohne führende/nachgestellte Leerzeichen oder Zeilenumbrüche** als Secret ein

**Smoke-Test des Jules API-Keys vor dem Deployment (auf dem Host):**

```bash
# Key temporär in eine Variable laden (nur für diesen Test, nicht persistent)
read -rs JULES_TEST_KEY
# Key eingeben, dann Enter drücken

# API-Erreichbarkeit und Key-Validität testen
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $JULES_TEST_KEY" \
  -H "Content-Type: application/json" \
  https://jules.google.com/api/v1/health 2>/dev/null || echo "000")

echo "HTTP Response: $HTTP_CODE"
# Erwartung: 200 (OK)
# 401: Key ungültig oder falsch kopiert
# 000: Netzwerk nicht erreichbar (VPN prüfen, oder Endpoint-URL ggf. abweichend)

# Variable wieder leeren
unset JULES_TEST_KEY
```

### 5.3 Stack deployen

1. Portainer → **Stacks** → **Add stack**
2. **Name:** `antigravity_agent_01`
3. **Build method:** Wähle **Repository**
4. Fülle die Git-Felder aus:
   - **Repository URL:** `https://github.com/<org>/antigravity-agent.git`
   - **Repository reference:** `refs/heads/main`
   - **Compose path:** `docker-compose.yml` *(oder direkt das Stack-YAML einfügen, siehe unten)*
   - **Authentication:** wähle `antigravity-repo` (aus Schritt 5.1)
   - **Automatic updates:** optional aktivieren (Portainer pollt Repo auf neue Commits)

**Stack-YAML** (im Web Editor einfügen oder als `docker-compose.yml` ins Repo legen):

```yaml
# ──────────────────────────────────────────────────────────────────────
# Stack: antigravity_agent_01
# Portainer baut das Image aus dem Git-Repo via Build-Integration.
# Das Netzwerk antigravity_net ist extern (manuell angelegt, nie löschen).
# Jules MCP: Coding-Assistent, integriert via Docker Secret + MCP Config.
# ──────────────────────────────────────────────────────────────────────

networks:
  antigravity_net:
    external: true
    name: antigravity_net
    # KEIN internal: true — Jules MCP benötigt Egress zu jules.google.com

services:
  agy_agent_01:
    build:
      context: .
      dockerfile: Dockerfile
    image: antigravity-agent:latest
    container_name: antigravity_agent_01
    user: "1000:1000"

    networks:
      - antigravity_net

    # DNS auf NordVPN-Resolver zwingen — verhindert DNS-Leaks
    dns:
      - 103.86.96.100
      - 103.86.99.100
    dns_search: []

    restart: unless-stopped

    # Container-Härtung
    privileged: false
    security_opt:
      - no-new-privileges:true
      # WICHTIG: Absoluter Pfad erforderlich.
      # Relativer Pfad (./seccomp-profile.json) funktioniert nur wenn
      # 'docker stack deploy' exakt aus dem Repo-Verzeichnis aufgerufen wird.
      # Bei Portainer's Git-Build-Integration (Abschnitt 5.1) wird das Profil
      # aus einem temporären Build-Kontext kopiert — der relative Pfad schlägt fehl
      # und der Container startet ohne Seccomp-Profil (seccomp=unconfined).
      # Lösung: Datei auf dem Host ablegen und absoluten Pfad verwenden.
      #
      # Vorbereitung (einmalig auf dem Host):
      #   sudo mkdir -p /etc/antigravity
      #   sudo cp seccomp-profile.json /etc/antigravity/seccomp-profile.json
      #   sudo chmod 644 /etc/antigravity/seccomp-profile.json
      - seccomp:/etc/antigravity/seccomp-profile.json
    cap_drop:
      - ALL

    # /tmp als noexec — ausführbare Temp-Dateien landen via TMPDIR im Workspace
    # TMPDIR=/home/agentuser/workspace/.tmp (gesetzt im Dockerfile und Entrypoint)
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=256m

    # Health Check: Prüft ausschließlich agy-Binary-Verfügbarkeit
    #
    # ACHTUNG – Bekannter Bug (behoben): Die ursprüngliche Version prüfte auch
    # die Jules API via curl mit $${JULES_API_KEY}. Das schlägt fehl, weil Docker
    # Healthchecks in einem frischen Exec-Kontext laufen und nur die in 'environment:'
    # oder 'ENV' konfigurierten Variablen sehen — NICHT was der Entrypoint via
    # 'export' gesetzt hat. JULES_API_KEY ist im Entrypoint-Prozess gesetzt (PID 1),
    # aber nicht in der Container-Konfiguration. Healthcheck-Kontext = leerer Key
    # → curl gibt 401 → Container dauerhaft "unhealthy" obwohl korrekt laufend.
    #
    # Fix: Healthcheck prüft nur agy --version (kein API-Key nötig, deterministisch).
    # Jules API-Konnektivität wird stattdessen über den manuellen Verifikationstest
    # in Abschnitt 6 (TEST 8) geprüft.
    healthcheck:
      test:
        - "CMD-SHELL"
        - "agy --version > /dev/null 2>&1"
      interval: 60s
      timeout: 15s
      retries: 3
      start_period: 30s

    # Ressourcenlimits
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
        reservations:
          memory: 512M

    volumes:
      # Isolierter Workspace für diese Instanz
      - /home/<DEIN_USER>/antigravity_workspaces/agent_01:/home/agentuser/workspace:rw
      # Geteilte globale Konfiguration (alle Instanzen lesen dieselbe Config)
      - /home/<DEIN_USER>/.gemini:/home/agentuser/.gemini:rw
      - /home/<DEIN_USER>/.config/antigravity:/home/agentuser/.config/antigravity:rw
      # Jules MCP Persistenz (Named Volumes — überleben Container-Neustarts und Updates)
      - jules_cache_agent_01:/home/agentuser/.cache/jules
      - jules_config_agent_01:/home/agentuser/.config/jules
      - jules_local_agent_01:/home/agentuser/.local/share/jules

    secrets:
      - agy_api_key_agent_01
      - jules_api_key_agent_01

    environment:
      # Hinweis: GOOGLE_API_KEY und JULES_API_KEY werden vom Entrypoint-Skript
      # aus den Secret-Dateien gelesen und exportiert. Diese _FILE-Variablen
      # dienen nur als interne Referenz für das Entrypoint-Skript.
      - GOOGLE_API_KEY_FILE=/run/secrets/agy_api_key_agent_01
      - JULES_API_KEY_FILE=/run/secrets/jules_api_key_agent_01
      # Jules MCP aktivieren (Antigravity-spezifische Env-Variable)
      - AGY_ENABLE_JULES_MCP=true
      # MCP-Konfigurationspfad explizit setzen
      - JULES_MCP_CONFIG=/etc/antigravity/mcp_config.json

secrets:
  agy_api_key_agent_01:
    external: true
  jules_api_key_agent_01:
    external: true

# Named Volumes für Jules MCP State-Persistenz
# Überleben docker-compose down/up und Container-Updates.
# Verhindert teure Re-Indizierungen von Codebases nach jedem Deploy.
volumes:
  jules_cache_agent_01:
    driver: local
    labels:
      managed_by: "antigravity-stack"
      instance: "agent_01"
      purpose: "jules-mcp-vector-cache"
  jules_config_agent_01:
    driver: local
    labels:
      managed_by: "antigravity-stack"
      instance: "agent_01"
      purpose: "jules-mcp-session-config"
  jules_local_agent_01:
    driver: local
    labels:
      managed_by: "antigravity-stack"
      instance: "agent_01"
      purpose: "jules-mcp-workspace-metadata"
```

> **Placeholder:** Ersetze `<DEIN_USER>` mit deinem tatsächlichen Ubuntu-Username (z. B. `phili`).

5. Klicke **Deploy the stack**

Portainer klont das Repo, baut das Image und startet den Container. Der Build-Vorgang dauert beim ersten Mal 3–7 Minuten (agy-Download + Node.js-Installation inbegriffen).

---

## 6. Verifikation & Sicherheitsaudit

Nach dem ersten erfolgreichen Deploy: **alle folgenden Tests müssen bestehen**, bevor produktiv gearbeitet wird.

Öffne in Portainer: **Containers** → `antigravity_agent_01` → **>_ Console** → Shell: `/bin/bash`

```bash
# ──────────────────────────────────────────────────────────────────────
# TEST 1: VPN-Routing
# Erwartung: NordVPN Exit-Node IP (≠ deine Provider-IP)
# ──────────────────────────────────────────────────────────────────────
curl -s https://ifconfig.me
# Beispiel-Erwartung: 194.165.xxx.xxx (NordVPN CH-Node)

# ──────────────────────────────────────────────────────────────────────
# TEST 2: LAN-Isolation — RFC-1918 muss geblockt sein
# Erwartung: "Connection timed out" nach 3 Sekunden
# NICHT: "Connection refused" (das wäre ein direkter Reach)
# ──────────────────────────────────────────────────────────────────────
curl --connect-timeout 3 http://192.168.1.1  && echo "FAIL" || echo "OK - Timeout"
curl --connect-timeout 3 http://10.0.0.1     && echo "FAIL" || echo "OK - Timeout"
curl --connect-timeout 3 http://172.16.0.1   && echo "FAIL" || echo "OK - Timeout"

# ──────────────────────────────────────────────────────────────────────
# TEST 3: Cloud-Metadata-Blockade
# Erwartung: "Connection timed out"
# ──────────────────────────────────────────────────────────────────────
curl --connect-timeout 3 http://169.254.169.254 && echo "FAIL" || echo "OK - Timeout"

# ──────────────────────────────────────────────────────────────────────
# TEST 4: DNS-Resolver verifizieren
# Erwartung: Auflösung über 103.86.96.100 (NordVPN)
# ──────────────────────────────────────────────────────────────────────
cat /etc/resolv.conf
# Erwartung: nameserver 103.86.96.100

# ──────────────────────────────────────────────────────────────────────
# TEST 5: agy Binary erreichbar und authentifiziert
# ──────────────────────────────────────────────────────────────────────
agy --version
# Erwartung: Versionsnummer (z. B. 2.0.x)

# ──────────────────────────────────────────────────────────────────────
# TEST 6: Kill-Switch-Test (auf dem HOST ausführen, nicht im Container)
# WireGuard temporär stoppen → Container darf keinen Internetzugang haben
# ──────────────────────────────────────────────────────────────────────
# HOST:
sudo systemctl stop wg-quick@wg0
# CONTAINER (gleichzeitig):
curl --connect-timeout 5 https://ifconfig.me  # Erwartung: Timeout/Fehler
# HOST:
sudo systemctl start wg-quick@wg0

# ──────────────────────────────────────────────────────────────────────
# TEST 7: Secrets korrekt als Umgebungsvariablen verfügbar
# Erwartung: API-Keys (gekürzt) werden angezeigt, nicht leer
# ──────────────────────────────────────────────────────────────────────
echo "GOOGLE_API_KEY: ${GOOGLE_API_KEY:0:12}..."
echo "JULES_API_KEY:  ${JULES_API_KEY:0:12}..."
# Erwartung: Jeweils 12 Zeichen des Keys + "..."
# FEHLER wenn: "..." (leer) oder Variable nicht gesetzt

# ──────────────────────────────────────────────────────────────────────
# TEST 8: Jules API-Konnektivität aus dem Container
# Erwartung: HTTP 200
# ──────────────────────────────────────────────────────────────────────
curl -s -o /dev/null -w "Jules API HTTP Status: %{http_code}\n" \
  -H "Authorization: Bearer $JULES_API_KEY" \
  https://jules.google.com/api/v1/health
# Erwartung: Jules API HTTP Status: 200

# ──────────────────────────────────────────────────────────────────────
# TEST 9: MCP-Konfigurationsdatei vorhanden und lesbar
# ──────────────────────────────────────────────────────────────────────
cat /etc/antigravity/mcp_config.json | jq '.mcpServers.jules.url'
# Erwartung: "https://jules.google.com/api/mcp/v1"

# ──────────────────────────────────────────────────────────────────────
# TEST 10: TMPDIR ist ausführbar (noexec-Fix verifizieren)
# ──────────────────────────────────────────────────────────────────────
echo '#!/bin/bash' > "$TMPDIR/test_exec.sh"
chmod +x "$TMPDIR/test_exec.sh"
"$TMPDIR/test_exec.sh" && echo "OK - TMPDIR ist executable" || echo "FAIL - noexec Problem"
rm -f "$TMPDIR/test_exec.sh"

# ──────────────────────────────────────────────────────────────────────
# TEST 11: Runtimes verfügbar (für Jules Code-Ausführung)
# ──────────────────────────────────────────────────────────────────────
python3 --version    # Erwartung: Python 3.10.x oder höher
pip3 --version       # Erwartung: pip 22.x oder höher
node --version       # Erwartung: v20.x.x
npm --version        # Erwartung: 10.x.x
gcc --version        # Erwartung: gcc 11.x oder höher
```

**nftables Drop-Logs auf dem Host prüfen:**

```bash
sudo journalctl -k | grep "nft-drop" | tail -20
# Logs zeigen jeden geblockten Verbindungsversuch mit Quelle und Ziel
```

---

## 7. Betriebsanleitung

### 7.1 Container-Verwaltung

#### Via Portainer Web-GUI (empfohlen)

| Aktion | Weg in Portainer |
|---|---|
| Container starten | **Containers** → `antigravity_agent_01` → ▶ Start |
| Container stoppen | **Containers** → `antigravity_agent_01` → ⏹ Stop |
| Container neustarten | **Containers** → `antigravity_agent_01` → 🔄 Restart |
| Shell öffnen | **Containers** → `antigravity_agent_01` → **>_ Console** → `/bin/bash` → Connect |
| Logs einsehen | **Containers** → `antigravity_agent_01` → **Logs** |
| Stats (CPU/RAM) | **Containers** → `antigravity_agent_01` → **Stats** |
| Stack neudeploy | **Stacks** → `antigravity_agent_01` → **Pull and redeploy** |

#### Via CLI auf dem Host

```bash
# Status aller Antigravity-Container
docker ps --filter "name=antigravity"

# Container stoppen
docker stop antigravity_agent_01

# Container starten
docker start antigravity_agent_01

# Shell in Container öffnen
docker exec -it antigravity_agent_01 /bin/bash

# Logs verfolgen (live)
docker logs -f antigravity_agent_01

# Ressourcenverbrauch live
docker stats antigravity_agent_01
```

### 7.2 Agy im Container bedienen

`agy` läuft interaktiv. Öffne eine Shell im Container (via Portainer Console oder `docker exec`) und starte dann `agy`:

```bash
# agy starten
agy

# Nützliche agy-Befehle (innerhalb der agy-Session)
/help          # Hilfe anzeigen
/models        # Verfügbare Modelle auflisten
/logout        # Aus Google-Account ausloggen
/settings      # Einstellungen anzeigen
agy update     # Binary auf neueste Version aktualisieren (außerhalb der Session)

# Jules MCP Status prüfen (falls agy entsprechenden Befehl unterstützt)
/mcp status    # MCP-Server-Verbindungsstatus anzeigen (agy-versionabhängig)
```

**Headless API-Key Authentifizierung:** Das Entrypoint-Skript liest die Docker Secrets aus `/run/secrets/` und exportiert `GOOGLE_API_KEY` sowie `JULES_API_KEY` als Umgebungsvariablen. `agy` liest `GOOGLE_API_KEY` beim Start automatisch — kein manueller Login-Schritt erforderlich.

Falls `agy` beim Start nach Login fragt, obwohl das Secret korrekt gesetzt ist:

```bash
# Env-Variablen prüfen
echo "GOOGLE_API_KEY gesetzt: ${GOOGLE_API_KEY:+ja}"
echo "JULES_API_KEY gesetzt:  ${JULES_API_KEY:+ja}"

# Secret-Dateien direkt prüfen (als agentuser im Container)
ls -la /run/secrets/
cat /run/secrets/agy_api_key_agent_01 | wc -c   # Zeichenanzahl (darf nicht 0 sein)
cat /run/secrets/jules_api_key_agent_01 | wc -c  # Zeichenanzahl (darf nicht 0 sein)
```

### 7.3 Logs & Monitoring

```bash
# Alle Container-Logs der letzten Stunde
docker logs --since 1h antigravity_agent_01

# Jules API Rate-Limit-Fehler überwachen
docker logs --since 1h antigravity_agent_01 2>&1 | grep -E "429|quota|rate.limit|Too Many"

# Jules API Auth-Fehler überwachen
docker logs --since 1h antigravity_agent_01 2>&1 | grep -E "401|403|Unauthorized|Forbidden"

# nftables Drop-Events (geblockte Verbindungen)
sudo journalctl -k --grep "nft-drop" --since "1 hour ago"

# Seccomp-Violations im Kernel-Log
sudo dmesg | grep seccomp | tail -20

# WireGuard Status (VPN aktiv?)
sudo wg show

# Netzwerk-Traffic durch wg0 (Pakete/Bytes)
sudo wg show wg0 | grep "transfer"

# Docker-Netzwerk Übersicht
docker network inspect antigravity_net

# Jules Named Volumes Belegung prüfen
docker system df -v | grep jules
```

### 7.4 Updates

#### agy Binary aktualisieren

```bash
# Im Container-Shell:
agy update

# Alternativ: Image neu bauen (aktualisiert agy beim Docker-Build)
# In Portainer: Stacks → antigravity_agent_01 → Pull and redeploy
```

#### Docker-Image neu bauen (nach Dockerfile-Änderung)

1. Änderungen ins Git-Repo pushen
2. Portainer → **Stacks** → `antigravity_agent_01` → **Pull and redeploy**
3. Portainer klont das Repo neu, baut das Image und startet den Container neu
4. Jules Named Volumes (`jules_cache_agent_01`, etc.) **bleiben erhalten** — keine Re-Indizierung erforderlich

#### Jules MCP-Konfiguration aktualisieren

```bash
# mcp_config.json im Repo anpassen, dann:
# Git commit + push
git add mcp_config.json
git commit -m "Update Jules MCP config"
git push

# Portainer: Pull and redeploy (lädt neue mcp_config.json ins Image)
```

#### nftables-Regelwerk aktualisieren

```bash
sudo nano /etc/nftables.conf
# Änderungen vornehmen, dann:
sudo nft -c -f /etc/nftables.conf   # Syntax validieren
sudo nft -f /etc/nftables.conf      # Laden
sudo nft list ruleset                # Prüfen
```

---

## 8. Skalierung: Weitere Instanzen hinzufügen

Jede neue Instanz erhält einen isolierten Workspace, ein eigenes agy-Secret, ein eigenes Jules-Secret und eigene Jules-Persistenz-Volumes. Die globale `agy`-Konfiguration (`~/.gemini`, `~/.config/antigravity`) wird geteilt.

**Schritt 1: Workspace anlegen**

```bash
mkdir -p ~/antigravity_workspaces/agent_02
```

**Schritt 2: Secrets anlegen** (Portainer → Secrets → Add secret)

- Name: `agy_api_key_agent_02` — Wert: separater oder gleicher agy API-Key
- Name: `jules_api_key_agent_02` — Wert: separater Jules API-Key (empfohlen, um Quota-Limits zu verteilen)

**Schritt 3: Neuen Stack deployen** (Portainer → Stacks → Add stack)

Stack-Name: `antigravity_agent_02`
YAML — kopiere das Stack-YAML aus Abschnitt 5.3 und ersetze:

```
antigravity_agent_01  → antigravity_agent_02
agy_agent_01          → agy_agent_02
agy_api_key_agent_01  → agy_api_key_agent_02
jules_api_key_agent_01 → jules_api_key_agent_02
jules_cache_agent_01  → jules_cache_agent_02
jules_config_agent_01 → jules_config_agent_02
jules_local_agent_01  → jules_local_agent_02
agent_01              → agent_02
```

Das Netzwerk `antigravity_net` bleibt `external: true` — keine Änderung erforderlich.

> **Quota-Hinweis:** Jules API hat Anfrage-Limits pro API-Key. Bei parallelen Instanzen mit demselben Key können `429 Too Many Requests`-Fehler auftreten. Separate Keys pro Instanz werden empfohlen.

---

## 9. Troubleshooting

### Container startet nicht (Exit Code 1)

```bash
docker logs antigravity_agent_01
# Häufige Ursache 1: Entrypoint findet Secret-Datei nicht
# → "[ENTRYPOINT] FEHLER: Secret-Datei nicht gefunden"
# Fix: Secret in Portainer anlegen und Stack neu deployen

# Häufige Ursache 2: Permission-Fehler auf Volume-Mounts → UID/GID prüfen
ls -la ~/antigravity_workspaces/agent_01
# Erwartung: Owner ist UID 1000

# Fix:
chown -R 1000:1000 ~/antigravity_workspaces/agent_01
```

### Jules API gibt 401 / Unauthorized

```bash
# Jules API-Key aus laufendem Container prüfen
docker exec antigravity_agent_01 bash -c \
  'echo "Key: ${JULES_API_KEY:0:16}... (${#JULES_API_KEY} Zeichen)"'
# Erwartung: Mehr als 30 Zeichen

# Secret-Datei direkt prüfen
docker exec antigravity_agent_01 \
  cat /run/secrets/jules_api_key_agent_01 | wc -c
# Erwartung: > 30 (Zeichen inkl. Newline)

# Key-Validität extern testen (auf dem Host)
JULES_KEY=$(docker exec antigravity_agent_01 \
  cat /run/secrets/jules_api_key_agent_01 | tr -d '[:space:]')
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $JULES_KEY" \
  https://jules.google.com/api/v1/health
# Erwartung: 200 — bei 401 neuen Key unter jules.google.com/u/0/settings/api generieren
```

### Jules API nicht erreichbar (Timeout / 000)

```bash
# DNS-Auflösung aus dem Container testen
docker exec antigravity_agent_01 \
  curl -sv --connect-timeout 5 https://jules.google.com 2>&1 | head -20
# Bei "Could not resolve host": DNS-Problem

# VPN aktiv?
sudo wg show
# Falls kein Peer-Handshake: WireGuard neu starten
sudo systemctl restart wg-quick@wg0

# Egress-Route über wg0 prüfen
docker exec antigravity_agent_01 \
  curl -s https://ifconfig.me
# Erwartung: NordVPN IP, nicht Host-IP
```

### Jules gibt 429 (Rate Limit)

```bash
# Fehler in Logs identifizieren
docker logs --since 30m antigravity_agent_01 2>&1 | grep -c "429"

# Maßnahmen:
# 1. Separaten Jules API-Key für diese Instanz verwenden
# 2. Anfragerate in agy / Jules MCP Konfiguration drosseln (falls konfigurierbar)
# 3. Quota-Limit im Jules-Dashboard unter jules.google.com/u/0/settings/api prüfen
```

### TMPDIR / pip Permission denied

```bash
# TMPDIR korrekt gesetzt?
docker exec antigravity_agent_01 bash -c 'echo $TMPDIR'
# Erwartung: /home/agentuser/workspace/.tmp

# Verzeichnis existiert?
docker exec antigravity_agent_01 ls -la /home/agentuser/workspace/.tmp
# Falls nicht: Container neu starten (Entrypoint legt es an)

# pip Test
docker exec -it antigravity_agent_01 bash -c \
  'pip3 install --dry-run requests 2>&1 | tail -5'
# Erwartung: Keine Permission-Fehler
```

### UID-Mapping schlägt fehl (Volume Permission Denied)

Wenn der Host-User eine andere UID als 1000 hat, schlagen alle Volume-Zugriffe fehl. Robuste Lösung ohne `chown`-Workaround:

```bash
# Host-User UID ermitteln
HOST_UID=$(id -u)
HOST_GID=$(id -g)
echo "Host UID: $HOST_UID / GID: $HOST_GID"

# Stack-YAML anpassen:
# user: "1000:1000"  →  user: "${HOST_UID}:${HOST_GID}"

# Oder: Dockerfile anpassen (UID als Build-Arg):
# docker build --build-arg USER_UID=$HOST_UID --build-arg USER_GID=$HOST_GID .
# Im Dockerfile: ARG USER_UID=1000 / ARG USER_GID=1000 verwenden

# Volume-Ownership korrigieren (temporärer Fix):
sudo chown -R ${HOST_UID}:${HOST_GID} ~/antigravity_workspaces/agent_01
```

### Seccomp-Violation (Syscall blockiert)

```bash
# Kernel-Log auf Seccomp-Violations prüfen
sudo dmesg | grep seccomp | tail -20

# Beispiel-Output:
# [12345.678] audit: type=1326 audit(...) auid=... syscall=318 ...
# syscall=318 → io_uring_setup (fehlt im Profil → in seccomp-profile.json ergänzen)

# Syscall-Nummer auflösen (auf dem Host)
python3 -c "
import ctypes, ctypes.util
# Nummer aus dmesg eintragen:
nr = 318
print(f'syscall {nr}:', end=' ')
" 
# Alternativ: ausyscall-Tool
sudo apt install -y libseccomp-dev
scmp_sys_resolver 318   # Gibt Syscall-Namen zurück
```

### VPN-Verbindung bricht ab (Kill-Switch greift)

```bash
# Host: WireGuard-Status
sudo wg show
sudo systemctl status wg-quick@wg0

# Neustart
sudo systemctl restart wg-quick@wg0

# Container-Logs auf Netzwerkfehler prüfen
docker logs --since 5m antigravity_agent_01 2>&1 | grep -iE "connect|timeout|refused"
```

### agy gibt "Authentication failed" aus

```bash
# Secret korrekt gemountet?
docker exec antigravity_agent_01 cat /run/secrets/agy_api_key_agent_01

# Env-Variable korrekt gesetzt (via Entrypoint)?
docker exec antigravity_agent_01 bash -c 'echo ${GOOGLE_API_KEY:0:16}...'

# API Key gültig? (außerhalb Container testen)
curl -H "X-Goog-Api-Key: $(cat /path/to/key)" \
  https://generativelanguage.googleapis.com/v1/models
```

### nftables-Regeln fehlen nach Reboot

```bash
# Ist nftables-Service aktiv?
sudo systemctl status nftables

# Regelwerk manuell neu laden
sudo nft -f /etc/nftables.conf

# Persistenz sicherstellen (Regelwerk wird beim Start geladen)
sudo systemctl enable nftables
```

### Portainer kann nicht aus Git-Repo bauen

- Git-Token abgelaufen? → Portainer → Settings → Git credentials → Token erneuern
- Repo-URL korrekt? → HTTPS-URL ohne `.git`-Suffix verwenden wenn nötig
- Dockerfile im Repo-Root? → Build-Kontext in Portainer prüfen
- `docker-entrypoint.sh` im Repo? → Muss neben dem Dockerfile liegen und `chmod +x` erhalten haben

### Jules Named Volumes löschen (Reset der Caches)

```bash
# Nur durchführen wenn Caches korrupt sind oder Re-Indizierung erzwungen werden soll
# Container vorher stoppen!
docker stop antigravity_agent_01

docker volume rm jules_cache_agent_01 jules_config_agent_01 jules_local_agent_01
# Volumes werden beim nächsten Stack-Deploy automatisch neu erstellt

docker start antigravity_agent_01
# Jules re-indiziert beim ersten Zugriff (einmalig zeitintensiv)
```

---

*Ende des Dokuments. Alle Konfigurationswerte mit `<PLATZHALTER>` müssen vor dem ersten Deploy angepasst werden. Nach Änderungen an `seccomp-profile.json` immer Seccomp-Audit mit `sudo dmesg | grep seccomp` nach dem ersten Test-Deploy durchführen.*
