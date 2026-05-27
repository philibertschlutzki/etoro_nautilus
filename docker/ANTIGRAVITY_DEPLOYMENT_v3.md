# ANTIGRAVITY_DEPLOYMENT.md
# Vollständige Deployment- und Betriebsanleitung: Antigravity CLI auf Ubuntu mit Portainer

> **Stand:** Mai 2026 (Rev. 3 — Gluetun VPN-Migration)
> **Architektur:** Gluetun VPN-Container (NordVPN WireGuard) → nftables LAN-Isolation → Docker `antigravity_net` Bridge → Portainer-verwaltete Container
> **Binary:** `agy` (Go-Binary, Google Antigravity CLI 2.0)
> **Auth im Container:** API-Key via Docker Secret (headless, kein Browser)
> **Jules MCP:** Coding-Assistent via Jules API, integriert über Model Context Protocol (MCP)
> **Git-Integration:** Portainer baut Images direkt aus Git-Repo (kein lokales `docker build`)

> **Changelog Rev. 3 (Gluetun-Migration):**
> | ID | Abschnitt | Beschreibung |
> |----|-----------|-----------------|
> | FIX-1 | 2.3 | `METADATA`-IP-Tippfehler: `169.254.254.169` → `169.254.169.254` |
> | FIX-2 | 2.4.1 | Neuer Abschnitt: Docker Swarm init (Pflichtvoraussetzung für Docker Secrets) |
> | FIX-3 | 5.3 | Healthcheck: `$JULES_API_KEY`-Abhängigkeit entfernt |
> | FIX-4 | 2.1 | `resolvconf` → `openresolv` (aufgehoben durch Gluetun-Migration) |
> | FIX-5 | 5.3 | `seccomp:./` → absoluter Pfad |
> | **MIG-1** | **2.2** | **Host-seitiger WireGuard entfernt → Gluetun Docker-Container übernimmt VPN** |
> | **MIG-2** | **2.3** | **nftables vereinfacht: kein wg0-Kill-Switch mehr (Gluetun intern)** |
> | **MIG-3** | **3** | **Repo: `Dockerfile.gluetun` + `gluetun-entrypoint.sh` hinzugefügt** |
> | **MIG-4** | **5.3** | **Stack: `gluetun`-Service + `network_mode: service:gluetun` für agy** |

---

## Inhaltsverzeichnis

