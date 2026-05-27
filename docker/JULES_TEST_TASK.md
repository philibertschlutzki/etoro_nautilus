# JULES_TEST_TASK.md
# Vollständiger Integrations- und Sicherheits-Audit: Antigravity + Jules MCP Deployment

> **Verwendung:** Diese Datei als Task-Prompt in `agy` einfügen (Dateiinhalt in die agy-Session pasten
> oder via `agy --task JULES_TEST_TASK.md` übergeben, je nach CLI-Version).
>
> Jules führt alle Schritte autonom aus. Voraussetzung: Der Container läuft, Secrets sind gesetzt,
> WireGuard ist aktiv. Jules hat Shell-Zugriff auf den Container und (via `docker exec`) auf den Host.

---

## Deine Rolle

Du bist ein Deployment-Validierungsagent. Deine Aufgabe ist es, das Antigravity + Jules MCP Setup
**chirurgisch präzise** zu testen, Fehler zu dokumentieren, und `ANTIGRAVITY_DEPLOYMENT.md` auf Basis
deiner empirischen Befunde mit exakten Patches zu aktualisieren.

Du arbeitest **ausschließlich auf Basis von gemessenen Ist-Zuständen**, nie auf Basis von Annahmen.
Jeder Test wird mit dem tatsächlichen Output der Befehle belegt. Kein Test gilt als bestanden,
solange du den Befehlsoutput nicht selbst ausgeführt und validiert hast.

---

## Aufgaben-Übersicht

```
PHASE 1 │ Environment-Inspektion     (5 Tests)   – Host-seitige Voraussetzungen
PHASE 2 │ Netzwerk & Sicherheit      (8 Tests)   – VPN, nftables, Kill-Switch, LAN-Isolation
PHASE 3 │ Docker-Infrastruktur       (6 Tests)   – Swarm, Secrets, Netzwerk, Volumes
PHASE 4 │ Container-Laufzeit         (9 Tests)   – Binaries, Env, TMPDIR, Runtimes
PHASE 5 │ Jules MCP                  (5 Tests)   – API-Konnektivität, Config, Auth
PHASE 6 │ Seccomp & Härtung          (4 Tests)   – Syscall-Profil, Capabilities, noexec
PHASE 7 │ Regression: Bekannte Bugs  (5 Tests)   – Verifiziert gezielte Bugfixes
PHASE 8 │ Reporting & Patch-Output             – Markdown-Report + unified diff
```

---

## Vorbereitung: Test-Harness schreiben

Schreibe zunächst das folgende Python-Skript nach `/home/agentuser/workspace/test_harness.py`.
Es verwaltet alle Testergebnisse und generiert am Ende den Report und die Patches.

