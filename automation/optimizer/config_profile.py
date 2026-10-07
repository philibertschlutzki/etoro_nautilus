"""Issue #1381 (GH #1283, Pitfall #502) — Config-Profile als eigenes Konzept.

Eine abweichende Geometrie (z. B. ein Durchstich auf 96 Tagen) war bisher nur über ein von Hand kopiertes
``ETORO_CONFIG_DIR`` möglich: nichts im Report kennzeichnete den Lauf, Champion-Store und Promotion-Records
waren nicht von Produktion zu unterscheiden. Jetzt:

* ``automation/config/config_profiles.json`` hält die Overrides je Profil (Startbestand: ``smoke``).
* ``python -m automation.optimizer.config_profile materialize smoke`` erzeugt ``automation/config_smoke/`` als
  Kopie von ``automation/config`` + Overrides + ``optimizer.json["config_profile"] = "smoke"``.
* Das Overlay MUSS unter ``<PROJECT_ROOT>/automation/<name>/`` liegen: der Katalogpfad wird als
  ``config_dir().parent.parent / catalog_path`` aufgelöst (``sweep.count_available_bars``) — ein Overlay anderswo
  liest einen falschen Katalog (``materialize`` prüft ``overlay.parent.parent == PROJECT_ROOT``).
* Sweep/Report/Summary stempeln ``config_profile``; ``champions`` schreibt und liest nur bei ``production``;
  Promotion-Records tragen ``config_profile``; die Deployment-Grenze hat die 14. Klausel
  ``config_profile_production`` (fail-closed bei fehlendem Feld).

Ergebnisse eines Nicht-Produktionsprofils sind NIE Evidenz."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_CONFIG_DIR = PROJECT_ROOT / "automation" / "config"
PROFILES_PATH = BASE_CONFIG_DIR / "config_profiles.json"
PRODUCTION = "production"


class ConfigProfileError(ValueError):
    """Ungültiges Profil bzw. Overlay-Ziel."""


def load_profiles(path: Path | None = None) -> dict[str, dict[str, dict]]:
    """``{profil: {datei: overrides}}`` aus ``config_profiles.json`` (ohne den ``_schema``-Block)."""
    raw = json.loads((path or PROFILES_PATH).read_text("utf-8")) or {}
    return {name: spec for name, spec in raw.items() if not name.startswith("_")}


def overlay_dir(profile: str, *, project_root: Path | None = None) -> Path:
    """Zielverzeichnis des Overlays: ``<PROJECT_ROOT>/automation/config_<profil>``."""
    return (project_root or PROJECT_ROOT) / "automation" / f"config_{profile}"


def deep_merge(base: dict, overrides: dict) -> dict:
    """Rekursives Überschreiben (Dicts werden zusammengeführt, alles andere ersetzt)."""
    out = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def materialize(profile: str, *, project_root: Path | None = None, base_dir: Path | None = None,
                profiles: dict | None = None, dest: Path | None = None) -> Path:
    """Erzeugt das Overlay von ``profile`` und gibt sein Verzeichnis zurück (bestehendes Overlay wird ersetzt)."""
    root = (project_root or PROJECT_ROOT).resolve()
    base = base_dir or (root / "automation" / "config")
    if profile == PRODUCTION:
        raise ConfigProfileError("'production' ist automation/config selbst und wird nicht materialisiert.")
    profiles = profiles if profiles is not None else load_profiles(base / "config_profiles.json")
    if profile not in profiles:
        raise ConfigProfileError(f"Unbekanntes Profil {profile!r}; bekannt: {sorted(profiles)}.")
    overlay = (dest or overlay_dir(profile, project_root=root)).resolve()
    if overlay.parent.parent != root:
        raise ConfigProfileError(
            f"Das Overlay {overlay} liegt nicht unter {root}/automation/<name>/ — der Katalogpfad "
            f"(config_dir().parent.parent / catalog_path) würde einen falschen Katalog lesen.")
    if overlay == base.resolve():
        raise ConfigProfileError("Das Overlay darf nicht automation/config selbst sein.")
    if overlay.exists():
        shutil.rmtree(overlay)
    shutil.copytree(base, overlay, ignore=shutil.ignore_patterns("__pycache__", "config_profiles.json"))
    for filename, overrides in (profiles[profile] or {}).items():
        target = overlay / filename
        data = json.loads(target.read_text("utf-8")) if target.exists() else {}
        target.write_text(json.dumps(deep_merge(data, overrides), indent=2, ensure_ascii=False) + "\n", "utf-8")
    opt_path = overlay / "optimizer.json"
    opt = json.loads(opt_path.read_text("utf-8"))
    opt["config_profile"] = profile
    opt_path.write_text(json.dumps(opt, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return overlay


def current_profile(opt_data: dict | None) -> str:
    """Aktives Profil aus einer geladenen ``optimizer.json`` (fehlender Key ⇒ ``production``)."""
    return str((opt_data or {}).get("config_profile") or PRODUCTION)


def is_production(opt_data: dict | None) -> bool:
    return current_profile(opt_data) == PRODUCTION


def banner(profile: str) -> str | None:
    """Erste Zeile von Report-Summary und Konsole für ein Nicht-Produktionsprofil, sonst ``None``."""
    if profile == PRODUCTION:
        return None
    return f"{profile.upper()} — keine Evidenz"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Config-Profile (Issue #1381)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mat = sub.add_parser("materialize", help="Overlay automation/config_<profil>/ erzeugen")
    mat.add_argument("profile")
    args = parser.parse_args(argv)
    try:
        overlay = materialize(args.profile)
    except ConfigProfileError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    print(f"Overlay erzeugt: {overlay}")
    print(f'Lauf: ETORO_CONFIG_DIR="{overlay}" OPTIMIZER_WORK_DIR=… python -m automation.optimizer.sweep …')
    return 0


if __name__ == "__main__":
    sys.exit(main())
