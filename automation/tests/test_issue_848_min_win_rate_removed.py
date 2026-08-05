"""Issue #848 (P1) — `min_win_rate` streichen (5. Katalog) und Fingerprint-Homogenität herstellen.

117× `[#660]`-Warnung: `eligible_requires_any = ['min_profit_factor', 'min_win_rate']` — der
zweite Arm ist strukturell unerreichbar (fünf aufeinanderfolgende Kataloge: #660 → #668 → #678 →
#812 → hier), die Klausel kollabiert still auf `min_profit_factor`. `any_arm_unreachable_
policy='drop_arm'` behandelt die Symptomatik bereits korrekt, aber solange die Klausel in der
Config steht, feuert die Warnung jeden Lauf erneut UND kann bei einer künftigen Rekalibrierung
unbemerkt reaktiviert werden.

16× `[#812]`: zwei verschiedene `selection_rule_fingerprint` über dieselbe Familie — Ursache war
derselbe `drop_arm`-Mechanismus (in Study A verworfen, in Study B nicht). Mit der Entfernung
verschwindet die Ursache; ein danach noch auftretender zweiter Fingerprint ist eine ANDERE,
unbekannte Ursache und wird jetzt als FAIL (statt WARNING) gemeldet.

Akzeptanzkriterien:
- AK-1: 0 `[#660]`-Warnungen im Re-Run (min_win_rate ist kein eligible_requires_any-Arm mehr).
- AK-2: check_selection_rule_homogeneity PASST für alle Familien; ein künstlich erzeugter zweiter
  Fingerprint FAILt den Test.
"""
import json

from automation.optimizer import invariants as inv

_TCFG = json.loads(open("automation/config/tournament.json", encoding="utf-8").read())


# ── AK-1: min_win_rate ist kein eligible_requires_any-Arm mehr ───────────────────────────────────
def test_ak1_min_win_rate_removed_from_eligible_requires_any():
    # Issue #888 — 'min_profit_factor' war danach das letzte Element von eligible_requires_any
    # (eine Ein-Element-Disjunktion ist logisch eine Konjunktion, Pitfall #279) und wurde nach
    # eligible_requires_all verschoben; eligible_requires_any ist seither leer.
    assert "min_win_rate" not in _TCFG["eligible_requires_any"]
    assert _TCFG["eligible_requires_any"] == []


def test_min_win_rate_value_stays_documented_but_inert():
    """Der Wert bleibt in der Config (Reaktivierungs-Referenz), ist aber kein aktiver Gate-Arm."""
    assert "min_win_rate" in _TCFG  # Wert bleibt dokumentiert
    assert "min_win_rate" not in _TCFG["eligible_requires_any"]  # aber nicht mehr aktiv


def test_any_arm_unreachable_policy_stays_drop_arm():
    """Bleibt fuer kuenftige Arme wirksam (Issue #848 Umsetzung Punkt 2)."""
    assert _TCFG["any_arm_unreachable_policy"] == "drop_arm"


# ── AK-2: check_selection_rule_homogeneity ────────────────────────────────────────────────────────
def test_ak2_single_fingerprint_per_symbol_passes():
    families = {
        "XOM.ETORO": {"fp-abc123": 40},
        "NFLX.ETORO": {"fp-def456": 35},
    }
    result = inv.check_selection_rule_homogeneity(families)
    assert result.passed is True
    assert result.actual is None


def test_ak2_second_fingerprint_fails():
    families = {
        "XOM.ETORO": {"fp-abc123": 40, "fp-artificial-second": 3},
    }
    result = inv.check_selection_rule_homogeneity(families)
    assert result.passed is False
    assert result.actual == {"XOM.ETORO": 2}
    assert result.severity == "high"


def test_empty_families_pass():
    result = inv.check_selection_rule_homogeneity({})
    assert result.passed is True


def test_unknown_bucket_counts_as_a_fingerprint():
    """Studies ohne Fingerprint (Pre-#812-Lauf) landen unter 'unknown' -- zusammen mit einem
    echten Fingerprint ist das ebenfalls > 1 Bucket und muss FAILen."""
    families = {"XOM.ETORO": {"unknown": 10, "fp-real": 30}}
    result = inv.check_selection_rule_homogeneity(families)
    assert result.passed is False