```python
#!/usr/bin/env python3
"""
test_harness.py – Antigravity Deployment Audit
Führe alle Tests aus, sammle Ergebnisse, generiere Report und Patches.
"""

import subprocess, json, sys, os, re, textwrap
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class TestResult:
    phase: str
    name: str
    status: str          # "PASS" | "FAIL" | "WARN" | "SKIP"
    expected: str
    actual: str
    detail: str = ""
    fix_required: bool = False
    patch_hint: str = ""  # Hinweis für ANTIGRAVITY_DEPLOYMENT.md Patch

RESULTS: list[TestResult] = []

def run(cmd: str, timeout: int = 15, shell: bool = True,
        container: str = "", ignore_error: bool = False) -> tuple[int, str, str]:
    """Führt Befehl aus – optional im Container via docker exec."""
    if container:
        cmd = f"docker exec {container} bash -c {repr(cmd)}"
    try:
        p = subprocess.run(cmd, shell=shell, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT nach {timeout}s"
    except Exception as e:
        return -2, "", str(e)

def record(phase, name, status, expected, actual, detail="",
           fix_required=False, patch_hint=""):
    r = TestResult(phase, name, status, expected, actual,
                   detail, fix_required, patch_hint)
    RESULTS.append(r)
    icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠", "SKIP": "○"}[status]
    color = {"PASS": "\033[32m", "FAIL": "\033[31m",
             "WARN": "\033[33m", "SKIP": "\033[90m"}[status]
    reset = "\033[0m"
    print(f"  {color}{icon} [{phase}] {name}{reset}")
    if status != "PASS":
        print(f"    Erwartet : {expected}")
        print(f"    Erhalten : {actual}")
    if detail:
        print(f"    Detail   : {detail}")

# ────────── CONTAINER NAME ERMITTELN ──────────────────────────────────────────
rc, CONTAINER, _ = run(
    "docker ps --filter 'name=antigravity' --format '{{.Names}}' | head -1"
)
if not CONTAINER:
    print("FEHLER: Kein laufender antigravity-Container gefunden.")
    sys.exit(1)
print(f"\n[Harness] Container: {CONTAINER}")

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Environment-Inspektion
# ═══════════════════════════════════════════════════════════════════════════════
print("\n═══ PHASE 1: Environment-Inspektion ════")

# T1.1 Ubuntu-Version
rc, out, _ = run("lsb_release -rs")
ok = out in ("22.04", "24.04")
record("P1", "Ubuntu-Version (22.04 oder 24.04)", "PASS" if ok else "FAIL",
       "22.04 oder 24.04", out,
       fix_required=not ok,
       patch_hint="Abschnitt 1: Systemvoraussetzungen – OS-Anforderung prüfen")

# T1.2 Kernel-Version (≥5.15 für nftables + WireGuard native)
rc, out, _ = run("uname -r")
try:
    major, minor = [int(x) for x in out.split(".")[:2]]
    ok = (major, minor) >= (5, 15)
except Exception:
    ok = False
record("P1", "Kernel ≥5.15", "PASS" if ok else "FAIL",
       "≥5.15.x", out,
       fix_required=not ok)

# T1.3 UID des laufenden Container-Users
rc, out, _ = run("id -u", container=CONTAINER)
host_uid_rc, host_uid, _ = run(f"stat -c %u $(docker inspect {CONTAINER} --format '{{{{.HostConfig.Binds}}}}' | grep -oP '/home/[^:]+' | head -1) 2>/dev/null || echo 'n/a'")
ok = rc == 0 and out.isdigit()
record("P1", "Container-User UID abrufbar", "PASS" if ok else "FAIL",
       "numerische UID", out)

# T1.4 IP-Forwarding aktiv
rc, out, _ = run("cat /proc/sys/net/ipv4/ip_forward")
ok = out.strip() == "1"
record("P1", "IP-Forwarding aktiv", "PASS" if ok else "FAIL",
       "1", out,
       fix_required=not ok,
       patch_hint="Abschnitt 2.3: sysctl ip_forward Befehl fehlt/funktioniert nicht")

# T1.5 openresolv vorhanden (NICHT klassisches resolvconf)
rc_ov, out_ov, _ = run("dpkg -l openresolv 2>/dev/null | grep -c '^ii'")
rc_rc, out_rc, _ = run("dpkg -l resolvconf 2>/dev/null | grep -c '^ii'")
has_openresolv = out_ov.strip() == "1"
has_resolvconf = out_rc.strip() == "1"
# Prüfe ob systemd-resolved im stub-only-Modus läuft
rc_sd, out_sd, _ = run("systemctl show systemd-resolved --property=ActiveState --value 2>/dev/null")
active_resolved = out_sd.strip() == "active"

if has_openresolv:
    status = "PASS"
    detail = "openresolv installiert (korrekt)"
elif has_resolvconf and active_resolved:
    status = "WARN"
    detail = f"resolvconf installiert, systemd-resolved aktiv → DNS-Konflikt möglich (FIX-4)"
else:
    status = "FAIL"
    detail = "Weder openresolv noch resolvconf gefunden"

record("P1", "DNS-Resolver: openresolv (FIX-4)",
       status, "openresolv installiert", out_ov,
       detail=detail,
       fix_required=(status != "PASS"),
       patch_hint="Abschnitt 2.1: 'resolvconf' → 'openresolv' + systemd-resolved stub-only-Modus")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Netzwerk & Sicherheit
# ═══════════════════════════════════════════════════════════════════════════════
print("\n═══ PHASE 2: Netzwerk & Sicherheit ════")

# T2.1 WireGuard Interface wg0 aktiv
rc, out, _ = run("ip link show wg0 2>/dev/null | head -1")
ok = "wg0" in out
record("P2", "wg0 Interface vorhanden", "PASS" if ok else "FAIL",
       "wg0@...: <...> mtu ...", out, fix_required=not ok)

# T2.2 WireGuard Peer-Handshake (≤120s zurück)
rc, out, _ = run("sudo wg show wg0 latest-handshakes 2>/dev/null | awk '{print $2}'")
try:
    import time
    ts = int(out.strip())
    age_s = int(time.time()) - ts
    ok = age_s < 180  # Handshake nicht älter als 3 Minuten
    detail = f"Handshake vor {age_s}s"
except Exception:
    ok = False
    detail = f"Handshake-Timestamp nicht parsebar: '{out}'"
record("P2", "WireGuard Peer-Handshake <3min", "PASS" if ok else "WARN",
       "Handshake <180s zurück", detail, detail=detail)

# T2.3 nftables METADATA-IP korrekt (FIX-1: 169.254.169.254, NICHT 169.254.254.169)
rc, out, _ = run("sudo nft list ruleset 2>/dev/null")
correct_meta   = "169.254.169.254" in out
incorrect_meta = "169.254.254.169" in out

if correct_meta and not incorrect_meta:
    status = "PASS"
    detail = "Korrekte Metadata-IP 169.254.169.254 in Ruleset"
elif incorrect_meta:
    status = "FAIL"
    detail = "FEHLER: Falsche Metadata-IP 169.254.254.169 im Ruleset (FIX-1 nicht angewendet)"
else:
    status = "WARN"
    detail = "Metadata-IP nicht im Ruleset gefunden – Regel evtl. anders formuliert"

record("P2", "nftables METADATA-IP korrekt (FIX-1)",
       status, "169.254.169.254/32", detail, detail=detail,
       fix_required=(status == "FAIL"),
       patch_hint="Abschnitt 2.3: define METADATA = 169.254.254.169/32 → 169.254.169.254/32")

# T2.4 nftables Kill-Switch-Regel vorhanden
has_killswitch = "nft-drop-novpn" in out
record("P2", "nftables Kill-Switch-Regel (nft-drop-novpn)",
       "PASS" if has_killswitch else "FAIL",
       "log prefix 'nft-drop-novpn'", "vorhanden" if has_killswitch else "FEHLT")

# T2.5 LAN-Isolation aus Container: RFC-1918 geblockt
# Test mehrere RFC-1918 Adressen
lan_tests = [("10.0.0.1", "10.0.0.0/8"), ("192.168.1.1", "192.168.0.0/16"), ("172.16.0.1", "172.16.0.0/12")]
lan_pass = True
lan_detail = []
for ip, cidr in lan_tests:
    rc, out, err = run(
        f"curl --connect-timeout 3 -s http://{ip} -o /dev/null -w '%{{http_code}}' 2>/dev/null || echo 'timeout'",
        container=CONTAINER, timeout=8, ignore_error=True
    )
    blocked = "timeout" in (out + err).lower() or rc != 0
    lan_detail.append(f"{ip}: {'geblockt ✓' if blocked else 'ERREICHBAR ✗'}")
    if not blocked:
        lan_pass = False

record("P2", "LAN-Isolation: RFC-1918 aus Container geblockt",
       "PASS" if lan_pass else "FAIL",
       "alle 3 IPs: Timeout", " | ".join(lan_detail),
       fix_required=not lan_pass,
       patch_hint="Abschnitt 2.3: nftables RFC-1918-Regel prüfen")

# T2.6 Cloud-Metadata-Endpunkt aus Container geblockt (korrigierte IP)
rc, out, err = run(
    "curl --connect-timeout 3 -s http://169.254.169.254 -o /dev/null -w '%{http_code}' 2>/dev/null || echo 'timeout'",
    container=CONTAINER, timeout=8, ignore_error=True
)
meta_blocked = "timeout" in (out + err).lower() or rc != 0
record("P2", "Metadata 169.254.169.254 aus Container geblockt",
       "PASS" if meta_blocked else "FAIL",
       "Timeout", out or err,
       fix_required=not meta_blocked)

# T2.7 Container-Traffic via VPN (nicht Host-IP)
rc_host, host_ip, _ = run("curl -s --connect-timeout 5 https://ifconfig.me 2>/dev/null || echo 'n/a'")
rc_cont, container_ip, _ = run(
    "curl -s --connect-timeout 8 https://ifconfig.me 2>/dev/null || echo 'timeout'",
    container=CONTAINER, timeout=12
)
vpn_ok = (container_ip not in ("timeout", "n/a", host_ip)
          and bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", container_ip)))
record("P2", "Container-Traffic via VPN (≠ Host-IP)",
       "PASS" if vpn_ok else ("WARN" if container_ip == "timeout" else "FAIL"),
       f"NordVPN-IP ≠ {host_ip}",
       f"Container: {container_ip} / Host: {host_ip}",
       fix_required=(not vpn_ok and container_ip != "timeout"))

# T2.8 Kill-Switch-Test: VPN stoppen → Container verliert Internet
print("  [T2.8] Kill-Switch-Test (wg0 wird temporär gestoppt – ca. 15s)...")
run("sudo systemctl stop wg-quick@wg0", timeout=10)
import time; time.sleep(5)
rc_ks, ks_out, _ = run(
    "curl -s --connect-timeout 5 https://ifconfig.me 2>/dev/null || echo 'blocked'",
    container=CONTAINER, timeout=10, ignore_error=True
)
run("sudo systemctl start wg-quick@wg0", timeout=15)
time.sleep(5)  # Neu verbinden lassen
ks_ok = "blocked" in ks_out.lower() or rc_ks != 0 or not re.match(r"^\d+\.\d+\.\d+\.\d+$", ks_out)
record("P2", "Kill-Switch: Container offline wenn wg0 down",
       "PASS" if ks_ok else "FAIL",
       "timeout/geblockt ohne wg0", ks_out,
       fix_required=not ks_ok,
       patch_hint="Abschnitt 2.3: nftables Kill-Switch-Regel greift nicht – Priority prüfen")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: Docker-Infrastruktur
# ═══════════════════════════════════════════════════════════════════════════════
print("\n═══ PHASE 3: Docker-Infrastruktur ════")

# T3.1 Docker Swarm aktiv (FIX-2)
rc, out, _ = run("docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null")
swarm_ok = out.strip() == "active"
record("P3", "Docker Swarm aktiv (FIX-2)",
       "PASS" if swarm_ok else "FAIL",
       "active", out,
       fix_required=not swarm_ok,
       patch_hint="Abschnitt 2.4: 'docker swarm init' fehlt nach Docker-Installation – neuen Abschnitt 2.4.1 einfügen")

# T3.2 Docker Secrets vorhanden und nicht leer
def check_secret(name):
    rc, out, _ = run(f"docker secret inspect {name} --format '{{{{.ID}}}}' 2>/dev/null")
    return rc == 0 and len(out.strip()) > 10

# Container-Name → Agent-Name ableiten
agent_match = re.search(r"antigravity_(agent_\w+)", CONTAINER)
agent_name = agent_match.group(1) if agent_match else "agent_01"

agy_secret   = f"agy_api_key_{agent_name}"
jules_secret  = f"jules_api_key_{agent_name}"

for sname in [agy_secret, jules_secret]:
    ok = check_secret(sname)
    record("P3", f"Docker Secret '{sname}'",
           "PASS" if ok else "FAIL",
           f"Secret-ID vorhanden", "✓" if ok else "FEHLT",
           fix_required=not ok)

# T3.3 Secret-Dateien im Container lesbar und nicht leer
for fname, envname in [(agy_secret, "GOOGLE_API_KEY"), (jules_secret, "JULES_API_KEY")]:
    rc, out, _ = run(
        f"wc -c < /run/secrets/{fname} 2>/dev/null || echo '0'",
        container=CONTAINER
    )
    try:
        char_count = int(out.strip())
    except Exception:
        char_count = 0
    ok = char_count > 30
    record("P3", f"Secret-Datei /run/secrets/{fname} nicht leer",
           "PASS" if ok else "FAIL",
           ">30 Zeichen", f"{char_count} Zeichen",
           fix_required=not ok)

# T3.4 Docker-Netzwerk antigravity_net: Subnet + Bridge-Name
rc, out, _ = run("docker network inspect antigravity_net --format '{{json .}}' 2>/dev/null")
try:
    net_data = json.loads(out)
    subnet   = net_data["IPAM"]["Config"][0]["Subnet"]
    bridge   = net_data.get("Options", {}).get("com.docker.network.bridge.name", "")
    subnet_ok = subnet == "172.28.0.0/24"
    bridge_ok = bridge == "br-antigravity"
    detail    = f"Subnet: {subnet} | Bridge: {bridge}"
except Exception:
    subnet_ok = bridge_ok = False
    detail = f"JSON-Parse-Fehler: {out[:80]}"

ok = subnet_ok and bridge_ok
record("P3", "Docker-Netzwerk antigravity_net: Subnet + Bridge",
       "PASS" if ok else ("WARN" if subnet_ok else "FAIL"),
       "Subnet=172.28.0.0/24, Bridge=br-antigravity", detail,
       fix_required=not ok)

# T3.5 Bridge br-antigravity im Kernel sichtbar
rc, out, _ = run("ip link show br-antigravity 2>/dev/null | head -1")
ok = "br-antigravity" in out
record("P3", "Bridge br-antigravity im Kernel",
       "PASS" if ok else "WARN",
       "br-antigravity: <...>", out,
       detail="Nur sichtbar wenn mindestens ein Container am Netz hängt")

# T3.6 Jules Named Volumes vorhanden
for vol in [f"jules_cache_{agent_name}", f"jules_config_{agent_name}", f"jules_local_{agent_name}"]:
    rc, out, _ = run(f"docker volume inspect {vol} --format '{{{{.Name}}}}' 2>/dev/null")
    ok = out.strip() == vol
    record("P3", f"Volume '{vol}'",
           "PASS" if ok else "FAIL",
           vol, out or "nicht gefunden",
           fix_required=not ok)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: Container-Laufzeit
# ═══════════════════════════════════════════════════════════════════════════════
print("\n═══ PHASE 4: Container-Laufzeit ════")

# T4.1 agy Binary vorhanden und ausführbar
rc, out, _ = run("agy --version 2>&1 || echo 'NOT_FOUND'", container=CONTAINER)
ok = "NOT_FOUND" not in out and rc == 0
record("P4", "agy Binary vorhanden und ausführbar",
       "PASS" if ok else "FAIL",
       "Versionsnummer (z.B. 2.0.x)", out,
       fix_required=not ok)

# T4.2 GOOGLE_API_KEY im Entrypoint gesetzt
rc, out, _ = run("echo ${#GOOGLE_API_KEY}", container=CONTAINER)
try:
    key_len = int(out.strip())
    ok = key_len > 30
except Exception:
    ok = False
    key_len = 0
record("P4", "GOOGLE_API_KEY gesetzt (via Entrypoint)",
       "PASS" if ok else "FAIL",
       ">30 Zeichen", f"{key_len} Zeichen",
       fix_required=not ok)

# T4.3 JULES_API_KEY im Entrypoint gesetzt
rc, out, _ = run("echo ${#JULES_API_KEY}", container=CONTAINER)
try:
    key_len = int(out.strip())
    ok = key_len > 30
except Exception:
    ok = False
    key_len = 0
record("P4", "JULES_API_KEY gesetzt (via Entrypoint)",
       "PASS" if ok else "FAIL",
       ">30 Zeichen", f"{key_len} Zeichen",
       fix_required=not ok,
       patch_hint="Abschnitt 3.5: Entrypoint-Skript – Secret-Pfad und Parsing prüfen")

# T4.4 TMPDIR-Pfad und exec-Berechtigung (noexec-Fix)
rc, out, _ = run("echo $TMPDIR", container=CONTAINER)
tmpdir = out.strip()
tmpdir_ok = "workspace/.tmp" in tmpdir

# Executable-Test im TMPDIR
rc_exec, out_exec, _ = run(
    f'echo \'#!/bin/sh\necho ok\' > {tmpdir}/test_$$.sh && chmod +x {tmpdir}/test_$$.sh && {tmpdir}/test_$$.sh && rm {tmpdir}/test_$$.sh',
    container=CONTAINER
)
exec_ok = rc_exec == 0 and "ok" in out_exec

record("P4", "TMPDIR korrekt gesetzt",
       "PASS" if tmpdir_ok else "FAIL",
       "*workspace/.tmp*", tmpdir)
record("P4", "TMPDIR exec-berechtigt (noexec-Fix)",
       "PASS" if exec_ok else "FAIL",
       "Script im TMPDIR ausführbar", out_exec or "keine Ausgabe",
       fix_required=not exec_ok)

# T4.5 Python3 Version (≥3.10)
rc, out, _ = run("python3 --version 2>&1", container=CONTAINER)
try:
    pyver = tuple(int(x) for x in re.search(r"(\d+)\.(\d+)", out).groups())
    ok = pyver >= (3, 10)
except Exception:
    ok = False
record("P4", "Python3 ≥3.10", "PASS" if ok else "FAIL",
       "Python 3.10.x oder höher", out)

# T4.6 Node.js v20
rc, out, _ = run("node --version 2>&1", container=CONTAINER)
ok = out.startswith("v20")
record("P4", "Node.js v20.x.x", "PASS" if ok else "WARN",
       "v20.x.x", out,
       detail="Jules MCP benötigt Node.js für TypeScript-Ausführung")

# T4.7 pip3 verfügbar
rc, out, _ = run("pip3 --version 2>&1 | head -1", container=CONTAINER)
ok = rc == 0 and "pip" in out
record("P4", "pip3 verfügbar", "PASS" if ok else "FAIL",
       "pip 22.x oder höher", out)

# T4.8 gcc (Build-Tools)
rc, out, _ = run("gcc --version 2>&1 | head -1", container=CONTAINER)
ok = rc == 0 and "gcc" in out
record("P4", "gcc (build-essential)", "PASS" if ok else "FAIL",
       "gcc 11.x oder höher", out)

# T4.9 Jules MCP Config-Datei vorhanden und valide
rc, out, _ = run("cat $JULES_MCP_CONFIG | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"mcpServers\"][\"jules\"][\"url\"])' 2>&1",
                 container=CONTAINER)
ok = "jules.google.com" in out
record("P4", "Jules MCP Config: URL korrekt",
       "PASS" if ok else "FAIL",
       "https://jules.google.com/api/mcp/v1", out,
       fix_required=not ok)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: Jules MCP
# ═══════════════════════════════════════════════════════════════════════════════
print("\n═══ PHASE 5: Jules MCP ════")

# T5.1 DNS-Auflösung jules.google.com aus Container
rc, out, _ = run(
    "getent hosts jules.google.com 2>/dev/null | awk '{print $1}' | head -1",
    container=CONTAINER, timeout=10
)
ok = bool(re.match(r"^\d+\.\d+\.\d+\.\d+$", out.strip()))
record("P5", "DNS: jules.google.com auflösbar aus Container",
       "PASS" if ok else "FAIL",
       "IPv4-Adresse", out or "keine Antwort",
       fix_required=not ok)

# T5.2 DNS-Resolver ist NordVPN (103.86.96.100 oder 103.86.99.100)
rc, out, _ = run("cat /etc/resolv.conf", container=CONTAINER)
nordvpn_dns = "103.86.96.100" in out or "103.86.99.100" in out
record("P5", "DNS-Resolver ist NordVPN (103.86.96.100)",
       "PASS" if nordvpn_dns else "WARN",
       "nameserver 103.86.96.100", out.replace("\n", " "),
       detail="Anderer DNS wäre ein DNS-Leak")

# T5.3 Jules API Health-Endpoint erreichbar und Key gültig
rc, out, err = run(
    'curl -s -o /dev/null -w "%{http_code}" '
    '-H "Authorization: Bearer $JULES_API_KEY" '
    'https://jules.google.com/api/v1/health 2>/dev/null || echo "000"',
    container=CONTAINER, timeout=20
)
http_code = out.strip()
if http_code == "200":
    status = "PASS"
    detail = "Jules API antwortet HTTP 200 – Key gültig"
elif http_code == "401":
    status = "FAIL"
    detail = "HTTP 401 – Jules API-Key ungültig oder abgelaufen"
elif http_code == "000":
    status = "WARN"
    detail = "Kein Netzwerkkontakt zu jules.google.com (VPN/DNS-Problem?)"
else:
    status = "WARN"
    detail = f"Unerwarteter HTTP-Code: {http_code}"

record("P5", "Jules API /v1/health → HTTP 200",
       status, "200", http_code, detail=detail,
       fix_required=(status == "FAIL"),
       patch_hint="Abschnitt 5.2: Jules API-Key erneuern unter jules.google.com/u/0/settings/api")

# T5.4 Jules MCP Endpoint erreichbar (MCP-Protokoll-Handshake)
rc, out, _ = run(
    'curl -s -o /dev/null -w "%{http_code}" '
    '-H "Authorization: Bearer $JULES_API_KEY" '
    '-H "Content-Type: application/json" '
    '-d \'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\' '
    'https://jules.google.com/api/mcp/v1 2>/dev/null || echo "000"',
    container=CONTAINER, timeout=20
)
http_code = out.strip()
ok = http_code in ("200", "400")  # 400 ist ok – Endpoint antwortet, Protocol-Error erwartet
record("P5", "Jules MCP /api/mcp/v1 erreichbar",
       "PASS" if ok else "WARN",
       "200 oder 400 (Endpoint antwortet)", http_code,
       detail="400 = Endpoint aktiv, aber MCP-Handshake-Parameter fehlen (erwartet)")

# T5.5 Jules MCP Config: ${JULES_API_KEY} wird zur Laufzeit aufgelöst
rc, out, _ = run(
    'python3 -c "'
    "import json,os; "
    "cfg=open(os.environ['JULES_MCP_CONFIG']).read(); "
    "key=os.environ.get('JULES_API_KEY',''); "
    "resolved=cfg.replace('\${JULES_API_KEY}',key); "
    "d=json.loads(resolved); "
    "h=d['mcpServers']['jules']['headers']['Authorization']; "
    "print('Bearer' in h and len(h)>30)"
    '"',
    container=CONTAINER
)
ok = "True" in out
record("P5", "Jules MCP Config: JULES_API_KEY wird aufgelöst",
       "PASS" if ok else "WARN",
       "True", out,
       detail="Antigravity muss ${JULES_API_KEY} zur Laufzeit aus Env substituieren")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: Seccomp & Härtung
# ═══════════════════════════════════════════════════════════════════════════════
print("\n═══ PHASE 6: Seccomp & Härtung ════")

# T6.1 Seccomp aktiv im Container (Seccomp: 2 in /proc/self/status)
rc, out, _ = run(
    "grep '^Seccomp:' /proc/self/status 2>/dev/null || echo 'Seccomp: 0'",
    container=CONTAINER
)
seccomp_mode = re.search(r"Seccomp:\s*(\d+)", out)
mode = int(seccomp_mode.group(1)) if seccomp_mode else 0
ok = mode == 2  # 2 = SECCOMP_MODE_FILTER (custom profile aktiv)
record("P6", "Seccomp-Modus 2 (custom filter) aktiv",
       "PASS" if ok else ("WARN" if mode == 1 else "FAIL"),
       "Seccomp: 2 (FILTER)", f"Seccomp: {mode}",
       detail="0=off, 1=strict, 2=filter(custom); 2 erwartet bei custom seccomp-profile.json",
       fix_required=(mode != 2),
       patch_hint="Abschnitt 5.3: seccomp-Pfad absolut angeben (FIX-5)")

# T6.2 seccomp-Pfad prüfen (absoluter Pfad in Stack-YAML erwartet – FIX-5)
rc, out, _ = run(
    f"docker inspect {CONTAINER} --format '{{{{json .HostConfig.SecurityOpt}}}}' 2>/dev/null"
)
try:
    sec_opts = json.loads(out)
    seccomp_val = next((s for s in sec_opts if "seccomp" in s), "")
    # Absoluter Pfad beginnt mit /
    is_absolute = seccomp_val.startswith("seccomp:/")
    detail = f"seccomp-Opt: {seccomp_val[:80]}"
    status = "PASS" if is_absolute and "seccomp" in seccomp_val else "WARN"
    if "seccomp=unconfined" in seccomp_val:
        status = "FAIL"
        detail = "seccomp=unconfined – kein Profil aktiv!"
except Exception:
    status = "WARN"
    detail = f"Parse-Fehler: {out[:80]}"

record("P6", "seccomp-Pfad absolut in Container-Config (FIX-5)",
       status, "seccomp:/absoluter/pfad/...", detail,
       fix_required=(status != "PASS"),
       patch_hint="Abschnitt 5.3: security_opt seccomp:./seccomp-profile.json → absoluter Pfad")

# T6.3 Capabilities: alle gedroppt (nur Whitelist)
rc, out, _ = run(
    "grep 'CapEff:' /proc/self/status 2>/dev/null || echo 'CapEff: 0000000000000000'",
    container=CONTAINER
)
cap_match = re.search(r"CapEff:\s*([0-9a-f]+)", out)
cap_eff = int(cap_match.group(1), 16) if cap_match else -1
ok = cap_eff == 0  # Alle Capabilities gedroppt
record("P6", "Alle Capabilities gedroppt (cap_drop: ALL)",
       "PASS" if ok else "WARN",
       "CapEff: 0000000000000000", out,
       detail=f"CapEff={hex(cap_eff)} – 0x0 = alle gedroppt")

# T6.4 /tmp noexec
rc, out, _ = run(
    "python3 -c \"import subprocess; r=subprocess.run(['mount'],capture_output=True,text=True); "
    "lines=[l for l in r.stdout.split(chr(10)) if 'tmpfs' in l and 'on /tmp ' in l]; print(lines[0] if lines else 'not_found')\"",
    container=CONTAINER
)
noexec_ok = "noexec" in out
record("P6", "/tmp als noexec gemountet",
       "PASS" if noexec_ok else "FAIL",
       "tmpfs on /tmp ... noexec ...", out,
       fix_required=not noexec_ok,
       patch_hint="Abschnitt 5.3: tmpfs /tmp:rw,noexec,nosuid,size=256m fehlt oder falsch")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 7: Regression – bekannte Bugs (FIX-1 bis FIX-5)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n═══ PHASE 7: Regression – Bekannte Bugfixes ════")

# R7.1 [FIX-1] Falsche METADATA-IP schlägt fehl (169.254.254.169 NICHT in ruleset)
rc, out, _ = run("sudo nft list ruleset 2>/dev/null")
wrong_ip_present = "169.254.254.169" in out
record("P7", "[FIX-1] Tippfehler-IP 169.254.254.169 NICHT im Ruleset",
       "FAIL" if wrong_ip_present else "PASS",
       "169.254.254.169 = nicht vorhanden", "vorhanden!" if wrong_ip_present else "nicht vorhanden ✓",
       fix_required=wrong_ip_present,
       patch_hint="Abschnitt 2.3: define METADATA = 169.254.254.169/32 → 169.254.169.254/32")

# R7.2 [FIX-2] Docker Swarm läuft
rc, out, _ = run("docker info --format '{{.Swarm.LocalNodeState}}'")
record("P7", "[FIX-2] Docker Swarm aktiv (für Secrets erforderlich)",
       "PASS" if out.strip() == "active" else "FAIL",
       "active", out.strip(),
       fix_required=out.strip() != "active",
       patch_hint="Abschnitt 2.4: Neuen Unterabschnitt 'Docker Swarm initialisieren' nach Docker-Installation einfügen")

# R7.3 [FIX-3] Healthcheck nutzt NICHT $JULES_API_KEY (da im healthcheck-Kontext leer)
rc, out, _ = run(
    f"docker inspect {CONTAINER} --format '{{{{json .Config.Healthcheck.Test}}}}' 2>/dev/null"
)
hc_uses_jules_key = "JULES_API_KEY" in out and "jules.google.com" in out
record("P7", "[FIX-3] Healthcheck ohne $JULES_API_KEY-Abhängigkeit",
       "FAIL" if hc_uses_jules_key else "PASS",
       "kein jules.google.com curl mit $JULES_API_KEY im Healthcheck",
       "jules.google.com im Healthcheck ✗" if hc_uses_jules_key else "nur agy --version ✓",
       fix_required=hc_uses_jules_key,
       patch_hint="Abschnitt 5.3: healthcheck.test = ['CMD-SHELL','agy --version > /dev/null 2>&1']")

# R7.4 [FIX-4] openresolv statt resolvconf
rc, ov, _ = run("dpkg -l openresolv 2>/dev/null | grep -c '^ii'")
record("P7", "[FIX-4] openresolv installiert (nicht resolvconf)",
       "PASS" if ov.strip() == "1" else "WARN",
       "openresolv installiert", ov.strip(),
       patch_hint="Abschnitt 2.1: resolvconf → openresolv + systemd-resolved stub-only")

# R7.5 [FIX-5] seccomp-Pfad absolut (nicht relativ ./)
rc, out, _ = run(
    f"docker inspect {CONTAINER} --format '{{{{json .HostConfig.SecurityOpt}}}}' 2>/dev/null"
)
has_relative = "seccomp:./" in out
record("P7", "[FIX-5] seccomp kein relativer Pfad (./) in Container-Config",
       "FAIL" if has_relative else "PASS",
       "kein 'seccomp:./' in SecurityOpt", "relativer Pfad gefunden ✗" if has_relative else "absoluter Pfad ✓",
       fix_required=has_relative,
       patch_hint="Abschnitt 5.3: security_opt: - seccomp:/absoluter/pfad/seccomp-profile.json")


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 8: Report generieren und Patches schreiben
# ═══════════════════════════════════════════════════════════════════════════════
print("\n═══ PHASE 8: Report & Patches ════")

PASS   = [r for r in RESULTS if r.status == "PASS"]
FAIL   = [r for r in RESULTS if r.status == "FAIL"]
WARN   = [r for r in RESULTS if r.status == "WARN"]
FIXES  = [r for r in RESULTS if r.fix_required]
total  = len(RESULTS)

ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── JSON-Report ────────────────────────────────────────────────────────────────
report_json = {
    "timestamp": ts,
    "container": CONTAINER,
    "summary": {"total": total, "pass": len(PASS), "fail": len(FAIL), "warn": len(WARN)},
    "fixes_required": len(FIXES),
    "results": [
        {
            "phase": r.phase, "name": r.name, "status": r.status,
            "expected": r.expected, "actual": r.actual,
            "fix_required": r.fix_required, "patch_hint": r.patch_hint
        } for r in RESULTS
    ]
}

json_path = Path("/home/agentuser/workspace/audit_report.json")
json_path.write_text(json.dumps(report_json, indent=2, ensure_ascii=False))
print(f"  → JSON-Report: {json_path}")

# ── Markdown-Report ────────────────────────────────────────────────────────────
def md_table_row(r: TestResult) -> str:
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⬜"}[r.status]
    return f"| {r.phase} | {r.name} | {icon} {r.status} | {r.actual[:60]} |"

md_lines = [
    f"# Antigravity Deployment Audit Report",
    f"",
    f"**Timestamp:** {ts}  ",
    f"**Container:** `{CONTAINER}`  ",
    f"**Ergebnis:** {len(PASS)}/{total} bestanden, {len(FAIL)} fehlgeschlagen, {len(WARN)} Warnungen",
    f"",
    f"---",
    f"",
    f"## Testergebnisse",
    f"",
    f"| Phase | Test | Status | Ist-Wert |",
    f"|-------|------|--------|----------|",
]
for r in RESULTS:
    md_lines.append(md_table_row(r))

if FAIL or FIXES:
    md_lines += ["", "---", "", "## Fehler & Patches für ANTIGRAVITY_DEPLOYMENT.md", ""]
    for r in FAIL + [w for w in WARN if w.fix_required]:
        md_lines += [
            f"### ❌ {r.name}",
            f"",
            f"- **Phase:** {r.phase}",
            f"- **Erwartet:** `{r.expected}`",
            f"- **Erhalten:** `{r.actual}`",
            f"- **Patch-Hinweis:** {r.patch_hint or 'Manuell prüfen'}",
            f"",
        ]

md_path = Path("/home/agentuser/workspace/audit_report.md")
md_path.write_text("\n".join(md_lines))
print(f"  → Markdown-Report: {md_path}")

# ── Patch-Datei: unified diff für ANTIGRAVITY_DEPLOYMENT.md ───────────────────
# Jules soll nach dem Test-Lauf auf Basis der RESULTS eine Patch-Datei generieren.
# Dieser Block erzeugt einen Platzhalter; Jules füllt ihn mit den tatsächlichen diffs.

patches_required = [(r.patch_hint, r.name, r.status) for r in FIXES if r.patch_hint]
patch_summary = "\n".join([f"- [{s}] {n}: {h}" for h, n, s in patches_required])

patch_path = Path("/home/agentuser/workspace/deployment_patches.txt")
patch_path.write_text(
    f"# Deployment Guide Patches – generiert {ts}\n\n"
    f"Folgende Abschnitte in ANTIGRAVITY_DEPLOYMENT.md müssen geändert werden:\n\n"
    f"{patch_summary or 'Keine Patches erforderlich.'}\n\n"
    f"# Jules: Bitte schreibe nach diesem Header die konkreten unified diffs.\n"
    f"# Format: --- a/ANTIGRAVITY_DEPLOYMENT.md / +++ b/ANTIGRAVITY_DEPLOYMENT.md\n"
)
print(f"  → Patch-Datei: {patch_path}")

# ── Abschlusszusammenfassung ───────────────────────────────────────────────────
print(f"\n{'═'*60}")
print(f"AUDIT ABGESCHLOSSEN")
print(f"{'═'*60}")
print(f"  Gesamt      : {total}")
print(f"  PASS        : {len(PASS)}")
print(f"  FAIL        : {len(FAIL)}")
print(f"  WARN        : {len(WARN)}")
print(f"  Fixes nötig : {len(FIXES)}")
print(f"{'═'*60}")

if FAIL:
    print(f"\nKRITISCHE FEHLER:")
    for r in FAIL:
        print(f"  ✗ {r.name}")
        if r.patch_hint:
            print(f"    → {r.patch_hint}")
    sys.exit(1)
else:
    print("\nAlle kritischen Tests bestanden.")
    sys.exit(0)
```