1. [Systemvoraussetzungen](#1-systemvoraussetzungen)
2. [Host-Vorbereitung](#2-host-vorbereitung)
   - 2.1 [Pakete installieren](#21-pakete-installieren)
   - 2.2 [Gluetun VPN-Container vorbereiten](#22-gluetun-vpn-container-vorbereiten)
   - 2.3 [nftables Regelwerk](#23-nftables-regelwerk)
   - 2.4 [Docker installieren](#24-docker-installieren)
   - 2.4.1 [Docker Swarm initialisieren](#241-docker-swarm-initialisieren-voraussetzung-für-docker-secrets)
   - 2.5 [Portainer installieren](#25-portainer-installieren)
3. [Git-Repository Struktur](#3-git-repository-struktur)
   - 3.1 [Dockerfile (agy)](#31-dockerfile-agy)
   - 3.2 [Dockerfile.gluetun](#32-dockerfilegluetun)
   - 3.3 [gluetun-entrypoint.sh](#33-gluetun-entrypointsh)
   - 3.4 [seccomp-profile.json](#34-seccomp-profilejson)
   - 3.5 [AGENTS.md (Agy-Konfiguration)](#35-agentsmd-agy-konfiguration)
   - 3.6 [Jules MCP-Konfigurationsdatei](#36-jules-mcp-konfigurationsdatei)
   - 3.7 [Docker Entrypoint-Skript (agy)](#37-docker-entrypoint-skript-agy)
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
| Kernel | ≥ 5.15 (nftables native) |
| RAM | ≥ 8 GB (4 GB pro agy-Container-Limit) |
| Docker | Engine ≥ 26.x |
| Portainer | CE ≥ 2.21 |
| NordVPN WireGuard Private Key | Von my.nordaccount.com (kein .conf-File nötig, nur der Private Key) |
| Git-Repo | Erreichbar vom Portainer-Host (HTTPS oder SSH) |
| API-Key (agy) | Google Antigravity CLI API-Key (aus console.cloud.google.com) |
| API-Key (Jules) | Jules Coding-Assistent API-Key (aus jules.google.com/u/0/settings/api) |
| Netzwerk | Ausgehende HTTPS/UDP-Verbindungen zu NordVPN-Endpoints, `*.googleapis.com` und `jules.google.com` |

> **VPN-Architektur:** Der VPN-Tunnel läuft nicht mehr auf dem Host, sondern als **Gluetun Docker-Container**. Gluetun baut intern eine WireGuard-Verbindung zu NordVPN auf und stellt einen Network-Stack bereit, den der agy-Container via `network_mode: "service:gluetun"` vollständig nutzt. Der Kill-Switch ist in Gluetun eingebaut (`FIREWALL=on` ist der Default) — fällt der VPN-Tunnel aus, blockiert Gluetun selbst allen ausgehenden Traffic.

**Wichtige UID-Anmerkung:** Der agy-Container läuft als `user: "1000:1000"`. Verifiziere mit `id` auf dem Host.

---

## 2. Host-Vorbereitung

### 2.1 Pakete installieren

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
  nftables \
  curl \
  ca-certificates \
  gnupg \
  lsb-release
```

nftables beim Systemstart aktivieren:

```bash
sudo systemctl enable nftables
sudo systemctl start nftables
```

> **Hinweis:** `wireguard`, `wireguard-tools` und `openresolv` werden auf dem Host **nicht mehr benötigt** — WireGuard läuft ausschließlich innerhalb des Gluetun-Containers. Die systemd-resolved-Konfiguration entfällt ebenfalls.

### 2.2 Gluetun VPN-Container vorbereiten

Der VPN-Tunnel wird vollständig vom Gluetun Docker-Container übernommen. Auf dem Host ist lediglich der NordVPN WireGuard **Private Key** erforderlich, der als Docker Secret hinterlegt wird.

**Private Key beschaffen:**

1. Navigiere auf [my.nordaccount.com](https://my.nordaccount.com) → **NordVPN** → **Manual setup** → **WireGuard**
2. Klicke **Generate key pair**
3. Kopiere den **Private Key** (44 Zeichen, Base64-codiert, endet auf `=`)

> **Wichtig:** Speichere den Private Key **ausschließlich** als Docker Secret (Abschnitt 2.4.1 / 5.2). Niemals in Plaintext in Konfigurationsdateien oder Shell-History.

**Server-Land wählen:**

Gluetun wählt automatisch einen optimalen Server innerhalb des konfigurierten Landes. Das gewünschte Land wird als Umgebungsvariable `SERVER_COUNTRIES` angegeben (z. B. `Switzerland`, `Germany`, `Netherlands`).

**Gluetun-Architektur im Stack:**

```
antigravity_net (Bridge br-antigravity)
    │
    └── gluetun-Container
          ├── WireGuard-Tunnel → NordVPN-Server (intern im Container)
          ├── FIREWALL=on (eingebauter Kill-Switch)
          └── agy-Container (network_mode: service:gluetun)
                └── Gesamter Traffic über Gluetuns WireGuard-Tunnel
```

Der agy-Container teilt den kompletten Netzwerk-Namespace mit Gluetun. Aus seiner Perspektive ist Gluetun sein Netzwerk-Stack — DNS-Auflösung, Routing und Kill-Switch werden von Gluetun übernommen.

### 2.3 nftables Regelwerk

Das nftables-Regelwerk auf dem Host implementiert zwei Sicherheitsebenen:

- **LAN-Isolation:** RFC-1918 Adressen werden aus dem `antigravity_net`-Bridge blockiert
- **Metadata-Blockade:** Cloud-Metadata-Endpoint (169.254.169.254) blockiert

> **Kill-Switch:** Der VPN Kill-Switch ist **nicht mehr Aufgabe von nftables**, sondern wird von Gluetun intern (`FIREWALL=on`) verwaltet. Fällt der WireGuard-Tunnel aus, blockiert Gluetun ausgehenden Traffic innerhalb seines Netzwerk-Namespaces — bevor Pakete überhaupt die nftables-Forward-Chain erreichen.

> **Gluetun-Egress:** Gluetun muss NordVPN-Endpoint-IPs (öffentliche IPs, UDP 51820 oder TCP 443) über das LAN-Interface des Hosts erreichen. Die `container_fw`-Forward-Chain erlaubt deshalb Egress von `br-antigravity` nach `$ETH_IF` (ausgenommen RFC-1918 und Metadata).

```bash
sudo nano /etc/nftables.conf
```

Inhalt — passe `ETH_IF` auf dein tatsächliches LAN-Interface an:

```nftables
#!/usr/sbin/nft -f

flush ruleset

# ─────────────────────────────────────────────
# Variablen
# ─────────────────────────────────────────────
define ETH_IF     = eth0            # LAN-Interface (ggf. enp3s0, ens3, etc.)
define AGY_BRIDGE = br-antigravity  # Docker-Bridge für antigravity_net
define AGY_SUBNET = 172.28.0.0/24
define RFC1918    = { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 }
define METADATA   = 169.254.169.254/32

# ─────────────────────────────────────────────
# Host-Firewall
# ─────────────────────────────────────────────
table inet host_fw {

  chain input {
    type filter hook input priority 0; policy drop;

    iifname "lo" accept
    ct state established,related accept

    iifname $ETH_IF tcp dport 22 accept
    iifname $ETH_IF tcp dport { 9000, 9443 } accept

    ip protocol icmp accept
    ip6 nexthdr icmpv6 accept

    drop
  }

  chain output {
    type filter hook output priority 0; policy accept;
  }
}

# ─────────────────────────────────────────────
# Container-Firewall (antigravity_net / Gluetun)
# ─────────────────────────────────────────────
table inet container_fw {

  chain forward {
    type filter hook forward priority -100; policy accept;

    # 1. RFC-1918 aus Containern blockieren (LAN-Isolation)
    #    Gilt für Gluetun und indirekt für agy (shared netns)
    iifname $AGY_BRIDGE ip daddr $RFC1918 \
      log prefix "nft-drop-rfc1918: " drop

    # 2. Cloud-Metadata blockieren
    iifname $AGY_BRIDGE ip daddr $METADATA \
      log prefix "nft-drop-metadata: " drop

    # 3. Gluetun-Egress zu öffentlichen IPs erlauben
    #    (NordVPN-Endpoints, *.googleapis.com, jules.google.com)
    #    Kill-Switch ist Gluetuns Aufgabe (FIREWALL=on intern)
    iifname $AGY_BRIDGE oifname $ETH_IF accept

    # 4. Return-Traffic
    iifname $ETH_IF oifname $AGY_BRIDGE \
      ct state established,related accept
  }

  chain postrouting {
    type nat hook postrouting priority srcnat;
    iifname $AGY_BRIDGE oifname $ETH_IF masquerade
  }
}
```

Regelwerk aktivieren:

```bash
sudo nft -c -f /etc/nftables.conf  # Syntax prüfen
sudo nft -f /etc/nftables.conf     # Laden

echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.d/99-antigravity.conf
sudo sysctl -p /etc/sysctl.d/99-antigravity.conf

sudo nft list ruleset
```

### 2.4 Docker installieren

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker $USER
sudo systemctl enable docker
sudo systemctl start docker
```

> **Wichtig:** Nach `usermod` aus- und wieder einloggen.

### 2.4.1 Docker Swarm initialisieren (Voraussetzung für Docker Secrets)

> **Warum Swarm?** Docker Secrets sind eine Swarm-Funktion. Ohne `docker swarm init` schlägt `docker secret create` mit `"This node is not a swarm manager"` fehl.

```bash
HOST_IP=$(ip route get 8.8.8.8 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
sudo docker swarm init --advertise-addr "$HOST_IP"

docker info --format '{{.Swarm.LocalNodeState}}'
# Erwartung: active

# agy API-Key:
read -rsp "agy API-Key: " AGY_KEY
printf '%s' "$AGY_KEY" | docker secret create agy_api_key_agent_01 -
unset AGY_KEY

# Jules API-Key:
read -rsp "Jules API-Key: " JULES_KEY
printf '%s' "$JULES_KEY" | docker secret create jules_api_key_agent_01 -
unset JULES_KEY

# NordVPN WireGuard Private Key:
read -rsp "NordVPN WireGuard Private Key: " WG_KEY
printf '%s' "$WG_KEY" | docker secret create nordvpn_wg_key_agent_01 -
unset WG_KEY

docker secret ls
# Erwartung: agy_api_key_agent_01, jules_api_key_agent_01, nordvpn_wg_key_agent_01
```

### 2.5 Portainer installieren

```bash
docker volume create portainer_data

docker run -d \
  --name portainer \
  --restart always \
  -p 9000:9000 \
  -p 9443:9443 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v portainer_data:/data \
  portainer/portainer-ce:latest

docker ps | grep portainer
```

Portainer Web-GUI: `https://<HOST-IP>:9443`

---

## 3. Git-Repository Struktur

```
antigravity-agent/
├── Dockerfile
├── Dockerfile.gluetun
├── docker-entrypoint.sh
├── gluetun-entrypoint.sh
├── seccomp-profile.json
├── AGENTS.md
├── mcp_config.json
└── .gitignore
```

### 3.1 Dockerfile (agy)

Unverändert gegenüber Rev. 2 — das agy-Image enthält kein VPN-Tooling, da Gluetun den Netzwerk-Stack vollständig übernimmt.

```dockerfile
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

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

RUN groupadd -g 1000 agentuser \
 && useradd -u 1000 -g 1000 -m -s /bin/bash agentuser

RUN curl -fsSL https://antigravity.google/cli/install.sh | bash \
 && mv /root/.local/bin/agy /usr/local/bin/agy \
 && chmod 755 /usr/local/bin/agy \
 && agy --version

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

COPY mcp_config.json /etc/antigravity/mcp_config.json
RUN chown root:root /etc/antigravity/mcp_config.json \
 && chmod 644 /etc/antigravity/mcp_config.json

ENV PATH="/usr/local/bin:${PATH}" \
    HOME="/home/agentuser" \
    TMPDIR="/home/agentuser/workspace/.tmp" \
    PIP_TMPDIR="/home/agentuser/workspace/.tmp" \
    PIP_NO_CACHE_DIR="1" \
    PYTHONPYCACHEPREFIX="/home/agentuser/workspace/.pycache" \
    JULES_MCP_CONFIG="/etc/antigravity/mcp_config.json"

WORKDIR /home/agentuser/workspace
RUN chown -R agentuser:agentuser /home/agentuser
USER agentuser

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["tail", "-f", "/dev/null"]
```

### 3.2 Dockerfile.gluetun

Dieses Dockerfile wrапpt das offizielle `qmcgaw/gluetun`-Image und fügt einen eigenen Entrypoint hinzu, der den NordVPN WireGuard Private Key aus einem Docker Secret liest und als `WIREGUARD_PRIVATE_KEY`-Umgebungsvariable exportiert, bevor der originale Gluetun-Entrypoint aufgerufen wird.

> **Warum ein Wrapper?** Gluetun liest `WIREGUARD_PRIVATE_KEY` direkt aus Umgebungsvariablen — das `_FILE`-Pattern (Docker-Secrets-Konvention) wird nicht nativ unterstützt. Das Wrapper-Pattern ist identisch zur agy-Secret-Strategie.

```dockerfile
FROM qmcgaw/gluetun:latest

# Wrapper-Entrypoint: liest WireGuard Private Key aus Docker Secret,
# exportiert ihn als WIREGUARD_PRIVATE_KEY, startet dann originalen Gluetun-Entrypoint.
COPY gluetun-entrypoint.sh /gluetun-secret-entrypoint.sh
RUN chmod +x /gluetun-secret-entrypoint.sh

ENTRYPOINT ["/gluetun-secret-entrypoint.sh"]
```

### 3.3 gluetun-entrypoint.sh

```bash
#!/bin/sh
set -e

# ──────────────────────────────────────────────────────────────────────
# gluetun-entrypoint.sh
# Liest den NordVPN WireGuard Private Key aus Docker Secret und
# exportiert ihn als WIREGUARD_PRIVATE_KEY für Gluetun.
# Ruft anschließend den originalen Gluetun-Entrypoint auf.
# ──────────────────────────────────────────────────────────────────────

NORDVPN_SECRET_NAME="${NORDVPN_WG_SECRET_NAME:-nordvpn_wg_key_agent_01}"
WG_KEY_FILE="/run/secrets/${NORDVPN_SECRET_NAME}"

if [ ! -f "$WG_KEY_FILE" ]; then
  echo "[GLUETUN-ENTRYPOINT] FEHLER: Secret-Datei nicht gefunden: $WG_KEY_FILE" >&2
  exit 1
fi

export WIREGUARD_PRIVATE_KEY
WIREGUARD_PRIVATE_KEY=$(tr -d '[:space:]' < "$WG_KEY_FILE")

if [ -z "$WIREGUARD_PRIVATE_KEY" ]; then
  echo "[GLUETUN-ENTRYPOINT] FEHLER: WIREGUARD_PRIVATE_KEY ist leer." >&2
  exit 1
fi

echo "[GLUETUN-ENTRYPOINT] WireGuard Private Key geladen (${#WIREGUARD_PRIVATE_KEY} Zeichen)."

# Originalen Gluetun-Entrypoint starten
exec /gluetun/entrypoint.sh "$@"
```

### 3.4 seccomp-profile.json

Unverändert gegenüber Rev. 2 — gilt ausschließlich für den agy-Container. Gluetun läuft mit `cap_add: NET_ADMIN` und benötigt ein eigenes, breiteres Syscall-Profil (oder keines).

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

### 3.5 AGENTS.md (Agy-Konfiguration)

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
- All network traffic routes through Gluetun VPN (NordVPN WireGuard)
- VPN Kill-Switch active: Gluetun blocks all traffic if VPN tunnel drops
- Read/write access limited to workspace directory
- Temporary files must use TMPDIR=/home/agentuser/workspace/.tmp

## Project Context
<!-- Hier projektspezifische Informationen einfügen -->
```

### 3.6 Jules MCP-Konfigurationsdatei

Unverändert — `mcp_config.json` bleibt identisch zu Rev. 2.

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

### 3.7 Docker Entrypoint-Skript (agy)

Unverändert — `docker-entrypoint.sh` für den agy-Container bleibt identisch zu Rev. 2.

```bash
#!/bin/bash
set -euo pipefail

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

[[ -z "$GOOGLE_API_KEY" ]] && { echo "[ENTRYPOINT] FEHLER: GOOGLE_API_KEY ist leer." >&2; exit 1; }
[[ -z "$JULES_API_KEY" ]]  && { echo "[ENTRYPOINT] FEHLER: JULES_API_KEY ist leer." >&2; exit 1; }

echo "[ENTRYPOINT] Secrets geladen. GOOGLE_API_KEY: ${GOOGLE_API_KEY:0:8}... JULES_API_KEY: ${JULES_API_KEY:0:8}..."

mkdir -p "${TMPDIR:-/home/agentuser/workspace/.tmp}"
mkdir -p "/home/agentuser/workspace/.pycache"

echo "[ENTRYPOINT] Laufzeitverzeichnisse bereit. TMPDIR=${TMPDIR}"

exec "$@"
```

---

## 4. Infrastruktur auf dem Host anlegen

### 4.1 Docker Netzwerk `antigravity_net`

Identisch zu Rev. 2 — das Netzwerk wird manuell angelegt und nie via Portainer gelöscht.

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

Verifikation:

```bash
docker network inspect antigravity_net | jq '.[0].IPAM.Config'
ip link show br-antigravity
```

> **Kritisch:** Nur Gluetun ist direkt am `antigravity_net` angebunden. Der agy-Container benutzt `network_mode: "service:gluetun"` und hat keine eigene Netzwerk-Bindung.

### 4.2 Verzeichnisstruktur

```bash
mkdir -p ~/antigravity_workspaces/agent_01
mkdir -p ~/.gemini
mkdir -p ~/.config/antigravity
```

---

## 5. Portainer konfigurieren

### 5.1 Git-Integration einrichten

Identisch zu Rev. 2 — Git-Credentials in Portainer hinterlegen.

### 5.2 Secrets anlegen

Drei Secrets werden benötigt:

#### Secret 1: Antigravity CLI API-Key

- **Name:** `agy_api_key_agent_01`
- **Bezug:** console.cloud.google.com → APIs & Services → Credentials → API key

#### Secret 2: Jules API-Key

- **Name:** `jules_api_key_agent_01`
- **Bezug:** jules.google.com/u/0/settings/api

#### Secret 3: NordVPN WireGuard Private Key *(neu in Rev. 3)*

- **Name:** `nordvpn_wg_key_agent_01`
- **Bezug:** my.nordaccount.com → NordVPN → Manual setup → WireGuard → Generate key pair → Private Key

> **Format:** 44 Zeichen, Base64-codiert (z. B. `wOEI9rqqbDwnc8a68vOyMc5tVOIiijxh0lE9ZUQZGFE=`). Keine Leerzeichen oder Zeilenumbrüche.

### 5.3 Stack deployen

**Stack-YAML** (im Web Editor oder als `docker-compose.yml` im Repo):

```yaml
# ──────────────────────────────────────────────────────────────────────
# Stack: antigravity_agent_01
# Rev. 3: Gluetun VPN-Container
#
# Architektur:
#   gluetun → antigravity_net → ETH_IF → NordVPN WireGuard Tunnel
#   agy (network_mode: service:gluetun) → teilt Gluetuns Netzwerk-Namespace
#
# Secrets:
#   agy_api_key_agent_01      → GOOGLE_API_KEY (via docker-entrypoint.sh)
#   jules_api_key_agent_01    → JULES_API_KEY (via docker-entrypoint.sh)
#   nordvpn_wg_key_agent_01   → WIREGUARD_PRIVATE_KEY (via gluetun-entrypoint.sh)
# ──────────────────────────────────────────────────────────────────────

networks:
  antigravity_net:
    external: true
    name: antigravity_net

services:

  # ── Gluetun VPN-Container ──────────────────────────────────────────
  gluetun:
    build:
      context: .
      dockerfile: Dockerfile.gluetun
    image: antigravity-gluetun:agent_01
    container_name: gluetun_agent_01

    # Gluetun benötigt NET_ADMIN um WireGuard-Interfaces zu verwalten
    cap_add:
      - NET_ADMIN

    networks:
      - antigravity_net

    restart: unless-stopped

    environment:
      - VPN_SERVICE_PROVIDER=nordvpn
      - VPN_TYPE=wireguard
      # WIREGUARD_PRIVATE_KEY wird von gluetun-entrypoint.sh aus Secret gelesen
      - NORDVPN_WG_SECRET_NAME=nordvpn_wg_key_agent_01
      - SERVER_COUNTRIES=Switzerland
      # Gluetuns eingebauter Kill-Switch (default: on)
      - FIREWALL=on
      # DNS over TLS via NordVPN
      - DOT=on
      - DOT_PROVIDERS=custom
      - DOT_CUSTOM_ADDRESS=103.86.96.100
      # Logging
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
      - nordvpn_wg_key_agent_01

  # ── Antigravity / agy Container ───────────────────────────────────
  agy_agent_01:
    build:
      context: .
      dockerfile: Dockerfile
    image: antigravity-agent:latest
    container_name: antigravity_agent_01

    # Teilt Gluetuns kompletten Netzwerk-Namespace:
    # → kein eigener network-Eintrag
    # → DNS, Routing und Kill-Switch via Gluetun
    network_mode: "service:gluetun"

    depends_on:
      gluetun:
        condition: service_healthy

    user: "1000:1000"

    restart: unless-stopped

    privileged: false
    security_opt:
      - no-new-privileges:true
      # Absoluter Pfad erforderlich (FIX-5)
      # Vorbereitung einmalig auf Host:
      #   sudo mkdir -p /etc/antigravity
      #   sudo cp seccomp-profile.json /etc/antigravity/seccomp-profile.json
      - seccomp:/etc/antigravity/seccomp-profile.json
    cap_drop:
      - ALL

    tmpfs:
      - /tmp:rw,noexec,nosuid,size=256m

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
      - /home/<DEIN_USER>/antigravity_workspaces/agent_01:/home/agentuser/workspace:rw
      - /home/<DEIN_USER>/.gemini:/home/agentuser/.gemini:rw
      - /home/<DEIN_USER>/.config/antigravity:/home/agentuser/.config/antigravity:rw
      - jules_cache_agent_01:/home/agentuser/.cache/jules
      - jules_config_agent_01:/home/agentuser/.config/jules
      - jules_local_agent_01:/home/agentuser/.local/share/jules

    secrets:
      - agy_api_key_agent_01
      - jules_api_key_agent_01

    environment:
      - AGY_ENABLE_JULES_MCP=true
      - JULES_MCP_CONFIG=/etc/antigravity/mcp_config.json

secrets:
  agy_api_key_agent_01:
    external: true
  jules_api_key_agent_01:
    external: true
  nordvpn_wg_key_agent_01:
    external: true

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

> **Placeholder:** `<DEIN_USER>` durch tatsächlichen Ubuntu-Username ersetzen.

> **Deployment-Reihenfolge:** Portainer startet Gluetun zuerst. Der agy-Container wartet via `depends_on: condition: service_healthy` bis Gluetun's Healthcheck (VPN-IP-Prüfung) erfolgreich ist — erst dann startet agy.

> **seccomp für Gluetun:** Gluetun läuft ohne Seccomp-Einschränkung (kein `security_opt: seccomp:...` im Gluetun-Service). NET_ADMIN benötigt Syscalls wie `setns`, `unshare`, `ioctl` mit Netlink-Sockets — ein restriktives Seccomp-Profil würde WireGuard-Setup blockieren.

---

## 6. Verifikation & Sicherheitsaudit

Öffne in Portainer: **Containers** → `antigravity_agent_01` → **>_ Console** → `/bin/bash`

```bash
# ──────────────────────────────────────────────────────────────────────
# TEST 1: VPN-Routing
# Da agy Gluetuns Netzwerk-Namespace teilt, sieht es die VPN-IP
# Erwartung: NordVPN Exit-Node IP (≠ deine Provider-IP)
# ──────────────────────────────────────────────────────────────────────
curl -s https://ifconfig.me
# Beispiel: 194.165.xxx.xxx (NordVPN CH-Node)

# ──────────────────────────────────────────────────────────────────────
# TEST 2: LAN-Isolation (nftables-Regel greift via Host-Forward-Chain)
# Erwartung: Timeout nach 3 Sekunden
# ──────────────────────────────────────────────────────────────────────
curl --connect-timeout 3 http://192.168.1.1  && echo "FAIL" || echo "OK - Timeout"
curl --connect-timeout 3 http://10.0.0.1     && echo "FAIL" || echo "OK - Timeout"

# ──────────────────────────────────────────────────────────────────────
# TEST 3: Cloud-Metadata-Blockade
# ──────────────────────────────────────────────────────────────────────
curl --connect-timeout 3 http://169.254.169.254 && echo "FAIL" || echo "OK - Timeout"

# ──────────────────────────────────────────────────────────────────────
# TEST 4: Gluetun Kill-Switch (auf dem HOST ausführen)
# Gluetun-Container stoppen → agy darf keinen Internetzugang haben
# (da agy Gluetuns Netzwerk-Namespace nutzt, verliert es mit Gluetun den Zugang)
# ──────────────────────────────────────────────────────────────────────
# HOST:
# docker stop gluetun_agent_01
# CONTAINER (gleichzeitig, falls noch erreichbar):
# curl --connect-timeout 5 https://ifconfig.me  → Erwartung: Fehler/Timeout
# HOST:
# docker start gluetun_agent_01

# ──────────────────────────────────────────────────────────────────────
# TEST 5: agy Binary erreichbar
# ──────────────────────────────────────────────────────────────────────
agy --version

# ──────────────────────────────────────────────────────────────────────
# TEST 6: Secrets als Umgebungsvariablen verfügbar
# ──────────────────────────────────────────────────────────────────────
echo "GOOGLE_API_KEY: ${GOOGLE_API_KEY:0:12}..."
echo "JULES_API_KEY:  ${JULES_API_KEY:0:12}..."

# ──────────────────────────────────────────────────────────────────────
# TEST 7: Jules API-Konnektivität
# Erwartung: HTTP 200
# ──────────────────────────────────────────────────────────────────────
curl -s -o /dev/null -w "Jules API HTTP Status: %{http_code}\n" \
  -H "Authorization: Bearer $JULES_API_KEY" \
  https://jules.google.com/api/v1/health

# ──────────────────────────────────────────────────────────────────────
# TEST 8: Gluetun Healthcheck-Status (auf dem HOST)
# ──────────────────────────────────────────────────────────────────────
# docker inspect gluetun_agent_01 --format '{{.State.Health.Status}}'
# Erwartung: healthy

# ──────────────────────────────────────────────────────────────────────
# TEST 9: TMPDIR ausführbar
# ──────────────────────────────────────────────────────────────────────
echo '#!/bin/bash' > "$TMPDIR/test_exec.sh"
chmod +x "$TMPDIR/test_exec.sh"
"$TMPDIR/test_exec.sh" && echo "OK - TMPDIR ist executable" || echo "FAIL"
rm -f "$TMPDIR/test_exec.sh"

# ──────────────────────────────────────────────────────────────────────
# TEST 10: Runtimes verfügbar
# ──────────────────────────────────────────────────────────────────────
python3 --version
node --version
gcc --version
```

**nftables Drop-Logs auf dem Host:**

```bash
sudo journalctl -k | grep "nft-drop" | tail -20
```

**Gluetun-Logs:**

```bash
docker logs gluetun_agent_01 | tail -30
# Erwartung: "VPN is up" und aktuelle Exit-IP
```

---

## 7. Betriebsanleitung

### 7.1 Container-Verwaltung

> **Wichtig:** Gluetun und agy sind abhängig. Gluetun immer **vor** agy starten, agy immer **vor** Gluetun stoppen (oder beide gemeinsam via Stack).

| Aktion | Portainer | CLI |
|---|---|---|
| Stack starten | Stacks → antigravity_agent_01 → Start | `docker stack deploy ...` |
| Stack stoppen | Stacks → antigravity_agent_01 → Stop | `docker stack rm antigravity_agent_01` |
| Gluetun-Logs | Containers → gluetun_agent_01 → Logs | `docker logs -f gluetun_agent_01` |
| agy-Shell | Containers → antigravity_agent_01 → >_ Console | `docker exec -it antigravity_agent_01 /bin/bash` |
| VPN-IP prüfen | Containers → gluetun_agent_01 → Console | `docker exec gluetun_agent_01 wget -qO- ifconfig.me` |

### 7.2 Agy im Container bedienen

Identisch zu Rev. 2 — agy via `docker exec -it antigravity_agent_01 /bin/bash` starten, dann `agy` aufrufen.

### 7.3 Logs & Monitoring

```bash
# Gluetun VPN-Status
docker logs --since 10m gluetun_agent_01 | grep -iE "VPN|tunnel|handshake|error"

# VPN-IP aus Gluetun prüfen
docker exec gluetun_agent_01 wget -qO- --timeout=5 https://ifconfig.me

# agy Container-Logs
docker logs -f antigravity_agent_01

# nftables Drop-Events
sudo journalctl -k --grep "nft-drop" --since "1 hour ago"

# Gluetun Healthcheck-Status
docker inspect gluetun_agent_01 --format '{{.State.Health.Status}}'
docker inspect gluetun_agent_01 --format '{{json .State.Health.Log}}' | jq '.[0]'

# Docker-Netzwerk
docker network inspect antigravity_net
```

### 7.4 Updates

#### Gluetun aktualisieren

```bash
# Im Repo: Dockerfile.gluetun anpassen (ggf. Pinning auf neue Version)
# Dann: Portainer → Stacks → antigravity_agent_01 → Pull and redeploy
```

#### WireGuard Private Key rotieren

```bash
# 1. Neuen Private Key auf my.nordaccount.com generieren
# 2. Altes Secret löschen und neues anlegen:
docker secret rm nordvpn_wg_key_agent_01
read -rsp "Neuer WireGuard Private Key: " WG_KEY
printf '%s' "$WG_KEY" | docker secret create nordvpn_wg_key_agent_01 -
unset WG_KEY
# 3. Stack neu deployen
```

---

## 8. Skalierung: Weitere Instanzen hinzufügen

Jede neue Instanz erhält einen eigenen Gluetun-Container (eigene VPN-Session), eigene Secrets und einen eigenen Workspace.

**Schritt 1:** Workspace anlegen

```bash
mkdir -p ~/antigravity_workspaces/agent_02
```

**Schritt 2:** Secrets anlegen

- `agy_api_key_agent_02`
- `jules_api_key_agent_02`
- `nordvpn_wg_key_agent_02` *(separates Key-Pair generieren — empfohlen)*

**Schritt 3:** Stack-YAML kopieren und ersetzen:

```
antigravity_agent_01   → antigravity_agent_02
gluetun_agent_01       → gluetun_agent_02
agy_agent_01           → agy_agent_02
nordvpn_wg_key_agent_01 → nordvpn_wg_key_agent_02
agent_01               → agent_02
```

> **Hinweis:** Mehrere Gluetun-Container verbinden sich als separate Clients zum VPN. Jeder bekommt eine eigene Exit-IP, was Quota-Verteilung für Jules und VPN-Server-Load-Balancing ermöglicht.

---

## 9. Troubleshooting

### Gluetun startet nicht (Exit Code 1)

```bash
docker logs gluetun_agent_01 | tail -30
# Häufige Ursachen:
# → "[GLUETUN-ENTRYPOINT] FEHLER: Secret-Datei nicht gefunden"
#   Fix: Secret 'nordvpn_wg_key_agent_01' in Portainer anlegen
#
# → "invalid WireGuard private key"
#   Fix: Key prüfen – muss 44 Zeichen Base64 sein, keine Leerzeichen
#
# → "permission denied" auf /dev/net/tun
#   Fix: cap_add: NET_ADMIN ist im Stack vorhanden?
```

### VPN verbindet sich nicht

```bash
# Gluetun-Logs auf WireGuard-Fehler prüfen
docker logs gluetun_agent_01 | grep -iE "wireguard|handshake|error|failed"

# Gluetun-Container interaktiv testen
docker exec gluetun_agent_01 sh -c "wget -qO- --timeout=10 https://ifconfig.me"
# Bei Timeout: Netzwerk-Konnektivität des Hosts prüfen
# Bei falscher IP: VPN-Verbindung prüft Server-Land-Konfiguration

# WireGuard Private Key validieren (44 Zeichen, gültige Base64)
docker exec gluetun_agent_01 sh -c \
  'wc -c < /run/secrets/nordvpn_wg_key_agent_01'
# Erwartung: 44 (ohne Newline) oder 45 (mit Newline)
```

### agy Container startet nicht

```bash
# agy wartet auf Gluetun healthy – prüfen ob Gluetun healthy ist:
docker inspect gluetun_agent_01 --format '{{.State.Health.Status}}'
# Falls "unhealthy": Gluetun-Logs prüfen

# Manuelle Abhängigkeit überspringen (temporär, nur zum Debuggen):
docker run --rm -it \
  --network container:gluetun_agent_01 \
  antigravity-agent:latest \
  /bin/bash
```

### Jules API gibt 401 / Unauthorized

Identisch zu Rev. 2:

```bash
docker exec antigravity_agent_01 bash -c \
  'echo "Key: ${JULES_API_KEY:0:16}... (${#JULES_API_KEY} Zeichen)"'
```

### LAN-Isolation greift nicht

```bash
# nftables-Regeln auf dem Host prüfen:
sudo nft list table inet container_fw
# nft-drop-rfc1918 Regel muss vorhanden sein

# Drop-Logs prüfen:
sudo journalctl -k | grep "nft-drop-rfc1918" | tail -10

# Regelwerk neu laden falls nötig:
sudo nft -f /etc/nftables.conf
```

### Gluetun-Healthcheck dauerhaft unhealthy

```bash
# Healthcheck-Befehl manuell im Container testen:
docker exec gluetun_agent_01 sh -c \
  "wget -qO- --timeout=5 https://ifconfig.me"
# Bei Timeout: NordVPN-Server möglicherweise überlastet → SERVER_COUNTRIES ändern
# Bei falscher IP: VPN verbunden, aber kein Route-Leak

# Gluetun-Logs auf DNS-Fehler prüfen:
docker logs gluetun_agent_01 | grep -i "dns\|dot\|resolve"
# Bei DNS-Fehler: DOT_CUSTOM_ADDRESS überprüfen oder DOT=off temporär setzen
```

### nftables-Regeln fehlen nach Reboot

```bash
sudo systemctl status nftables
sudo nft -f /etc/nftables.conf
sudo systemctl enable nftables
```

### Jules Named Volumes zurücksetzen

```bash
docker stop antigravity_agent_01
docker volume rm jules_cache_agent_01 jules_config_agent_01 jules_local_agent_01
docker start antigravity_agent_01
```

---

*Ende des Dokuments. Alle `<PLATZHALTER>` vor dem ersten Deploy anpassen. Nach Secret-Rotation immer Stack neu deployen.*
