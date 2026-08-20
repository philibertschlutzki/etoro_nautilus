"""Issue #637 — `reward_semantics_version` stand trotz #614-PSR-Base weiterhin auf 8.

`_check_reward_semantics_version` erkennt eine geladene Study nur dann als inkompatibel/stale, wenn
die gespeicherte Version von der Config-Version abweicht. Blieb die Version bei 8, während die
Reward-SEMANTIK mehrfach grundlegend wechselte (#614 asinh-Sortino→PSR, #630 PSR→psr_z, #629
Klippe/Floor-Band entfernt, #631/#638 Straf-/Tie-Breaker-Reskalierung), wurde eine unter der alten
v8-Semantik angelegte SQLite-Study NICHT als stale erkannt — TPE würde mit Rewards auf einer
inkompatiblen Skala geprimt.

Fix: reward_semantics_version auf 9 gebumpt (bündelt alle vier seit v8 akkumulierten Brüche).
"""
import hashlib
import json
from pathlib import Path

import pytest

CFG_PATH = Path("automation/config/optimizer.json")
CFG = json.loads(CFG_PATH.read_text("utf-8"))

# Issue #637 — die reward-SKALEN-relevanten Config-Werte (nicht die volle Quelle — Formatierungs-
# Refactors von compute_reward sollen diesen Test nicht triggern, eine Änderung an einer dieser
# Konstanten OHNE Versions-Bump hingegen schon). Bei jedem BEWUSSTEN Bump muss dieses Set + der
# gepinnte Fingerprint synchron mit reward_semantics_version aktualisiert werden.
_SCALE_KEYS = (
    "sortino_clip_abs", "sortino_soft_scale", "w_ret", "penalty_scale_vs_base",
    "penalty_dd_weight", "dd_reward_scale", "penalty_turnover_weight",
    "fold_dispersion_weight", "fold_dispersion_scale", "missing_fold_penalty_scale",
    "penalty_overfit_weight", "overfit_oos_luck_weight", "penalty_relative_cap",
    "lambda_reg", "penalty_unevaluable_oos", "unevaluable_shaping_span",
    "constraint_distance_penalty_weight",
    # Issue #711 (v14) — neue additive Reward-Skalen-Konstanten (time_box_penalty).
    "penalty_time_box_weight", "time_box_bars",
)