---

## Ausführungsreihenfolge für Jules

Führe die folgenden Schritte **in dieser Reihenfolge** aus:

### Schritt 1: Test-Harness ausführen

```bash
cd /home/agentuser/workspace
python3 test_harness.py 2>&1 | tee audit_run.log
```

Warte auf den vollständigen Abschluss. Lies `audit_run.log` vollständig.

### Schritt 2: Rohe Befehle für jeden FAIL manuell nachverfolgen

Für **jeden** Test mit Status `FAIL`: Führe den entsprechenden Shell-Befehl nochmals manuell aus
und dokumentiere den vollständigen Output in `audit_report.md` unter dem jeweiligen Fehler.

### Schritt 3: Patch-Datei mit echten unified diffs befüllen

Öffne `deployment_patches.txt` und füge für jeden `fix_required=True`-Test einen exakten
`unified diff` gegen `ANTIGRAVITY_DEPLOYMENT.md` ein. Format:

```
--- a/ANTIGRAVITY_DEPLOYMENT.md
+++ b/ANTIGRAVITY_DEPLOYMENT.md
@@ -85,2 +85,3 @@
-  resolvconf \
+  openresolv \
```

Verwende dabei die **tatsächliche Zeilennummer** aus dem Originaldokument.
Kein Patch ohne zugehörigen FAIL-Test.

