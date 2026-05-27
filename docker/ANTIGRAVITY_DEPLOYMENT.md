# ANTIGRAVITY_DEPLOYMENT.md
# Vollständige Deployment- und Betriebsanleitung: Antigravity CLI auf Ubuntu mit Portainer

> **Stand:** Mai 2026  
> **Architektur:** Host-seitiger NordVPN WireGuard Kill-Switch → nftables LAN-Isolation → Docker `antigravity_net` Bridge → Portainer-verwaltete Container  
> **Binary:** `agy` (Go-Binary, Google Antigravity CLI 2.0)  
> **Auth im Container:** API-Key via Docker Secret (headless, kein Browser)  
> **Git-Integration:** Portainer baut Images direkt aus Git-Repo (kein lokales `docker build`)

---

## Inhaltsverzeichnis

1. [Systemvoraussetzungen](#1-systemvoraussetzungen)
2. [Host-Vorbereitung](#2-host-vorbereitung)
   - 2.1 [Pakete installieren](#21-pakete-installieren)
   - 2.2 [NordVPN WireGuard einrichten](#22-nordvpn-wireguard-einrichten)
   - 2.3 [nftables Regelwerk](#23-nftables-regelwerk)
   - 2.4 [Docker installieren](#24-docker-installieren)
   - 2.5 [Portainer installieren](#25-portainer-installieren)
3. [Git-Repository Struktur](#3-git-repository-struktur)
   - 3.1 [Dockerfile](#31-dockerfile)
   - 3.2 [seccomp-profile.json](#32-seccomp-profilejson)
   - 3.3 [AGENTS.md (Agy-Konfiguration)](#33-agentsmd-agy-konfiguration)
4. [Infrastruktur auf dem Host anlegen](#4-infrastruktur-auf-dem-host-anlegen)
   - 4.1 [Docker Netzwerk `antigravity_net`](#41-docker-netzwerk-antigravity_net)
   - 4.2 [Verzeichnisstruktur](#42-verzeichnisstruktur)
5. [Portainer konfigurieren](#5-portainer-konfigurieren)
   - 5.1 [Git-Integration einrichten](#51-git-integration-einrichten)
   - 5.2 [Secret anlegen](#52-secret-anlegen)
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
| API-Key | Google Antigravity CLI API-Key (aus console.cloud.google.com) |

**Wichtige UID-Anmerkung:** Der Container läuft als `user: "1000:1000"`. UID 1000 wird auf den bestehenden Host-User gemappt. Verifiziere mit `id` auf dem Host — die UID muss 1000 sein. Falls abweichend, passe den `user:`-Wert im Stack-YAML entsprechend an.

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
  resolvconf \
  ufw
```

nftables beim Systemstart aktivieren:

```bash
sudo systemctl enable nftables
sudo systemctl start nftables
```

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
define ETH_IF       = eth0          # Dein LAN-Interface (ggf. enp3s0, ens3, etc.)
define VPN_IF       = wg0           # WireGuard Interface
define AGY_BRIDGE   = br-antigravity  # Docker-Bridge für antigravity_net
define AGY_SUBNET   = 172.28.0.0/24  # Subnetz des Docker-Netzwerks
define RFC1918      = { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 }
define METADATA     = 169.254.254.169/32

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
├── seccomp-profile.json
├── AGENTS.md
└── .gitignore
```

### 3.1 Dockerfile

```dockerfile
# ──────────────────────────────────────────────────────────────────────────────
# Antigravity Agent Container
# Base: Ubuntu 22.04 LTS (minimal)
# Binary: agy (Google Antigravity CLI Go-Binary)
# Auth: Headless via API-Key (kein Browser erforderlich)
# ──────────────────────────────────────────────────────────────────────────────
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

# ── System-Pakete ──
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      gnupg \
      jq \
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

# ── PATH für agentuser setzen ──
ENV PATH="/usr/local/bin:${PATH}"
ENV HOME="/home/agentuser"

# ── Arbeitsverzeichnis ──
WORKDIR /home/agentuser/workspace

# ── Ownership: Workspace dem agentuser übergeben ──
RUN chown -R agentuser:agentuser /home/agentuser

# ── Ab hier als non-root agentuser ausführen ──
USER agentuser

# ── Standardbefehl: agy interaktive Shell ──
# In Produktion wird der Container im Hintergrund gehalten und
# via Portainer Console oder docker exec angesprochen.
CMD ["tail", "-f", "/dev/null"]
```

> **Hinweis zu `CMD`:** `tail -f /dev/null` hält den Container am Leben ohne eigenen Prozess zu starten. `agy`-Sessions werden via `docker exec` oder Portainer Console interaktiv gestartet (siehe [Betriebsanleitung](#7-betriebsanleitung)).

### 3.2 seccomp-profile.json

Dieses Profil erlaubt alle Syscalls, die `agy` (Go-Binary, Netzwerk, Dateisystem) benötigt, und blockiert explizit gefährliche Kernel-Schnittstellen.

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
        "io_setup", "io_submit", "ioctl", "kill", "lchown", "lgetxattr",
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

`agy` liest `AGENTS.md` aus dem Workspace-Root als Kontextdatei für den Agenten. Passe sie pro Instanz an:

```markdown
# Agent Configuration

## Role
This agent operates within an isolated Docker environment.
Workspace: /home/agentuser/workspace

## Constraints
- No access to LAN resources
- All network traffic routes through NordVPN
- Read/write access limited to workspace directory

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
```

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

# Verzeichnisstruktur anzeigen
tree ~/antigravity_workspaces ~/.gemini ~/.config/antigravity
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

### 5.2 Secret anlegen

API-Keys werden als Docker Secret hinterlegt — sie erscheinen nie in Umgebungsvariablen oder `docker inspect`.

1. Portainer → **Secrets** → **Add secret**
2. Fülle aus:
   - **Name:** `agy_api_key_agent_01`
   - **Secret:** API-Key im Klartext (wird verschlüsselt gespeichert)
3. Klicke **Create secret**

**API-Key beschaffen:** `agy`-Headless-Auth verwendet einen Gemini/Google API-Key. Dieser wird unter [console.cloud.google.com](https://console.cloud.google.com) → **APIs & Services** → **Credentials** → **Create credentials** → **API key** erstellt. Die Antigravity CLI API muss im Projekt aktiviert sein.

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
# ──────────────────────────────────────────────────────────────────────

networks:
  antigravity_net:
    external: true
    name: antigravity_net

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
      - seccomp:./seccomp-profile.json
    cap_drop:
      - ALL

    # tmpfs für /tmp (kein Exec, kein SUID, RAM-limitiert)
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=256m

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

    secrets:
      - agy_api_key_agent_01

    environment:
      # agy liest den Key aus dieser Datei (headless Auth, kein Browser)
      - GOOGLE_API_KEY_FILE=/run/secrets/agy_api_key_agent_01

secrets:
  agy_api_key_agent_01:
    external: true
```

> **Placeholder:** Ersetze `<DEIN_USER>` mit deinem tatsächlichen Ubuntu-Username (z. B. `phili`).

5. Klicke **Deploy the stack**

Portainer klont das Repo, baut das Image und startet den Container. Der Build-Vorgang dauert beim ersten Mal 2–5 Minuten (agy-Download inbegriffen).

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
# Erwartung: Versionsnummer (z. B. 1.0.1)

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
```

**Headless API-Key Authentifizierung:** Der Container nutzt `GOOGLE_API_KEY_FILE` (gesetzt auf den Secret-Pfad `/run/secrets/agy_api_key_agent_01`). `agy` liest diesen Key beim Start automatisch — kein manueller Login-Schritt erforderlich.

Falls `agy` beim Start nach Login fragt, obwohl das Secret korrekt gesetzt ist:

```bash
# Secret-Inhalt prüfen (als root oder via sudo)
cat /run/secrets/agy_api_key_agent_01
# Erwartung: API-Key ohne Leerzeichen/Newlines

# Env-Variable prüfen
echo $GOOGLE_API_KEY_FILE
# Erwartung: /run/secrets/agy_api_key_agent_01
```

### 7.3 Logs & Monitoring

```bash
# Alle Container-Logs der letzten Stunde
docker logs --since 1h antigravity_agent_01

# nftables Drop-Events (geblockte Verbindungen)
sudo journalctl -k --grep "nft-drop" --since "1 hour ago"

# WireGuard Status (VPN aktiv?)
sudo wg show

# Netzwerk-Traffic durch wg0 (Pakete/Bytes)
sudo wg show wg0 | grep "transfer"

# Docker-Netzwerk Übersicht
docker network inspect antigravity_net
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

Jede neue Instanz erhält einen isolierten Workspace und ein eigenes Secret. Die globale `agy`-Konfiguration (`~/.gemini`, `~/.config/antigravity`) wird geteilt.

**Schritt 1: Workspace anlegen**

```bash
mkdir -p ~/antigravity_workspaces/agent_02
```

**Schritt 2: Secret anlegen** (Portainer → Secrets → Add secret)

- Name: `agy_api_key_agent_02`
- Wert: separater API-Key (oder derselbe, je nach Anforderung)

**Schritt 3: Neuen Stack deployen** (Portainer → Stacks → Add stack)

Stack-Name: `antigravity_agent_02`  
YAML — kopiere das Stack-YAML aus Abschnitt 5.3 und ersetze:

```
antigravity_agent_01 → antigravity_agent_02
agy_agent_01         → agy_agent_02
agy_api_key_agent_01 → agy_api_key_agent_02
agent_01             → agent_02
```

Das Netzwerk `antigravity_net` bleibt `external: true` — keine Änderung erforderlich.

---

## 9. Troubleshooting

### Container startet nicht (Exit Code 1)

```bash
docker logs antigravity_agent_01
# Häufig: Permission-Fehler auf Volume-Mounts → UID/GID prüfen
ls -la ~/antigravity_workspaces/agent_01
# Erwartung: Owner ist UID 1000

# Fix:
chown -R 1000:1000 ~/antigravity_workspaces/agent_01
```

### VPN-Verbindung bricht ab (Kill-Switch greift)

```bash
# Host: WireGuard-Status
sudo wg show
sudo systemctl status wg-quick@wg0

# Neustart
sudo systemctl restart wg-quick@wg0
```

### agy gibt "Authentication failed" aus

```bash
# Secret korrekt gemountet?
docker exec antigravity_agent_01 cat /run/secrets/agy_api_key_agent_01

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

---

*Ende des Dokuments. Alle Konfigurationswerte mit `<PLATZHALTER>` müssen vor dem ersten Deploy angepasst werden.*
