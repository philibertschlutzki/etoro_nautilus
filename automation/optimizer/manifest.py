import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Issue #944/#1110 — konfigurierbare Study-Store-Wurzel. Die store-weite Sweep-Lock-Datei
# (``sweep._sweep_run_lock_path`` → ``WORK/"sweep"/".run.lock"``) schuetzt genau EINEN ``WORK``-Baum
# vor zwei gleichzeitigen, unabhaengigen Sweep-Prozessen — sie schuetzt NICHT vor zwei Prozessen mit
# je EIGENEM ``WORK``. Fuer paralleler Mehr-Symbol-Betrieb (siehe manuals/run_optimizer.md,
# Abschnitt „Paralleler Mehr-Symbol-Betrieb") setzt jeder Prozess ``OPTIMIZER_WORK_DIR`` VOR dem
# Start auf ein eigenes Verzeichnis, danach werden die Reports getrennt eingesammelt. Da ``WORK``
# an mehreren Stellen per ``from manifest import WORK`` beim Modul-Import gebunden wird, muss die
# Umgebungsvariable vor dem Prozessstart gesetzt sein (nicht erst zur Laufzeit per CLI-Flag
# umschaltbar).
WORK = Path(os.environ.get("OPTIMIZER_WORK_DIR", str(PROJECT_ROOT / "data" / "optimizer")))

# Issue #1270 (GH #1140), Pitfall #447-Klasse in AGENTS.md — Root-Cause: ``champions._champions_
# dir() = WORK / "champions"`` lag im WEGWERF-Verzeichnis, das ``logs/executor.sh`` je Lauf FRISCH
# anlegt (Empfehlung E-1 aus Issue #1142, ertragswirksam belegt, p = 0,046) — E-1 und der
# #702-Closed-Loop (Champion-Store als Gedaechtnis ueber Laeufe hinweg) schliessen einander
# KONSTRUKTIV aus, solange ein persistenter Zustand unter ``WORK`` liegt. ``CHAMPION_ROOT`` ist
# DESHALB explizit NICHT relativ zu ``WORK`` (der Bug-Klasse, die Pitfall #447 beschreibt),
# sondern an ``PROJECT_ROOT`` verankert — ein Wechsel von ``OPTIMIZER_WORK_DIR`` zwischen zwei
# Laeufen aendert diesen Pfad NICHT. Ueberschreibbar per ``OPTIMIZER_CHAMPION_DIR`` (derselbe
# Override-Mechanismus wie ``OPTIMIZER_WORK_DIR`` fuer ``WORK``, fuer Tests/Multi-Instanz-Betrieb).
CHAMPION_ROOT = Path(
    os.environ.get("OPTIMIZER_CHAMPION_DIR", str(PROJECT_ROOT / "data" / "optimizer" / "champions")))

# Issue #1252 (GH #1122) — derselbe Verankerungsgrund wie ``CHAMPION_ROOT``: der Lauf-
# Fingerabdruck-Index muss Duplikate ueber genau die WORK-Recycling-Grenze hinweg erkennen, die
# ihn sonst bei jedem Lauf leeren wuerde.
RUN_FINGERPRINT_INDEX_PATH = Path(os.environ.get(
    "OPTIMIZER_RUN_FINGERPRINT_INDEX", str(PROJECT_ROOT / "data" / "optimizer" / "run_fingerprints.jsonl")))

# Issue #1270 (GH #1140) Fix Punkt 3 — dieselbe Root-Cause-Klasse traf zusaetzlich den Symbol-Bar-
# Qualitaets-Cache (``sweep.write_symbol_bar_quality_cache``), den kalibrierten-Slippage-Cache
# (``sweep.calibrate_and_write_slippage_cache``) UND den Annualisierungsfaktor-Cache
# (``backtest_runner._annualization_factor_cache_path``) — alle drei lasen/schrieben bislang unter
# ``WORK`` (Symptom: ``symbol_bar_quality_cache.cache_found = false`` in 3/3 Laeufen, dieselbe
# Ursache wie der Champion-Store). ``PERSISTENT_CACHE_ROOT`` buendelt sie an EINER, ebenfalls
# ``PROJECT_ROOT``-verankerten Stelle. Ueberschreibbar per ``OPTIMIZER_PERSISTENT_CACHE_DIR``.
PERSISTENT_CACHE_ROOT = Path(os.environ.get(
    "OPTIMIZER_PERSISTENT_CACHE_DIR", str(PROJECT_ROOT / "data" / "optimizer" / "cache")))