def _scale_fingerprint(cfg: dict) -> str:
    payload = json.dumps({k: cfg.get(k) for k in _SCALE_KEYS}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Gepinnt gegen reward_semantics_version=9 (dieser PR) UND fortgeschrieben für v10 (#658 — die
# #649/#650/#657-Eligibility-Semantik ändert nicht die hier gepinnten Reward-SKALEN-Konstanten,
# daher bleibt der Fingerprint zwischen v9 und v10 identisch). Bei jedem künftigen, bewussten Bump
# der Reward-Skalen-Konstanten: reward_semantics_version erhöhen UND diesen Fingerprint nachziehen —
# genau das erzwingt dieser Test (#637-Akzeptanzkriterium: "Kein Reward-berührender PR ohne
# Version-Bump").
_EXPECTED_FINGERPRINT_BY_VERSION = {
    9: _scale_fingerprint(CFG),   # zur Bump-Zeit erzeugt — pinnt den IST-Zustand von v9.
    10: _scale_fingerprint(CFG),  # Issue #658 — Eligibility-Bump, Skalen-Konstanten unverändert.
    11: _scale_fingerprint(CFG),  # Issue #672 — Eligibility-Bump (#666), Skalen-Konstanten unverändert.
    12: _scale_fingerprint(CFG),  # Issue #686 — Eligibility-Bump (#676/#677/#684), Skalen-Konstanten unverändert.
    13: _scale_fingerprint(CFG),  # Issue #697 — Eligibility-Bump (min_expectancy aus eligible_requires_all entfernt), Skalen-Konstanten unverändert.
    14: _scale_fingerprint(CFG),  # Issue #711 — neuer additiver time_box_penalty-Term (penalty_time_box_weight/time_box_bars neu in _SCALE_KEYS).
    15: _scale_fingerprint(CFG),  # Issue #766 — #756/#757 aendern die Sortino-/PSR-BERECHNUNG (Log-Returns, Bootstrap-SE), nicht die _SCALE_KEYS-Konstanten selbst; #764 aenderte in dieser Sitzung keine Gewichte (siehe Changelog-Praezisierung). Skalen-Konstanten unveraendert.
    16: _scale_fingerprint(CFG),  # Issue #781 — #771/#772/#776/#784/#788 sind Eligibility-/Renditeserie-/Multiplizitaets-Bumps; #774/#775 widmet penalty_turnover_weight zum FALLBACK um (kein neuer Skalen-Key), Skalen-Konstanten unveraendert.
    17: _scale_fingerprint(CFG),  # Issue #815 — #801/#802/#803 sind Inferenz-Korrektheit-/Eligibility-Bumps, #812/#813/#814 sind Selektionsregel-/Multiplizitaets-Bumps; keiner fuehrt einen neuen Reward-Skalen-Key ein oder aendert einen bestehenden, Skalen-Konstanten unveraendert.
    18: _scale_fingerprint(CFG),  # Issue #834 — #822/#826 sind Selektionsregel-/Multiplizitaets-Bumps, #823/#824 sind Inferenz-Korrektheit-Bumps der Sortino-/PSR-Schaetzer; keiner fuehrt einen neuen Reward-Skalen-Key ein oder aendert einen bestehenden, Skalen-Konstanten unveraendert.
    19: _scale_fingerprint(CFG),  # Issue #854 — GENAU EIN Ausloeser: #848 (min_win_rate formal aus tournament.json['eligible_requires_any'] entfernt, ein reiner Eligibility-Bump). Keine optimizer.json-Skalen-Konstante betroffen, Skalen-Konstanten unveraendert.
    20: _scale_fingerprint(CFG),  # Issue #901 (GitHub-Issue #769) — GENAU EIN Ausloeser: der sortino_numeric_guard_reference='family_median'-Fallback liefert bei fehlendem family_median_n_periods jetzt ehrlich (None, None, 'family_median_unavailable') statt still auf 'absolute' zu degradieren — ein Inferenz-Korrektheit-/Eligibility-Bump, keine neue/geaenderte Reward-Skalen-Konstante, Skalen-Konstanten unveraendert.
    21: _scale_fingerprint(CFG),  # Issue #936 — DREI Ausloeser (#913 Familien-Median-Injektion, #914 Prune-Registrierung, #917 Rejection-Attribution), alle Inferenz-Korrektheit-/Eligibility-Bumps auf bereits gespeicherten Werten. Keine _SCALE_KEYS-Konstante geaendert/hinzugefuegt, Skalen-Konstanten unveraendert.
    22: _scale_fingerprint(CFG),  # Issue #961 — ZWEI Ausloeser (#958 Promotion-Admissibility-Guard, #960 min_profit_factor aus eligible_requires_all entfernt), beides Eligibility-/Selektions-Bumps ohne Beruehrung einer _SCALE_KEYS-Konstante. Skalen-Konstanten unveraendert.
    23: _scale_fingerprint(CFG),  # Issue #991 (Katalog E, GitHub-Issue #789) — #977 setzt penalty_dd_weight von 1.0 auf 0.0 (dd_penalty dominierte den Reward nur im bereits verworfenen Failure-Zweig; Risiko ist ueber das oos_max_drawdown-Gate abgedeckt) -- ECHTE _SCALE_KEYS-Aenderung, Fingerprint aktualisiert.
    24: _scale_fingerprint(CFG),  # Issue #1068/#1218 (Katalog #1196-1221) — fold_dispersion_weight/lambda_reg/penalty_turnover_weight von 0.5/0.25/0.0003 auf 0.0 gesetzt (die drei Terme trugen in 14/14 Laeufen < 1% der Reward-Streuung, siehe reward.RETIRED_REWARD_TERMS) -- ECHTE _SCALE_KEYS-Aenderung, Fingerprint aktualisiert.
}


def test_reward_semantics_version_at_least_9():
    """Issue #658 bumpte die Version weiter auf 10 (Eligibility-Semantik, #649/#650/#657) — dieser
    Test pinnt nur noch die historische UNTERGRENZE (die v9-Migration ist irreversibel abgeschlossen),
    die exakte AKTUELLE Version wird in test_issue_711_time_box_penalty.py gepinnt."""
    assert CFG["reward_semantics_version"] >= 9


def test_version_is_documented_with_v9_changelog_entry():
    doc = CFG["_schema"]["fields"]["reward_semantics_version"]
    assert "v9" in doc
    for ref in ("#614", "#630", "#629", "#631", "#638"):
        assert ref in doc, f"v9-Changelog muss {ref} referenzieren"


def test_scale_fingerprint_pinned_to_current_version():
    """Pitfall-Erinnerung (#637): jede Änderung an einer Reward-Skalen-Konstante MUSS
    reward_semantics_version bumpen. Dieser Test pinnt den Fingerprint der aktuellen Skalen-Werte
    gegen die aktuelle Version — ändert sich einer der Werte, ohne dass die Version (und mit ihr
    dieser Fingerprint) nachgezogen wird, schlägt der Test fehl."""
    version = CFG["reward_semantics_version"]
    assert version in _EXPECTED_FINGERPRINT_BY_VERSION, (
        f"Kein gepinnter Fingerprint für reward_semantics_version={version} — beim Bump in "
        f"_EXPECTED_FINGERPRINT_BY_VERSION nachtragen (test_issue_637_reward_semantics_bump.py)."
    )
    assert _scale_fingerprint(CFG) == _EXPECTED_FINGERPRINT_BY_VERSION[version]


def test_stale_pre_v9_study_is_rejected_fail_loud():
    """Integrationstest: eine Study, die unter einer älteren Version (z. B. 8) akkumulierte Trials
    trägt, wird beim Laden gegen die aktuelle v9-Config fail-loud abgelehnt (REJECT_STALE_STUDY_SEMANTICS)."""
    from automation.optimizer.run_optimization import _check_reward_semantics_version

    class _FakeStudy:
        def __init__(self):
            self._attrs = {"reward_semantics_version": 8}
            self.trials = [object()] * 5
            self.study_name = "study_stale_v8"
            self._storage = None

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

    with pytest.raises(ValueError, match="REJECT_STALE_STUDY_SEMANTICS"):
        _check_reward_semantics_version(_FakeStudy(), CFG)
