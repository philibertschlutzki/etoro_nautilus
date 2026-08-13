"""Issue #1024 (Katalog #866) — CI-Gate: jede committete ``logs/zusammenfassung_<run_id>.md`` muss
aus ihrer gleichnamigen ``logs/run_<run_id>.json`` bit-identisch regenerierbar sein
(``summary_de.generate_german_summary``).

Root-Cause #1024: ``summary_de.generate_german_summary(run_34b99e6e_20260812T041920057275.json)``
weicht von der committeten ``zusammenfassung_34b99e6e_20260812T041920057275.md`` um 219 (Stand
dieses Katalogs; nach den #1023/#1029/#1038-Textänderungen in dieser Serie mehr) Diff-Zeilen ab —
inklusive eines Vorzeichenwechsels beim TSLA-Buy&Hold. Die Gegenprobe (``8a59f96d_20260812_PLTR``/
``_BTC``) regeneriert bit-identisch: ``summary_de.py`` ist als deterministisch und korrekt
entlastet — die Divergenz liegt in den ARTEFAKTEN (zwei aus verschiedenen Regenerationen/Läufen
desselben ``run_id`` stammende Dateien, oder ein degradierter JSON-Teilschrieb, siehe Katalog #866
Abschnitt B-6/#1024 — Root-Cause bleibt OFFEN, keine SQLite-Study aus jenem Lauf ist in diesem
Repository verfügbar, um sie zu entscheiden).

Diese Gate-Datei macht die Divergenz zu einem SICHTBAREN, NAMENTLICH DOKUMENTIERTEN Ausnahme-
Eintrag (``_KNOWN_DIVERGENT_PAIRS``) statt eines stillschweigenden Nichttests — jedes ANDERE Paar
in ``logs/`` (einschliesslich jedes künftig committeten) MUSS bit-identisch regenerieren, sonst
wird dieser Test rot."""
import difflib
import json
from pathlib import Path

import pytest

from automation.optimizer import summary_de as sd
from automation.optimizer.manifest import sha256_file

_LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"

# Issue #1024 — bekannte, bislang ungeklärte Divergenz (siehe Moduldocstring). Jeder Eintrag hier
# MUSS auf eine offene Root-Cause-Untersuchung verweisen, niemals eine stille Dauerausnahme sein.
#
# Der urspruengliche Eintrag (``run_34b99e6e_20260812T041920057275.json``, die #1024-Root-Cause-
# Evidenz) wurde entfernt: die Datei ist aus ``logs/`` gelöscht (die #1023-Datenintegritaets-
# Bereinigung hat den betroffenen Mehr-Lauf-Report ersetzt) — ``test_known_divergent_pairs_are_
# still_committed`` haette sonst rot geschlagen (toter Ausnahme-Eintrag ohne zugehoerige Datei).
_KNOWN_DIVERGENT_PAIRS: dict[str, str] = {}


def _committed_run_json_files() -> list[Path]:
    if not _LOGS_DIR.exists():
        return []
    return sorted(_LOGS_DIR.glob("run_*.json"))


@pytest.mark.parametrize("run_json_path", _committed_run_json_files(), ids=lambda p: p.name)
def test_committed_summary_regenerates_bit_identically(run_json_path):
    md_path = _LOGS_DIR / f"zusammenfassung_{run_json_path.name[len('run_'):-len('.json')]}.md"
    if not md_path.exists():
        pytest.skip(f"keine zugehoerige .md fuer {run_json_path.name} committet")

    report = json.loads(run_json_path.read_text("utf-8"))
    regenerated = sd.generate_german_summary(report, report_sha256=sha256_file(run_json_path))
    committed = md_path.read_text("utf-8")
    diff = list(difflib.unified_diff(
        committed.splitlines(), regenerated.splitlines(), lineterm="",
        fromfile=str(md_path), tofile="regenerated"))

    if run_json_path.name in _KNOWN_DIVERGENT_PAIRS:
        assert diff, (
            f"{run_json_path.name} ist als bekannt-divergent eingetragen "
            f"({_KNOWN_DIVERGENT_PAIRS[run_json_path.name]}), regeneriert aber jetzt bit-identisch "
            "— Eintrag aus _KNOWN_DIVERGENT_PAIRS entfernen."
        )
        return

    assert not diff, (
        f"{md_path.name} regeneriert NICHT bit-identisch aus {run_json_path.name} "
        f"({len(diff)} Diff-Zeilen) — entweder die .md neu schreiben "
        "(summary_de.write_german_summary_for_report_path) oder, falls die Ursache ungeklaert ist, "
        "einen dokumentierten Eintrag in _KNOWN_DIVERGENT_PAIRS anlegen "
        f"(siehe Katalog #866/#1024):\n" + "\n".join(diff[:40])
    )


def test_known_divergent_pairs_are_still_committed():
    """Ein Eintrag in _KNOWN_DIVERGENT_PAIRS, dessen Datei nicht mehr existiert, ist toter Ballast."""
    for name in _KNOWN_DIVERGENT_PAIRS:
        assert (_LOGS_DIR / name).exists(), (
            f"_KNOWN_DIVERGENT_PAIRS referenziert {name}, das nicht mehr in logs/ liegt — Eintrag entfernen."
        )