def git_commit() -> str:
    """Returns the current git short hash, or 'unknown' if not available."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT)
        ).decode("utf-8").strip()
    except Exception:
        return "unknown"

def sha256_file(path: Path) -> str:
    """Returns the SHA-256 hash of the file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def write_json_atomic(path: Path, data: Any, *, indent: int = 2) -> None:
    """Issue #742 — schreibt ``data`` als JSON atomar nach ``path``: erst in eine eindeutige
    Tempdatei IM SELBEN Verzeichnis (damit ``os.replace`` auf demselben Filesystem bleibt, kein
    Cross-Device-Fehler), dann ``os.replace`` (POSIX-atomarer Rename) — ein Leser sieht entweder
    die alte oder die vollständige neue Datei, NIE einen Teilzustand/``.tmp``-Rest im Ergebnis."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, default=str)
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_jsonl_atomic(path: Path, record: dict) -> None:
    """Issue #1252 (GH #1122) — haengt ``record`` als EINE JSON-Zeile an eine JSONL-Datei an, mit
    derselben Sicherheitsgarantie wie ``write_json_atomic`` (kein Leser sieht jemals einen
    Teilzustand), aber ueber das dafuer passende Primitiv: ein JSONL-Index ist ein WACHSENDES
    Append-Only-Log, kein einzelnes, bei jeder Aenderung komplett ersetztes Dokument — das
    Temp-Datei-plus-``os.replace``-Muster von ``write_json_atomic`` wuerde hier einen echten
    Read-Modify-Write-Race zwischen mehreren gleichzeitigen Schreibern einfuehren (Leser A liest
    den Stand, Leser B haengt an, Leser A schreibt seinen (jetzt veralteten) Gesamtstand zurueck
    und ueberschreibt B's Eintrag). ``O_APPEND`` (POSIX) ist stattdessen das korrekte Primitiv fuer
    genau diesen Anwendungsfall: ein Schreibvorgang unterhalb ``PIPE_BUF`` (typischerweise 4096
    Byte, ein Fingerabdruck-Eintrag liegt weit darunter) ist auf jedem POSIX-System ATOMAR — kein
    Leser sieht jemals eine ineinander verschraenkte Zeile zweier gleichzeitiger Schreiber."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def read_jsonl(path: Path) -> list[dict]:
    """Issue #1252 (GH #1122) — liest eine JSONL-Datei (siehe ``append_jsonl_atomic``) als Liste
    von Dicts. Fehlt die Datei ⇒ leere Liste (frischer Index, kein Fehler). Eine einzelne kaputte
    Zeile (z. B. durch einen abgebrochenen Schreibvorgang VOR diesem Fix, oder Datenkorruption)
    wird uebersprungen statt den gesamten Index unlesbar zu machen — dieselbe Fail-Open-Konvention
    wie ``champions._read_entry``."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        return []
    return out


def library_versions() -> dict:
    """Issue #802 — Provenienz der Inferenz-relevanten Bibliotheksversionen (``pandas`` allen voran:
    #801/#802 zeigten, dass die Gueltigkeit der Log-Return-Identitaet von der installierten
    ``pandas``-Version abhing, nicht nur von der Konfiguration). Best-effort: eine fehlende/nicht
    importierbare Bibliothek liefert ``None`` statt den Report-Aufbau zu crashen (analog
    ``git_commit``/``catalog_fingerprint`` oben)."""
    versions: dict[str, str | None] = {}
    for name in ("pandas", "numpy", "optuna", "nautilus_trader"):
        try:
            mod = __import__(name)
            versions[name] = getattr(mod, "__version__", None)
        except Exception:
            versions[name] = None
    return versions


def catalog_fingerprint(catalog: Path | None = None) -> str:
    """Returns a stable fingerprint for the given catalog path based on its data.parquet files."""
    if catalog is None:
        catalog = PROJECT_ROOT / "data" / "nautilus"

    if not catalog.exists() or not catalog.is_dir():
        return "unknown_catalog_missing"

    parts = []
    for p in sorted(catalog.rglob("data.parquet")):
        rel = p.relative_to(catalog)
        st = p.stat()
        parts.append(f"{rel}:{st.st_size}:{int(st.st_mtime)}")

    if not parts:
        return "empty_catalog"

    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