### Schritt 4: Patches auf ANTIGRAVITY_DEPLOYMENT.md anwenden

```bash
# Backup anlegen
cp ANTIGRAVITY_DEPLOYMENT.md ANTIGRAVITY_DEPLOYMENT.md.bak

# Patches einlesen und anwenden (patch-Tool oder manuell via Python)
python3 - << 'EOF'
# Jules: Implementiere hier das Patch-Anwenden auf Basis von deployment_patches.txt
# Verwende ausschließlich Zeilen mit tatsächlich gemessenem FAIL-Status.
# Niemals Patches anwenden die keinen korrespondierenden FAIL-Test haben.
EOF
```

### Schritt 5: Finales Dokument validieren

```bash
# Prüfe: Alle bekannten Fehler-Strings aus den Patches sind nicht mehr im Dokument
grep -n "169\.254\.254\.169" ANTIGRAVITY_DEPLOYMENT.md && echo "FIX-1 FEHLT NOCH" || echo "FIX-1 OK"
grep -n "resolvconf" ANTIGRAVITY_DEPLOYMENT.md | grep -v "openresolv" | grep -v "#" && echo "FIX-4 FEHLT NOCH" || echo "FIX-4 OK"
```

### Schritt 6: Alle Artefakte ausgeben

```
/home/agentuser/workspace/
├── audit_run.log              # vollständiger Konsolenoutput
├── audit_report.json          # maschinenlesbare Ergebnisse
├── audit_report.md            # menschenlesbarer Report
├── deployment_patches.txt     # unified diffs
├── ANTIGRAVITY_DEPLOYMENT.md  # aktualisiertes Dokument (wenn patches angewendet)
└── test_harness.py            # das Testskript (zur Nachvollziehbarkeit)
```

---

## Invarianten (niemals verletzen)

1. **Kein Test gilt als bestanden ohne gemessenen Ist-Wert.** Keine Annahmen.
2. **Keine Patches ohne korrespondierenden FAIL.** Nur empirisch belegte Änderungen.
3. **Secret-Inhalte niemals in Logs oder Reports schreiben.** Nur Zeichenanzahl oder erste 8 Zeichen.
4. **Nach Kill-Switch-Test (`T2.8`) immer `wg-quick start wg0` bestätigen** bevor nächster Test.
5. **Patch-Format: immer unified diff mit Zeilennummern.** Keine prose-Beschreibungen ohne diff.
