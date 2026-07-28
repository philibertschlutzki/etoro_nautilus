import hashlib
import json
import math
import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.optimizer.parsing import TournamentMetrics


def _softplus(z: float) -> float:
    """``ln(1 + e^z)`` — überall streng monoton, C∞, numerisch stabil (kein Overflow für großes z).

    Issue #560 — glatte, streng monotone Abbildung des Rest-Gaps zum Return-Gate im
    return-verankerten Failure-Reward. Für ``z ≫ 0`` ≈ ``z``, für ``z ≪ 0`` ≈ ``e^z`` → 0.
    """
    if z > 30.0:
        return z
    if z < -30.0:
        return math.exp(z)
    return math.log1p(math.exp(z))


def _apply_soft_scale(value: float, scale: float | None) -> float:
    """Wendelt die weiche asinh-Kompression an, wenn eine Scale vorliegt."""
    if scale is not None and scale > 0.0:
        c = float(scale)
        return c * math.asinh(float(value) / c)
    return float(value)


def _penalty_scale_vs_base(weights: dict) -> float:
    """Issue #631 — globaler Skalierungsfaktor, der ALLE additiven Straf-Terme (dd_penalty,
    turnover_penalty, fold_dispersion_penalty, param_pen) gegen die REALISIERTE Streuung der
    aktuellen Reward-Base kalibriert (``psr_z`` seit #630). Die Strafterme wurden historisch gegen
    die asinh-Sortino-Base (Skala ~1–15) kalibriert; seit #614/#630 ist die Base ``psr_z`` mit einer
    DEUTLICH engeren realisierten Eligible-Kohorten-Streuung (σ ≈ 0.05–0.07, hergeleitet aus der
    #614-Referenzrechnung σ(psr)=0.017 über die Delta-Methode dz≈dPSR/φ(z)). Ohne Rekalibrierung
    dominieren die Strafterme (z. B. dd_penalty σ≈0.077, das 4,5-Fache der Base-Streuung) das Ranking,
    obwohl sie nur Korrekturterme sein sollen. Ein Strafterm soll die Base-ORDNUNG modifizieren, nicht
    überstimmen: Zielgrösse ``σ(penalty_term) ≲ 0.25·σ(base)``. Fehlt der Key ⇒ 1.0 (Legacy,
    bit-identisch, Zero-Hardcoding) — siehe ``assert_penalty_scale_calibrated`` für die fail-loud
    Kalibrier-Prüfung, die genau diese Regression erkennt."""
    v = weights.get("penalty_scale_vs_base")
    return float(v) if v is not None else 1.0


def _dd_penalty(m: "TournamentMetrics", weights: dict, risk_dd_cap: float | None) -> float:
    """Issue #578/#597 — progressive Drawdown-Strafe ``penalty_dd_weight·(oos_max_drawdown/scale)^2``.

    Issue #597 — die Normierungsskala ist ``dd_reward_scale`` (die realisierte Risiko-Skala,
    ENTKOPPELT vom Gate-Cap ``max_drawdown``). Grund: ``oos_max_drawdown`` ist portfolio-relativ (auf
    ``start_capital``), die Strategie setzt aber nur einen Bruchteil ein ⇒ realer DD 0.6–2.4 %; gegen
    den 30 %-Gate-Cap normiert war ``dd_penalty ≈ 0.004`` (vier Größenordnungen zu klein, struktureller
    Blindgänger). Auf ``dd_reward_scale ≈ 0.03`` normiert liegt der Term im Bereich der übrigen
    Strafterme. Fehlt ``dd_reward_scale`` ⇒ Fallback auf den Gate-Cap (Legacy, bit-identisch).

    Issue #631 — zusätzlich mit ``penalty_scale_vs_base`` gegen die realisierte Base-Streuung
    (``psr_z`` seit #630) rekalibriert (siehe ``_penalty_scale_vs_base``). Fehlt der Key ⇒ Faktor 1.0
    (Legacy, bit-identisch)."""
    scale = weights.get("dd_reward_scale")
    if scale is None:
        scale = risk_dd_cap
    if scale and float(scale) > 0.0:
        raw = float(weights["penalty_dd_weight"]) * ((m.oos_max_drawdown / float(scale)) ** 2)
    else:
        raw = 0.0
    return raw * _penalty_scale_vs_base(weights)


def _time_box_penalty(m: "TournamentMetrics", weights: dict) -> float:
    """Issue #711 (Req-04, chirurgisch) — additiver Deadline-Näherungs-Term
    ``penalty_time_box_weight · (oos_median_bars_held / time_box_bars)² · penalty_scale_vs_base``.

    Reiner Shaping-/Tie-Breaking-Term im eligiblen Ast (NICHT in ``eligible_requires_all``, NICHT in
    der Deflations-/DSR-Kette — Lektion #629/#649: ein in die Gates gemischter Term tötet still
    Kandidaten). ``base`` bleibt ``psr_z`` (#614/#630); dies ist NUR der zusätzliche additive Term aus
    #708-Req-04 (``Obj = E[R]/σ_R⁻ − β·(t_hold/24)²``), ohne die getestete Base zu ersetzen.

    ``time_box_bars`` (Default 24, GR-01 #714) ist die Normierungs-Deadline in Bars — konfigurierbar
    (Zero-Hardcoding), aber an dieselbe 24-Bar-Zeitbox gebunden wie der harte Bar-Zähler-Exit.
    Der konvexe Kern ``(t/T_max)²`` bestraft Deadline-Nähe progressiv und lässt kurze Haltedauern
    nahezu ungestraft. Wie ``_dd_penalty``/``_penalty_scale_vs_base`` gegen die realisierte
    ``psr_z``-Base-Streuung skaliert (#631) — sonst wäre β entweder wirkungslos (zu klein) oder
    dominierte das Ranking (zu gross).

    Fehlt ``m.oos_median_bars_held`` (None — Legacy-JSON/Fixture ohne #710-Feld, oder ein Trial ohne
    OOS-Trades) ⇒ 0.0 (kein erfundener Wert). Fehlt ``penalty_time_box_weight`` ⇒ 0.0 (Default,
    bit-identisch/Legacy — siehe optimizer.json)."""
    weight = float(weights.get("penalty_time_box_weight", 0.0) or 0.0)
    if weight == 0.0 or m.oos_median_bars_held is None:
        return 0.0
    time_box_bars = float(weights.get("time_box_bars", 24.0) or 24.0)
    if time_box_bars <= 0.0:
        return 0.0
    t_norm = float(m.oos_median_bars_held) / time_box_bars
    raw = weight * (t_norm ** 2)
    return raw * _penalty_scale_vs_base(weights)


_oos_min_trades_cache: int | None = None
_tournament_cfg_cache: dict | None = None


def _read_tournament_cfg() -> dict:
    global _tournament_cfg_cache
    if _tournament_cfg_cache is not None:
        return _tournament_cfg_cache

    try:
        from automation.optimizer.trial_config import config_dir

        cfg_path = config_dir() / "tournament.json"
        if not cfg_path.exists():
            _tournament_cfg_cache = {}
            return {}
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            _tournament_cfg_cache = data
            return data
    except OSError:
        _tournament_cfg_cache = {}
        return {}
    except ValueError:
        _tournament_cfg_cache = {}
        return {}


def _read_oos_min_trades() -> int:
    global _oos_min_trades_cache
    if _oos_min_trades_cache is not None:
        return _oos_min_trades_cache

    tournament_cfg = _read_tournament_cfg()
    _oos_min_trades_cache = tournament_cfg.get("oos_min_trades", 3)
    return _oos_min_trades_cache


def _cfg_value(weights: dict, tournament_cfg: dict | None, key: str, default=None):
    """Issue #452 — Constraint-Schwelle aufloesen: erst aus ``weights`` (optimizer.json/DI),
    dann aus der ``tournament.json``-Config, sonst ``default``. So bleibt die Distanz-Penalty
    rein deklarativ (Zero-Hardcoding, HI-6) und an dieselben Gates gebunden, die ueber
    ``oos_eligible`` entscheiden."""
    if key in weights:
        return weights[key]
    if tournament_cfg and key in tournament_cfg:
        return tournament_cfg[key]
    return default


def _shortfall_distance(
    actual: float, target: float | None, scale: float | None = None
) -> float:
    """Lineare, auf den Target (oder Scale) normierte Unterschreitungs-Distanz ∈ [0, ∞).
    0.0, wenn ``actual >= target`` (oder kein/nicht-positiver Target).
    Issue #505: Lineare statt quadratische Distanz zur Vermeidung von Term-Dominanz.
    Issue #467: `scale` erlaubt die Entkopplung der Penalty-Skalierung vom Gate-Threshold.
    """
    if target is None or target <= 0.0:
        return 0.0
    denom = float(scale) if scale is not None else float(target)
    if denom <= 0.0:
        raise ValueError("scale/target must be strictly positive")
    return max(0.0, float(target) - float(actual)) / denom


def _excess_distance(actual: float, cap: float | None) -> float:
    """Lineare, auf den Cap normierte Ueberschreitungs-Distanz (z. B. Drawdown > max).
    0.0, wenn ``actual <= cap`` (oder kein/nicht-positiver Cap)."""
    if cap is None or cap <= 0.0:
        return 0.0
    return max(0.0, float(actual) - float(cap)) / float(cap)


# Issue #593 — die EINZIGEN Klauseln, für die _any_condition_distance einen korrespondierenden
# Distanz-Term besitzt. Gate (eligible_requires_any) und Reward-Distanz MÜSSEN dieselbe Klauselmenge
# sehen (#549-Parität) — deshalb ist diese Menge die Single Source of Truth für die Parity-Assertion.
_ANY_CONDITION_CLAUSES = frozenset({"min_sortino", "min_profit_factor", "min_win_rate"})


def assert_any_condition_parity(tournament_cfg: dict | None) -> None:
    """Issue #593 — fail-loud beim Config-Load: JEDE Klausel in ``eligible_requires_any`` MUSS einen
    korrespondierenden Term in ``_any_condition_distance`` haben (sonst sehen Gate und Reward
    unterschiedliche Klauselmengen — genau die #549-Paritätsverletzung, die einen Optimierer eine im
    Reward unsichtbare Gate-Hürde ausnutzen lässt). Eine unbekannte Klausel ⇒ ``ValueError``."""
    any_clauses = set((tournament_cfg or {}).get("eligible_requires_any", []) or [])
    unknown = any_clauses - _ANY_CONDITION_CLAUSES
    if unknown:
        raise ValueError(
            f"eligible_requires_any enthält Klausel(n) ohne korrespondierenden Term in "
            f"_any_condition_distance: {sorted(unknown)}. Gate und Reward müssen dieselbe "
            f"Klauselmenge sehen (#549/#593-Parität). Erlaubt: {sorted(_ANY_CONDITION_CLAUSES)}."
        )


# Issue #633 — dokumentiertes Kalibrier-Fixture: die empirisch beobachtete OOS-Win-Rate-Verteilung
# über 336 Trials (Trend-/Breakout-Strategien auf 1h-Bars, asymmetrischer Payoff — hoher Profit-
# Factor bei niedriger Trefferquote, z. B. PF 3,80 bei 13 % Win-Rate). Beobachtetes Maximum 0,197.
# Rein deklarativ, KEIN I/O, KEIN Zufall — Referenz für ``check_any_arm_reachability``.
_CALIBRATION_FIXTURE_WIN_RATES = (
    0.05, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.13, 0.14,
    0.14, 0.15, 0.15, 0.16, 0.16, 0.17, 0.17, 0.18, 0.18, 0.19, 0.197,
)
# clause -> (Kalibrier-Fixture-Stichprobe, Schwellen-Key in tournament.json). Nur Klauseln mit
# dokumentierter Referenzverteilung werden geprüft; min_sortino/min_profit_factor haben (noch) keine
# #633-Kalibrierung ⇒ kein Urteil (kein False-Positive durch Rate).
_ANY_ARM_CALIBRATION = {
    "min_win_rate": (_CALIBRATION_FIXTURE_WIN_RATES, "oos_min_win_rate"),
}


def check_any_arm_reachability(tournament_cfg: dict | None) -> list[str]:
    """Issue #633 — warnt (WARNING-Log, KEIN Abbruch — Zero-Hardcoding-Diagnose statt Hard-Fail, weil
    die wahre Erreichbarkeit strategie-/symbolabhängig ist), wenn eine ``eligible_requires_any``-
    Schwelle STRUKTURELL über dem p99 der dokumentierten Kalibrier-Fixture-Verteilung liegt. Ein
    solcher OR-Arm ist faktisch unerreichbar (#633-Root-Cause: ``oos_min_win_rate=0.25`` bei einem
    über 336 Trials beobachteten Maximum von 0.197) und die ANY-Klausel kollabiert lautlos auf die
    übrigen Arme — TPE optimiert dann unbemerkt gegen ein engeres Gate als konfiguriert scheint.
    Rückgabe: die Namen der betroffenen Klauseln (für Tests / Aufrufer)."""
    import logging

    any_clauses = (tournament_cfg or {}).get("eligible_requires_any", []) or []
    unreachable = []
    for clause in any_clauses:
        calib = _ANY_ARM_CALIBRATION.get(clause)
        if calib is None:
            continue
        fixture, threshold_key = calib
        threshold = (tournament_cfg or {}).get(threshold_key)
        if threshold is None:
            continue
        sorted_vals = sorted(fixture)
        p99_idx = max(0, min(len(sorted_vals) - 1, round(0.99 * (len(sorted_vals) - 1))))
        p99 = sorted_vals[p99_idx]
        if float(threshold) > p99:
            unreachable.append(clause)
            logging.getLogger("optimizer").warning(
                "[#633] eligible_requires_any-Klausel '%s': Schwelle %.4f > p99(Kalibrier-Fixture)=%.4f "
                "— der OR-Arm ist strukturell unerreichbar und kollabiert die ANY-Klausel lautlos auf "
                "die übrigen Arme. tournament.json['%s'] gegen die realisierte Metrik-Verteilung "
                "nachschärfen.", clause, float(threshold), p99, threshold_key,
            )
    return unreachable


# Issue #660 — clause -> Threshold-Key, für die LIVE-Variante von check_any_arm_reachability. Die
# statische #633-Fixture (_ANY_ARM_CALIBRATION) ist ein CROSS-STRATEGY-Referenzwert (p99=0.197 über
# 336 Trials mehrerer Trend-/Breakout-Strategien) — für ein SPEZIFISCHES Symbol/Tier kann die real
# beobachtete Verteilung strukturell darunter liegen (TSLA.ETORO Hourly-Tier: max ~0.11), ohne dass
# die statische, config-load-time Prüfung (die noch keine Trials kennt) das je erkennen könnte.
_ANY_ARM_LIVE_THRESHOLD_KEYS = {"min_win_rate": "oos_min_win_rate"}


def check_any_arm_reachability_live(tournament_cfg: dict | None,
                                    observed_values: dict[str, list] | None, *,
                                    n_evaluated: int | None = None) -> list[str]:
    """Issue #660 — wie ``check_any_arm_reachability`` (#633), aber gegen die TATSÄCHLICH in EINER
    KONKRETEN Study beobachtete empirische Verteilung (``observed_values``, z. B.
    ``{"min_win_rate": [0.05, 0.08, ...]}`` aus den Trial-User-Attrs), NICHT das statische,
    cross-strategy Kalibrier-Fixture aus #633. Root-Cause #660: ``oos_min_win_rate=0.15`` liegt
    UNTER dem #633-Fixture-p99 (0.197) und wird von ``check_any_arm_reachability`` daher als
    'erreichbar' eingestuft — obwohl die für EIN SPEZIFISCHES Symbol/Tier (z. B. TSLA.ETORO Hourly)
    tatsächlich beobachtete OOS-Win-Rate nie über ~0.11 hinauskam. Der OR-Arm kollabiert für DIESEN
    Lauf lautlos auf ``min_profit_factor``, ohne dass die #633-Prüfung (die noch keine Trials kennt)
    dies je erkennen könnte. Aufgerufen NACH Studienabschluss (``_emit_study_summary``).

    Issue #759 — Mindest-Stichprobenumfang jetzt ``any_arm_min_observations`` (Default 10 ECHTE
    Beobachtungen, vorher hartcodiert 5): darunter keine Diagnose (kein Urteil auf zu wenig Daten).
    ``observed_values`` darf seit #759 KEINE Missing-Data-Sentinels mehr enthalten (die Parsing-
    Schicht liefert ``None`` durch statt auf 0.0 zu kollabieren, siehe ``parsing.TournamentMetrics.
    oos_win_rate``) — ``samples`` filtert ``None`` ohnehin bereits (defensiv). Zusätzlich: hat die
    Study ``n_evaluated == 0`` (kein einziger ausgewerteter Trial), ist JEDE Reachability-Aussage
    inhaltsleer — sofortiger No-Op, unabhängig von der Sample-Zahl in ``observed_values``.
    Rückgabe: die Namen der betroffenen Klauseln."""
    import logging

    if n_evaluated is not None and n_evaluated <= 0:
        return []

    min_observations = int((tournament_cfg or {}).get("any_arm_min_observations", 10))
    any_clauses = (tournament_cfg or {}).get("eligible_requires_any", []) or []
    unreachable = []
    for clause in any_clauses:
        threshold_key = _ANY_ARM_LIVE_THRESHOLD_KEYS.get(clause)
        if threshold_key is None:
            continue
        threshold = (tournament_cfg or {}).get(threshold_key)
        if threshold is None:
            continue
        samples = [float(v) for v in (observed_values or {}).get(clause, []) if v is not None]
        if len(samples) < min_observations:
            continue
        sorted_vals = sorted(samples)
        p99_idx = max(0, min(len(sorted_vals) - 1, round(0.99 * (len(sorted_vals) - 1))))
        p99 = sorted_vals[p99_idx]
        if float(threshold) > p99:
            unreachable.append(clause)
            logging.getLogger("optimizer").warning(
                "[#660] eligible_requires_any-Klausel '%s': Schwelle %.4f > beobachtetes p99=%.4f "
                "DIESER Study (%d Trials) — der OR-Arm ist für DIESES Symbol/DIESE Strategie "
                "strukturell unerreichbar und kollabiert lautlos auf die übrigen Arme, obwohl das "
                "globale #633-Kalibrier-Fixture die Schwelle als erreichbar einstuft. "
                "tournament.json['%s'] symbol-spezifisch nachschärfen (p99 der empirischen "
                "Symbol-Verteilung) oder den OR-Arm bewusst auf die übrigen Arme reduzieren.",
                clause, float(threshold), p99, len(samples), threshold_key,
            )
    return unreachable


# Issue #668 — gültige Werte für tournament.json['any_arm_unreachable_policy']. 'warn' (Default,
# fehlt der Key) ist BIT-IDENTISCH zum Pre-#668-Verhalten (nur die #660-Live-Warnung, keine
# Verhaltensänderung). Ein unbekannter Wert bricht fail-loud ab (analog #659
# promotion_correction_mode) statt einer möglicherweise falsch geschriebenen Policy-Zeichenkette
# still zu ignorieren.
_ANY_ARM_UNREACHABLE_POLICIES = frozenset({"warn", "drop_arm", "recalibrate"})


def resolve_any_arm_policy(tournament_cfg: dict | None,
                           observed_values: dict[str, list] | None, *,
                           n_evaluated: int | None = None) -> dict:
    """Issue #668 — hebt ``check_any_arm_reachability_live`` (#660, reine WARNUNG) auf eine
    KONFIGURIERTE Policy (``tournament.json['any_arm_unreachable_policy']``).

    Root-Cause: #660 lieferte nur die Live-Diagnose (WARNING-Log), aber die Schwelle blieb eine
    globale Zahl, die per (Symbol, Strategie) strukturell unerreichbar sein kann — der OR-Arm
    kollabiert dann STILL auf die übrigen Arme (z. B. reines ``min_profit_factor``-Gate), ohne dass
    dies je EXPLIZIT entschieden/telemetriert wurde.

    ``'warn'`` (Default) — bit-identisch zu #660: reine Diagnose, keine Verhaltensänderung.
    ``'drop_arm'`` — jede strukturell unerreichbare Klausel (``check_any_arm_reachability_live``)
    wird EXPLIZIT als gedroppt zurückgegeben (``dropped_clauses``) — die Disjunktion wird bewusst
    auf die übrigen Arme reduziert, statt still zu kollabieren. Issue #812 — SEIT #812 DEFAULT
    (vorher ``'recalibrate'``): die Regel bleibt damit über ALLE Studies identisch ("bestehe PF,
    oder bestehe WR, sofern WR auf diesem Paar überhaupt erreichbar ist"), Voraussetzung für eine
    gültige familienweite DSR-Multiplizitätskorrektur (Pitfall #248) — ``'recalibrate'`` macht die
    Selektionsprozedur STUDY-abhängig (siehe unten).
    ``'recalibrate'`` — die Schwelle wird SYMBOL-spezifisch auf das rohe p99 der beobachteten
    Verteilung neu gesetzt, sodass der Arm ein echter Filter bleibt statt strukturell unerreichbar
    zu sein. Issue #812 — bleibt NUR als Option für explizite Kalibrierläufe erhalten: da die
    effektive Schwelle dann PRO STUDY unterschiedlich ist, ist in diesem Modus KEIN DSR-Vergleich
    über Studies hinweg zulässig (E[max_N] ist aus N allein nicht mehr berechenbar, wenn N
    Kandidaten aus unterschiedlich strengen Selektionsprozeduren mischt) — eine WARNING macht das
    bei jeder tatsächlichen Rekalibrierung explizit. Der globale Floor
    (``min_win_rate_recalibration_floor``) entfiel mit dem Default-Wechsel (toter Schlüssel, seit
    #812 entfernt) — das rohe p99 ist bereits durch ``any_arm_min_observations`` (#759) gegen
    Kleinstichproben abgesichert.

    Issue #759 — ``any_arm_decision`` macht explizit, WARUM keine Schwelle geändert wurde: unter
    ``any_arm_min_observations`` ECHTEN Beobachtungen (oder bei ``n_evaluated == 0``) trifft diese
    Funktion KEIN Urteil (``'insufficient_data'``) statt eine Schwelle aus einer Verteilung
    abzuleiten, die (teils) aus fehlenden Werten besteht — Root-Cause des #759-Katalogfundes
    (``any_arm_unreachable_policy='recalibrate'`` rekalibrierte auf Missing-Data-Sentinels, siehe
    ``parsing.TournamentMetrics.oos_win_rate``-Docstring).

    Rückgabe: ``{'policy': str, 'dropped_clauses': [...], 'recalibrated_thresholds': {...},
    'any_arm_decision': str | None}``. Beide Listen/Dicts bleiben leer, wenn kein Arm unerreichbar
    ist (oder Policy='warn'). Ein unbekannter Policy-Wert bricht FAIL-LOUD ab (``ValueError``,
    analog ``confirm.py['promotion_correction_mode']``, #659)."""
    tournament_cfg = tournament_cfg or {}
    policy = tournament_cfg.get("any_arm_unreachable_policy", "warn")
    if policy not in _ANY_ARM_UNREACHABLE_POLICIES:
        raise ValueError(
            f"tournament.json: any_arm_unreachable_policy={policy!r} unbekannt. "
            f"Erlaubt: {sorted(_ANY_ARM_UNREACHABLE_POLICIES)}."
        )
    result = {"policy": policy, "dropped_clauses": [], "recalibrated_thresholds": {},
             "any_arm_decision": None}
    if policy == "warn":
        return result

    if n_evaluated is not None and n_evaluated <= 0:
        result["any_arm_decision"] = "insufficient_data"
        return result

    min_observations = int(tournament_cfg.get("any_arm_min_observations", 10))
    any_clauses = tournament_cfg.get("eligible_requires_any", []) or []
    has_checkable_clause = any(
        len([v for v in (observed_values or {}).get(c, []) if v is not None]) >= min_observations
        for c in any_clauses
    )
    if not has_checkable_clause:
        result["any_arm_decision"] = "insufficient_data"
        return result

    unreachable = check_any_arm_reachability_live(
        tournament_cfg, observed_values, n_evaluated=n_evaluated)
    if not unreachable:
        return result

    for clause in unreachable:
        threshold_key = _ANY_ARM_LIVE_THRESHOLD_KEYS.get(clause)
        if threshold_key is None:
            continue
        if policy == "drop_arm":
            result["dropped_clauses"].append(clause)
        else:  # 'recalibrate'
            samples = sorted(float(v) for v in (observed_values or {}).get(clause, []) if v is not None)
            if not samples:
                continue
            p99_idx = max(0, min(len(samples) - 1, round(0.99 * (len(samples) - 1))))
            result["recalibrated_thresholds"][threshold_key] = samples[p99_idx]
    if result["recalibrated_thresholds"]:
        import logging
        logging.getLogger("optimizer").warning(
            "[#812] any_arm_unreachable_policy='recalibrate' hat %s study-spezifisch rekalibriert "
            "— die effektive Selektionsregel dieser Study weicht damit von Studies ohne "
            "Rekalibrierung ab (siehe reward.selection_rule_fingerprint). KEIN DSR-Vergleich ueber "
            "Studies hinweg zulaessig, solange dieser Modus aktiv ist (E[max_N] setzt eine ueber "
            "die Familie konstante Selektionsprozedur voraus, Pitfall #248).",
            sorted(result["recalibrated_thresholds"]),
        )
    result["any_arm_decision"] = "dropped" if policy == "drop_arm" else "recalibrated"
    return result


def _effective_gate_thresholds(tournament_cfg: dict | None,
                               any_arm_policy_decision: dict | None = None) -> dict:
    """Issue #812 — das EFFEKTIV wirksame Schwellen-Dict einer Study: ``eligible_requires_all``-
    Klauseln mit ihren konfigurierten Schwellen, plus ``eligible_requires_any``-Klauseln NACH
    Anwendung der #668-Policy (``resolve_any_arm_policy``) — gedroppte Klauseln (``drop_arm``)
    fehlen, rekalibrierte Klauseln (``recalibrate``) tragen ihre study-spezifische statt der
    Config-Schwelle. Das ist die Grundlage von ``selection_rule_fingerprint``: zwei Studies mit
    demselben Fingerprint haben garantiert dieselbe EFFEKTIVE Selektionsregel angewendet."""
    tournament_cfg = tournament_cfg or {}
    any_arm_policy_decision = any_arm_policy_decision or {}
    dropped = set(any_arm_policy_decision.get("dropped_clauses") or [])
    recalibrated = any_arm_policy_decision.get("recalibrated_thresholds") or {}
    effective: dict = {}
    for k in tournament_cfg.get("eligible_requires_all") or []:
        effective[k] = tournament_cfg.get(k)
    for k in tournament_cfg.get("eligible_requires_any") or []:
        if k in dropped:
            continue
        threshold_key = _ANY_ARM_LIVE_THRESHOLD_KEYS.get(k)
        if threshold_key is not None and threshold_key in recalibrated:
            effective[k] = recalibrated[threshold_key]
        else:
            effective[k] = tournament_cfg.get(k)
    return effective


def selection_rule_fingerprint(tournament_cfg: dict | None,
                               any_arm_policy_decision: dict | None = None) -> str:
    """Issue #812 — SHA-256-Hexdigest über die EFFEKTIV wirksame Gate-Konfiguration einer Study
    (``_effective_gate_thresholds`` — Schwellen inklusive aller #668-Policy-Anpassungen dieser
    Study). Root-Cause #812: die Deflated Sharpe Ratio korrigiert für das Maximum von ``N``
    Kandidaten, die DERSELBEN Selektionsprozedur unterworfen wurden; ``any_arm_unreachable_
    policy='recalibrate'`` macht die Prozedur je (Strategie, Symbol) verschieden, ohne dass dies
    irgendwo maschinell sichtbar/durchsetzbar war — ``E[max_N]`` ist aus ``N`` allein dann nicht
    mehr berechenbar.

    Zwei Studies mit demselben Fingerprint haben garantiert dieselbe effektive Selektionsregel
    angewendet und dürfen in DERSELBEN Multiplizitäts-Familie gezählt werden; unterschiedliche
    Fingerprints innerhalb eines Symbols (z. B. weil ``drop_arm`` bei einer Study, aber nicht bei
    einer anderen griff) müssen als getrennte Familien geführt werden (siehe
    ``sweep._family_n_from_studies``, ``report._build_report``'s ``selection_rule_families``).
    Deterministisch (``json.dumps(..., sort_keys=True)`` vor dem Hash) — dieselbe effektive
    Konfiguration liefert IMMER denselben Fingerprint, unabhängig von der Dict-Iterationsreihenfolge."""
    effective = _effective_gate_thresholds(tournament_cfg, any_arm_policy_decision)
    payload = json.dumps(effective, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _active_gate_collinearity_keys(tournament_cfg: dict | None) -> list[str]:
    """Issue #760 — leitet die zu prüfenden Gate-Keys ZUR LAUFZEIT aus der AKTIVEN
    ``eligible_requires_all``-Konjunktion ab, statt aus einer eingefrorenen Code-Konstante
    (der #667-Stand, ``_GATE_COLLINEARITY_KEYS``, driftete lautlos von der Config weg: #676/#697
    entfernten Gates aus dem Default, ohne dass die Kollinearitäts-Diagnose es je bemerkt hätte —
    426 ``[#667]``-Warnungen bezogen sich zuletzt auf ``oos_min_expectancy``, das seit #697 gar
    nicht mehr Teil der Konjunktion ist).

    Jeder Konjunktions-Key wird auf seinen ``oos_``-präfigierten ``oos_gate_deltas``-Spaltennamen
    normiert (die Delta-Dicts sind durchgehend präfigiert, ``eligible_requires_all`` schreibt manche
    Klauseln OHNE Präfix — ``min_trades`` statt ``oos_min_trades``, siehe
    ``backtest_runner._canonical_gate_key`` für die spiegelbildliche Normalisierung Richtung
    ``condition_map``). ``'any_condition'`` wird als Proxy für die GESAMTE
    ``eligible_requires_any``-Disjunktion angehängt, sofern die Config überhaupt einen ANY-Arm
    deklariert — order-preserving dedupliziert."""
    tournament_cfg = tournament_cfg or {}
    requires_all = tournament_cfg.get("eligible_requires_all") or []
    keys = [k if k.startswith("oos_") else f"oos_{k}" for k in requires_all]
    if tournament_cfg.get("eligible_requires_any"):
        keys.append("any_condition")
    seen: set = set()
    ordered: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def _spearman_rank_correlation(x: list, y: list) -> float | None:
    """Spearman-Rangkorrelation zweier gleichlanger Sequenzen (Pearson auf den Rängen, Ties über
    den Durchschnittsrang aufgelöst). ``None`` bei < 3 Punkten oder Null-Varianz in einer Sequenz
    (Rangkorrelation nicht definierbar). Rein, deterministisch, kein externer Abhängigkeit (numpy/
    scipy) nötig."""
    n = len(x)
    if n < 3 or n != len(y):
        return None

    def _ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = _ranks(x), _ranks(y)
    mean_rx, mean_ry = sum(rx) / n, sum(ry) / n
    var_x = sum((v - mean_rx) ** 2 for v in rx)
    var_y = sum((v - mean_ry) ** 2 for v in ry)
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    return cov / ((var_x ** 0.5) * (var_y ** 0.5))


def _any_condition_delta_from_gate_deltas(deltas: dict | None) -> float | None:
    """Issue #667 — die ``eligible_requires_any``-Klausel hat KEINEN einzelnen Delta-Eintrag in
    ``oos_gate_deltas`` (sie ist eine Disjunktion mehrerer Arme). Als Kollinearitäts-Proxy dient
    der BESTE (am wenigsten negative) der konfigurierten Arm-Deltas — konsistent mit der
    Gate-Semantik selbst (die ANY-Klausel ist erfüllt, sobald IRGENDEIN Arm besteht)."""
    if not deltas:
        return None
    candidates = [deltas[k] for k in ("oos_min_profit_factor", "oos_min_win_rate", "oos_min_sortino")
                  if deltas.get(k) is not None]
    return max(candidates) if candidates else None


def gate_rank_correlation_matrix(trial_gate_deltas: list, tournament_cfg: dict | None = None) -> dict:
    """Issue #667/#760 — Rang-Korrelationsmatrix (Spearman) der AKTIVEN eligible-Gates (siehe
    ``_active_gate_collinearity_keys`` — zur Laufzeit aus ``tournament_cfg`` abgeleitet, KEINE
    eingefrorene Code-Konstante mehr) über eine Kohorte von Trials, aus deren gestempelten
    ``oos_gate_deltas`` (#554/#668 — pro Trial als User-Attr verfügbar). Nur Trials mit ALLEN
    aktiven Deltas fliessen ein (Zero-Hardcoding: fehlende Gates werden nicht künstlich mit 0
    aufgefüllt, das würde eine falsche Korrelation vortäuschen).

    ``tournament_cfg=None`` (kein ``eligible_requires_all``/``_any`` bekannt) liefert eine leere
    Key-Menge — bewusst KEIN Fallback auf eine geratene Gate-Liste.

    Rückgabe ``{'n_samples': int, 'keys': [...], 'correlations': {(gate_a, gate_b): rho_or_None,
    ...}, 'non_correlable_keys': [...]}``. ``non_correlable_keys`` — Issue #760 — sind aktive Keys,
    die über die GESAMTE Kohorte NIE ein Delta beigetragen haben (keine ``oos_gate_deltas``-Spalte
    in den vorliegenden Trial-Daten) und daher explizit als nicht-korrelierbar telemetriert werden,
    statt still als durchgehend ``None`` in der Matrix zu verschwinden. Rein, deterministisch."""
    keys = _active_gate_collinearity_keys(tournament_cfg)
    series: dict[str, list] = {k: [] for k in keys}
    for deltas in trial_gate_deltas:
        deltas = deltas or {}
        row = {}
        for k in keys:
            row[k] = _any_condition_delta_from_gate_deltas(deltas) if k == "any_condition" else deltas.get(k)
        if any(v is None for v in row.values()):
            continue
        for k, v in row.items():
            series[k].append(float(v))

    correlations = {}
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            correlations[(k1, k2)] = _spearman_rank_correlation(series[k1], series[k2])
    n_samples = len(series[keys[0]]) if keys else 0
    non_correlable_keys = [k for k in keys if not series[k]]
    return {"n_samples": n_samples, "keys": keys, "correlations": correlations,
            "non_correlable_keys": non_correlable_keys}


def _gate_collinearity_threshold(tournament_cfg: dict | None) -> float:
    """Issue #792 — EINE deklarative Schwelle (``tournament.json.gate_collinearity_threshold``,
    Default 0.90 — die schärfere der beiden zuvor koexistierenden Werte 0.90/0.95) für ALLE DREI
    Kollinearitäts-Einstiegspunkte (``assert_gate_collinearity_guard``,
    ``gate_collinearity_redundancy_alarm``, ``assert_eligible_requires_all_not_redundant``).

    Root-Cause #792: die drei Funktionen trugen ZWEI unabhängige Code-Defaults (0.95 vs. 0.90) für
    dieselbe Frage ("sind zwei Gates redundant?") — ein Graubereich (0.90–0.95), in dem der
    [#697]-ERROR feuerte und die [#667]-WARNUNG schwieg, ohne dass die Differenz irgendwo begründet
    war (695 [#667]-Warnungen bei > 0.95 vs. 287 [#697]-Errors bei > 0.90 im selben Lauf)."""
    return float((tournament_cfg or {}).get("gate_collinearity_threshold", 0.90))


def _gate_redundancy_jaccard_threshold(tournament_cfg: dict | None) -> float:
    """Issue #811 — deklarative Jaccard-Schwelle (``tournament.json['gate_redundancy_jaccard']``,
    Default 0.95) fuer "ist die PASS-MENGE zweier Gates praktisch identisch". Bewusst GETRENNT von
    ``_gate_collinearity_threshold`` (#792, Spearman-|ρ| auf den rohen Delta-WERTEN, bleibt reine
    ``assert_gate_collinearity_guard``-Telemetrie) — Root-Cause #811: eine hohe Rangkorrelation der
    Deltas beweist nur, dass zwei Gates derselben latenten "Aktivitaets-Achse" folgen (z. B.
    Trade-Frequenz), NICHT dass sie dieselbe Zulassungs-ENTSCHEIDUNG treffen; disjunkte Pass-Mengen
    bei stark kovariierenden Deltas sind der explizite HEAD-Bug, den #811 behebt."""
    return float((tournament_cfg or {}).get("gate_redundancy_jaccard", 0.95))


def _gate_redundancy_marginal_threshold(tournament_cfg: dict | None) -> float:
    """Issue #811 — deklarative Schwelle (``tournament.json['gate_redundancy_marginal']``,
    Default 0.02) fuer die marginale Passraten-Differenz Δ_B = P(eligible ohne Gate B) − P(eligible
    mit Gate B) (siehe ``gate_marginal_pass_rate_delta``). Ein Gate gilt erst dann als redundanter
    Konsolidierungs-Kandidat, wenn es ZUSAETZLICH zur hohen Jaccard-Uebereinstimmung mit seinem
    hoeher priorisierten Partner auch selbst kaum noch EIGENSTAENDIG filtert — sonst koennte ein
    zufaellig (kleine Kohorte) perfekt uebereinstimmendes Paar vorschnell konsolidiert werden, obwohl
    das niedriger priorisierte Gate tatsaechlich zusaetzliche Trials ausschliesst."""
    return float((tournament_cfg or {}).get("gate_redundancy_marginal", 0.02))


def _gate_pass_flags(trial_gate_deltas: list, keys: list) -> dict:
    """Issue #811 — pro Key die PASS/FAIL-Flagfolge ueber die Kohorte (``delta >= 0.0`` — dieselbe
    Vorzeichenkonvention wie ``oos_gate_deltas`` selbst: bestanden, wenn actual >= threshold).
    ``None`` an Position i, wenn das Delta fuer dieses Gate bei Trial i fehlt (Zero-Hardcoding: kein
    kuenstliches Auffuellen mit False/True, das wuerde eine falsche Passrate vortaeuschen). Parallel
    zu ``trial_gate_deltas`` indiziert — Position i in JEDER Liste entspricht demselben Trial."""
    flags: dict[str, list] = {k: [] for k in keys}
    for deltas in trial_gate_deltas:
        deltas = deltas or {}
        for k in keys:
            v = _any_condition_delta_from_gate_deltas(deltas) if k == "any_condition" else deltas.get(k)
            flags[k].append(None if v is None else (float(v) >= 0.0))
    return flags


def _jaccard_of_pass_sets(flags_a: list, flags_b: list) -> float | None:
    """Issue #811 — Jaccard-Aehnlichkeit |A∩B|/|A∪B| zweier PASS-Mengen (aus ``_gate_pass_flags``),
    ausgewertet nur ueber Trials, an denen BEIDE Gates ein definiertes Flag haben (fehlende Werte
    werden uebersprungen, nicht als False gewertet — sonst wuerde eine Datenluecke als
    Uebereinstimmung/Divergenz fehlinterpretiert). ``None``, wenn es keine gemeinsam definierten
    Trials gibt ODER die Vereinigung der Pass-Mengen leer ist (Jaccard bei leerer Vereinigung ist
    undefiniert, nicht 0 — analog zur Null-Varianz-``None``-Konvention in
    ``_spearman_rank_correlation``)."""
    inter = union = 0
    for a, b in zip(flags_a, flags_b):
        if a is None or b is None:
            continue
        if a or b:
            union += 1
            if a and b:
                inter += 1
    return (inter / union) if union else None


def gate_marginal_pass_rate_delta(trial_gate_deltas: list, tournament_cfg: dict | None,
                                  gate: str) -> float | None:
    """Issue #811 — Δ_gate = P(eligible ueber ALLE aktiven Gates OHNE ``gate``) − P(eligible ueber
    ALLE aktiven Gates MIT ``gate``), ausgewertet ueber die Trials, an denen JEDES aktive Gate
    (``_active_gate_collinearity_keys`` — die VOLLE Konjunktion, nicht nur die Kandidatenmenge:
    Eligibilitaet ist eine Eigenschaft der GESAMTEN Config, strukturelle/protected Gates eingerechnet)
    ein definiertes Delta hat. Da Entfernen eines Gates die eligible Menge nur vergroessern oder
    gleich lassen kann, ist Δ_gate >= 0; ein Wert nahe 0 heisst, ``gate`` schliesst so gut wie KEINE
    zusaetzlichen Trials aus, die nicht ohnehin schon durch die uebrigen aktiven Gates ausgeschlossen
    waeren — das Gate ist redundant im STRENGEN Sinn (nicht nur pass-set-aehnlich zu einem einzelnen
    Partner). ``None``, wenn ``gate`` nicht aktiv ist oder kein Trial alle aktiven Deltas traegt."""
    keys = _active_gate_collinearity_keys(tournament_cfg)
    if gate not in keys:
        return None
    other_keys = [k for k in keys if k != gate]
    flags = _gate_pass_flags(trial_gate_deltas, keys)
    considered = without_b = with_b = 0
    for i in range(len(trial_gate_deltas)):
        gate_flag = flags[gate][i]
        other_flags = [flags[k][i] for k in other_keys]
        if gate_flag is None or any(f is None for f in other_flags):
            continue
        considered += 1
        if all(other_flags):
            without_b += 1
            if gate_flag:
                with_b += 1
    return ((without_b - with_b) / considered) if considered else None


def assert_gate_collinearity_guard(trial_gate_deltas: list, tournament_cfg: dict | None = None, *,
                                   threshold: float | None = None) -> dict:
    """Issue #667/#760/#792 — Redundanz-Wächter: warnt FAIL-LOUD-artig (WARNING-Log, KEIN Abbruch —
    die Kollinearität selbst automatisiert nicht, WELCHES Gate entfernt wird; das bleibt eine
    bewusste, im PR dokumentierte Entscheidung), sobald zwei der AKTIVEN eligible-Gates
    (``tournament_cfg``, siehe ``_active_gate_collinearity_keys``) ``|ρ| > threshold`` über das
    Kalibrier-/Studien-Fixture zeigen. ``threshold=None`` (Default) liest die EINE deklarative
    Schwelle aus ``tournament_cfg`` (#792, ``_gate_collinearity_threshold``) — ein explizit
    übergebener Wert überschreibt sie (Tests/Kalibrierläufe). Rückgabe: die volle
    Korrelationsmatrix (Telemetrie/Tests).

    Issue #811 — diese Funktion bleibt bewusst UNVERÄNDERT gegenüber der Jaccard-Umstellung in
    ``gate_collinearity_redundancy_alarm``: die Spearman-Matrix ist forensisch nützliche #742-
    Report-Telemetrie (zeigt, welche Gates derselben latenten Aktivitäts-Achse folgen), verliert
    aber ihre Alarm-/Konsolidierungs-Funktion an die Pass-Set-Messung."""
    import logging
    if threshold is None:
        threshold = _gate_collinearity_threshold(tournament_cfg)
    result = gate_rank_correlation_matrix(trial_gate_deltas, tournament_cfg)
    for (k1, k2), rho in result["correlations"].items():
        if rho is not None and abs(rho) > threshold:
            logging.getLogger("optimizer").warning(
                "[#667] Gate-Kollinearität: |ρ(%s, %s)|=%.3f > %.2f über %d Trials — die beiden "
                "eligible-Gates kodieren mutmasslich redundant dieselbe latente Achse; "
                "Konsolidierung auf das statistisch schärfste (PSR, fenster-/annualisierungs-"
                "invariant) erwägen.",
                k1, k2, rho, threshold, result["n_samples"],
            )
    return result


def _gate_consolidation_priority(tournament_cfg: dict | None) -> tuple[str, ...]:
    """Issue #810 — ERSETZT die eingefrorene Code-Konstante ``_GATE_CONSOLIDATION_PRIORITY``
    (Root-Cause #810: die Tabelle wurde bei der #776-Konsolidierung nicht mitgezogen — weder
    ``min_trades`` noch ``max_drawdown`` [seit #776 Teil von ``eligible_requires_all``] standen
    darin, beide fielen über ``priority.get(k, 99)`` auf denselben Sentinel, und der Redundanz-
    Alarm empfahl fälschlich die Entfernung von ``max_drawdown``, der harten Risikogrenze, die
    #776 ausdrücklich behalten hat — Pitfall #134 in einer Prioritätstabelle statt einer Kennzahl).

    Deklarativ aus ``tournament.json['gate_consolidation_priority']``, normiert auf ``oos_``-
    präfigierte Delta-Keys (identisch zu ``_active_gate_collinearity_keys`` — ``'any_condition'``
    bleibt dabei UNPRÄFIGIERT, sie ist kein ``oos_gate_deltas``-Spaltenname, sondern der feste
    Proxy-Literal für die gesamte ``eligible_requires_any``-Disjunktion). Reihenfolge = Priorität
    (Index 0 hat die höchste Priorität). Fehlt der Key (oder ist leer) ⇒ ``ValueError``: es gibt
    KEINEN stillen Fallback-Default für eine sicherheitsrelevante Prioritätsordnung."""
    tournament_cfg = tournament_cfg or {}
    raw = tournament_cfg.get("gate_consolidation_priority")
    if not raw:
        raise ValueError(
            "GATE_CONSOLIDATION_PRIORITY_MISSING (#810): tournament.json['gate_consolidation_"
            "priority'] fehlt oder ist leer. Eine Prioritätsordnung ohne diese Tabelle wäre "
            "derselbe stille priority.get(k, 99)-Sentinel-Fehler, den #810 behebt (Pitfall #134)."
        )
    return tuple(_normalize_gate_priority_key(k) for k in raw)


def _normalize_gate_priority_key(k: str) -> str:
    """Issue #810 — dieselbe Normalisierung wie ``_active_gate_collinearity_keys``: jeder Key wird
    ``oos_``-präfigiert, AUSSER er ist bereits präfigiert oder der feste ``'any_condition'``-Proxy
    (kein echter ``oos_gate_deltas``-Spaltenname, darf NIE ``oos_``-präfigiert werden)."""
    if k == "any_condition" or k.startswith("oos_"):
        return k
    return f"oos_{k}"


def _gate_consolidation_protected(tournament_cfg: dict | None) -> frozenset[str]:
    """Issue #810 — Gates, die NIE als ``redundant_candidate`` markiert werden, unabhängig von ρ
    UND unabhängig von ihrer Position in ``_gate_consolidation_priority`` (deklarativ aus
    ``tournament.json['gate_consolidation_protected']``, normiert auf ``oos_``-präfigierte Keys).
    Eine hohe Korrelation zwischen einer strukturellen Vorbedingung/harten Risikogrenze und einem
    Qualitätsgate ist keine Redundanz im #679-Sinn, sondern eine Kategorienverwechslung (#811).
    Fehlt der Key ⇒ leere Menge (keine zusätzliche Härte, rückwärtskompatibel — die
    Prioritätstabelle selbst bleibt trotzdem Pflicht, siehe ``_gate_consolidation_priority``)."""
    tournament_cfg = tournament_cfg or {}
    raw = tournament_cfg.get("gate_consolidation_protected") or []
    return frozenset(_normalize_gate_priority_key(k) for k in raw)


def assert_gate_priority_coverage(tournament_cfg: dict | None) -> None:
    """Issue #810 — FAIL-LOUD beim Start (analog ``assert_any_condition_parity``): JEDES aktive
    Gate (``_active_gate_collinearity_keys`` — die ``eligible_requires_all``-Konjunktion plus
    ``'any_condition'``, falls ``eligible_requires_any`` gesetzt ist) MUSS einen Eintrag in
    ``gate_consolidation_priority`` haben. Ein aktives Gate OHNE Prioritätseintrag ist jetzt ein
    Konfigurationsfehler (``ValueError``), kein stiller ``priority.get(k, 99)``-Sentinel mehr —
    genau der Root-Cause-Mechanismus, der #810 auslöste."""
    active_keys = _active_gate_collinearity_keys(tournament_cfg)
    priority_keys = set(_gate_consolidation_priority(tournament_cfg))
    missing = [k for k in active_keys if k not in priority_keys]
    if missing:
        raise ValueError(
            f"GATE_PRIORITY_COVERAGE_MISSING (#810): aktive(s) Gate(s) ohne Eintrag in "
            f"tournament.json['gate_consolidation_priority']: {missing}. Jedes aktive Gate "
            f"braucht eine explizite, begründete Priorität (Pitfall #134)."
        )


def gate_collinearity_redundancy_alarm(trial_gate_deltas: list, tournament_cfg: dict | None = None,
                                       *, jaccard_threshold: float | None = None,
                                       marginal_threshold: float | None = None) -> dict:
    """Issue #679/#811 — hebt die #667-Kollinearitäts-DIAGNOSE auf einen STRUKTURIERTEN,
    maschinenlesbaren Redundanz-ALARM: ``eligible_requires_all`` addiert redundante Klauseln, deren
    gemeinsame Passrate ≈ der strengsten Einzelklausel entspricht, ohne echte False-Positive-
    Kontrolle beizutragen (das leistet die DSR/PBO-Ebene bereits).

    Root-Cause #811: die Vorgänger-Version massaß Redundanz an der Spearman-Rangkorrelation der
    rohen ``oos_gate_deltas``-WERTE (``gate_rank_correlation_matrix``) — das beweist nur, dass zwei
    Gates derselben latenten "Aktivitäts-Achse" folgen (z. B. Trade-Frequenz treibt sowohl
    ``oos_min_trades`` als auch ``oos_max_drawdown``-Margen), NICHT dass sie dieselbe Zulassungs-
    ENTSCHEIDUNG treffen: 105 Studies zeigten ρ(min_trades, max_drawdown) > 0.9 bei disjunkten
    PASS-Mengen — der HEAD-Bug, den diese Funktion jetzt behebt. Redundanz wird stattdessen an der
    tatsächlichen ENTSCHEIDUNGSMENGE gemessen: Jaccard-Ähnlichkeit der PASS-Mengen (``delta >= 0``,
    ``_jaccard_of_pass_sets``) ZWEIER Gates, UND der marginale eigenständige Beitrag des schwächer
    priorisierten Gates (``gate_marginal_pass_rate_delta`` — schliesst es tatsächlich noch Trials
    aus, die sonst durchgingen?). Beide Kriterien müssen zutreffen: ``Jaccard > jaccard_threshold``
    (Default 0.95) UND ``Δ_candidate < marginal_threshold`` (Default 0.02) — reine Pass-Set-
    Übereinstimmung allein könnte in einer kleinen Kohorte Zufall sein; erst der zusätzlich fast
    inexistente Eigenbeitrag macht die Konsolidierungs-Empfehlung robust.

    Issue #811 — strukturelle Vorbedingungen/harte Risikogrenzen (``gate_consolidation_protected``,
    #810) werden VOR der Messung VOLLSTÄNDIG aus der Kandidatenmatrix entfernt (stärker als die
    #810-Garantie "wird nie Kandidat" — sie tauchen jetzt in KEINEM Paar mehr auf): eine hohe
    Übereinstimmung zwischen einer Evaluierbarkeits-Vorbedingung (``min_trades``) und einem
    Qualitätsgate ist keine Redundanz im #679-Sinn, sondern eine Kategorienverwechslung. Für jedes
    verbleibende Kandidaten-Paar wird — via ``_gate_consolidation_priority`` (#810; das aktive Gate
    ohne Eintrag bricht bereits VOR dieser Schleife über ``assert_gate_priority_coverage`` ab) —
    das NIEDRIGER priorisierte Gate als ``redundant_candidate`` markiert (der marginale Beitrag wird
    IMMER für dieses niedriger priorisierte Gate berechnet — es ist der Konsolidierungs-Kandidat).

    Rückgabe: ``{'n_samples': len(trial_gate_deltas), 'alarms': [{'keep', 'redundant_candidate',
    'jaccard', 'marginal_delta'}], 'redundant_candidates': {candidate: {'jaccard', 'marginal_delta'}}}``
    — ``alarms`` ist leer, wenn kein Paar beide Schwellen überschreitet (kein Alarm, die rohe
    Spearman-Diagnose bleibt über ``assert_gate_collinearity_guard`` als reine Report-Telemetrie,
    #742, verfügbar — sie verliert nur ihre Alarm-Funktion). Diese Funktion selbst KONSOLIDIERT
    NICHTS automatisch (welches Gate tatsächlich aus ``eligible_requires_all`` entfernt wird, bleibt
    eine bewusste, dokumentierte Config-/PR-Entscheidung) — sie macht den Alarm aber STRUKTURIERT
    auswertbar (Tooling/Dashboards/Tests können darauf reagieren), statt nur eine Log-Zeile zu
    emittieren."""
    assert_gate_priority_coverage(tournament_cfg)
    if jaccard_threshold is None:
        jaccard_threshold = _gate_redundancy_jaccard_threshold(tournament_cfg)
    if marginal_threshold is None:
        marginal_threshold = _gate_redundancy_marginal_threshold(tournament_cfg)
    active_keys = _active_gate_collinearity_keys(tournament_cfg)
    protected = _gate_consolidation_protected(tournament_cfg)
    candidate_keys = [k for k in active_keys if k not in protected]
    flags = _gate_pass_flags(trial_gate_deltas, candidate_keys)
    priority = {k: i for i, k in enumerate(_gate_consolidation_priority(tournament_cfg))}
    alarms = []
    redundant_candidates: dict[str, dict] = {}
    for i, k1 in enumerate(candidate_keys):
        for k2 in candidate_keys[i + 1:]:
            jaccard = _jaccard_of_pass_sets(flags[k1], flags[k2])
            if jaccard is None or jaccard <= jaccard_threshold:
                continue
            # Höhere Prioritaet (kleinerer Index) wird behalten; die andere ist der Kandidat.
            keep, candidate = (k1, k2) if priority[k1] < priority[k2] else (k2, k1)
            marginal = gate_marginal_pass_rate_delta(trial_gate_deltas, tournament_cfg, candidate)
            if marginal is None or marginal >= marginal_threshold:
                continue
            alarms.append({"keep": keep, "redundant_candidate": candidate,
                           "jaccard": jaccard, "marginal_delta": marginal})
            prev = redundant_candidates.get(candidate)
            if prev is None or jaccard > prev["jaccard"]:
                redundant_candidates[candidate] = {"jaccard": jaccard, "marginal_delta": marginal}
    return {
        "n_samples": len(trial_gate_deltas),
        "alarms": alarms,
        "redundant_candidates": redundant_candidates,
    }


def assert_eligible_requires_all_not_redundant(trial_gate_deltas: list, eligible_requires_all: list,
                                               tournament_cfg: dict | None = None, *,
                                               jaccard_threshold: float | None = None,
                                               marginal_threshold: float | None = None) -> list[str]:
    """Issue #697 — konsumiert den #679-Redundanz-ALARM (bislang reine Telemetrie OHNE Konsument,
    Root-Cause #697: ``eligible_requires_all`` behielt ``min_expectancy`` UND ``oos_min_psr``
    trotz dokumentierter |ρ|=0.961-Kollinearität, weil nichts die Alarm-Ausgabe gegen die Config
    prüfte). Root-Cause-Fix ist die KONFIG-Konsolidierung selbst (``min_expectancy`` wurde aus
    ``eligible_requires_all`` entfernt, siehe tournament.json); diese Funktion ist der WÄCHTER
    gegen eine künftige Regression — reaktiviert ein Operator versehentlich ein Gate, das die LIVE
    Kohorte (``trial_gate_deltas``, aus ``oos_gate_deltas``-User-Attrs) als redundant gegenüber
    einem höher priorisierten, ebenfalls konjunktiv geforderten Gate ausweist (siehe
    ``_gate_consolidation_priority``, #810), wird das jetzt LAUT (ERROR-Log statt stiller WARNING-
    Diagnose) — statt erneut folgenlos zu bleiben.

    Issue #760 — die Delta-Key→Konjunktions-Key-Rückübersetzung ist jetzt aus ``eligible_requires_
    all`` SELBST abgeleitet (``oos_``-Präfix-Normalisierung, spiegelbildlich zu
    ``_active_gate_collinearity_keys``), statt einer zweiten, separat gepflegten hartcodierten
    Zuordnungstabelle — die driftete sonst genauso lautlos weg wie ``_GATE_COLLINEARITY_KEYS``
    selbst. ``'any_condition'`` hat strukturell KEIN Gegenstück in ``eligible_requires_all`` (sie
    repräsentiert die GESAMTE ``eligible_requires_any``-Disjunktion) und wird daher nie gematcht.

    Rückgabe: sortierte Liste der noch unkonsolidierten Konjunktions-Mitglieder (leer ⇒ die Config
    ist konsistent mit der aktuellen Alarm-Ausgabe — der Regelfall nach #697). Bricht den Lauf NICHT
    ab (welches Gate konsolidiert wird, bleibt eine bewusste, dokumentierte Entscheidung, siehe
    ``gate_collinearity_redundancy_alarm``-Docstring) — macht die Inkonsistenz aber unübersehbar.

    Issue #810 — ``gate_collinearity_redundancy_alarm`` selbst ist jetzt fail-loud, sobald ein
    aktives Gate keinen Prioritätseintrag hat (``assert_gate_priority_coverage``); DIESE Funktion
    bleibt aber bei ihrem dokumentierten "bricht den Lauf NICHT ab"-Vertrag — ein Aufrufer aus
    ``report._study_record``/``confirm.confirm_per_symbol_promotion`` darf an einer unvollständigen
    Diagnose-Config nicht crashen. Eine fehlende Prioritätsabdeckung wird hier daher als "Redundanz
    nicht bestimmbar" behandelt (WARNING-Log, leere Liste) statt propagiert — der eigentliche
    Fail-Loud-Ort für eine fehlende ``gate_consolidation_priority``-Abdeckung ist der
    Sweep-Start-Preflight (``sweep._assert_gate_reward_parity``), nicht jeder einzelne
    Study-/Confirm-Aufruf."""
    import logging
    if tournament_cfg is None:
        tournament_cfg = {"eligible_requires_all": eligible_requires_all}
    try:
        alarm = gate_collinearity_redundancy_alarm(
            trial_gate_deltas, tournament_cfg,
            jaccard_threshold=jaccard_threshold, marginal_threshold=marginal_threshold)
    except ValueError:
        logging.getLogger("optimizer").warning(
            "[#810] gate_collinearity_redundancy_alarm konnte die Prioritätsabdeckung nicht "
            "auflösen (unvollständige tournament_cfg) — Redundanz-Check für diesen Aufruf "
            "übersprungen (non-fatal; der Sweep-Start-Preflight bleibt der Fail-Loud-Ort).",
            exc_info=True,
        )
        return []
    conjunction = set(eligible_requires_all or [])
    # Delta-Key (z. B. "oos_min_expectancy") → Original-Konjunktions-Schreibweise (z. B.
    # "min_expectancy" ODER "oos_min_psr", je nachdem wie eligible_requires_all den Key notiert).
    delta_to_conjunction = {
        (k if k.startswith("oos_") else f"oos_{k}"): k for k in conjunction
    }
    still_redundant = []
    for candidate, info in alarm["redundant_candidates"].items():
        conj_key = delta_to_conjunction.get(candidate)
        if conj_key is None:
            continue
        still_redundant.append(conj_key)
        logging.getLogger("optimizer").error(
            "[#697/#811] eligible_requires_all enthält weiterhin '%s' — gate_collinearity_"
            "redundancy_alarm markiert es als redundant (Jaccard=%.3f, marginaler Eigenbeitrag "
            "Δ=%.3f über %d Trials). Konsolidierung auf das schärfste Gate "
            "(tournament.json['gate_consolidation_priority']) erwägen "
            "(tournament.json['eligible_requires_all']).",
            conj_key, info["jaccard"], info["marginal_delta"], alarm["n_samples"],
        )
    return sorted(still_redundant)


def _any_condition_distance(
    m: "TournamentMetrics", weights: dict, tournament_cfg: dict | None
) -> float:
    """Distanz fuer die ``eligible_requires_any``-Klausel. Issue #593 — spiegelt EXAKT die in
    ``eligible_requires_any`` konfigurierten Klauseln (inkl. ``min_win_rate``, das vorher im Gate,
    aber NICHT in dieser Distanz war). Erfuellt der beste der Quotienten sein Gate (ratio >= 1), ist
    die Distanz 0; sonst linear im Rest-Gap des BESTEN Kandidaten — eine knapp verfehlte ANY-Bedingung
    darf nicht doppelt so hart bestraft werden wie eine knapp verfehlte ALL-Bedingung.
    Issue #467: Strikte Parameter-Isolation. Kein Fallback auf IS-Metriken im OOS-Pfad.
    """
    any_clauses = (tournament_cfg or {}).get("eligible_requires_any", []) if tournament_cfg else []
    ratios = []
    for clause in any_clauses:
        if clause == "min_sortino":
            req = _cfg_value(weights, tournament_cfg, "oos_min_sortino")
            if req and req > 0.0 and m.oos_sortino is not None:
                ratios.append(max(0.0, m.oos_sortino) / float(req))
        elif clause == "min_profit_factor":
            req = _cfg_value(weights, tournament_cfg, "oos_min_profit_factor")
            if req and req > 0.0 and m.oos_profit_factor is not None:
                ratios.append(max(0.0, m.oos_profit_factor) / float(req))
        elif clause == "min_win_rate":
            req = _cfg_value(weights, tournament_cfg, "oos_min_win_rate")
            # Issue #759 — analog zu min_sortino/min_profit_factor oben: eine FEHLENDE (None)
            # win_rate darf diesen OR-Arm nicht mit einer beobachteten Null verwechseln — sie wird
            # aus der ratios-Betrachtung ausgeschlossen (nicht als 0.0 gewertet), statt den
            # Naeherungs-Score fälschlich auf "maximal weit vom Gate entfernt" zu setzen.
            if req and req > 0.0 and m.oos_win_rate is not None:
                ratios.append(max(0.0, m.oos_win_rate) / float(req))

    if not ratios:
        return 0.0
    return max(0.0, 1.0 - min(1.0, max(ratios)))


# Issue #635 — die SECHS Kern-Gates, die _constraint_distance_penalty seit #452/#505/#534 verwendet
# (Reward-Near-Miss-Shaping). Als benannte Konstante, damit _compute_oos_constraints (Sampler-
# Constraint, run_optimization.py) exakt weiss, welche Keys aus ``_normalized_gate_distances`` mit
# dem Reward-Pfad GETEILT sind (Single Source of Truth der Skalen-Auflösung).
_CORE_DISTANCE_KEYS = (
    "oos_min_trades", "oos_min_total_return", "oos_min_expectancy",
    "oos_min_win_rate", "oos_max_drawdown", "any_condition",
)


def _normalized_gate_distances(
    m: "TournamentMetrics",
    weights: dict,
    risk_dd_cap: float | None,
    tournament_cfg: dict | None,
) -> dict:
    """Issue #452/#635 — GETEILTE Scale-Auflösung: liefert jede OOS-Gate-Distanz dimensionslos
    normiert (``_shortfall_distance``/``_excess_distance``, dieselbe Normierung wie überall) als
    benanntes Dict, statt eines aggregierten Skalars. Single Source of Truth für
    ``_constraint_distance_penalty`` (Reward-Near-Miss-Shaping, nutzt nur ``_CORE_DISTANCE_KEYS``)
    UND ``_compute_oos_constraints`` (#612-Sampler-Constraint, run_optimization.py, nutzt ALLE
    Keys inkl. PSR/Excess-Return) — beide MÜSSEN dieselben Skalen sehen, sonst sortiert der Sampler
    infeasible Trials nach einer anderen Rangordnung als der Reward sie bestraft (#635-Root-Cause:
    der Sampler summierte zuvor UN-normierte Rohdeltas, in denen ein grossskaliges Gate wie PSR
    (∈[0,1]) ein kleinskaliges wie Excess-Return (~[0,0.05]) um den Faktor ~19 verschluckte)."""
    # Strikte Isolation (kein impliziter Fallback auf IS)
    req_trades = _cfg_value(weights, tournament_cfg, "oos_min_trades")
    req_return = _cfg_value(weights, tournament_cfg, "oos_min_total_return")
    req_expectancy = _cfg_value(weights, tournament_cfg, "oos_min_expectancy")
    req_win_rate = _cfg_value(weights, tournament_cfg, "oos_min_win_rate")

    if None in (req_trades, req_return, req_expectancy, req_win_rate):
        raise ValueError(
            "Missing strict OOS configuration parameters in tournament.json"
        )

    return_penalty_scale = _cfg_value(weights, tournament_cfg, "return_penalty_scale")
    # Issue #547 — Expectancy-Distanz analog zum Return (#467) vom Gate-Threshold entkoppeln.
    # ``_shortfall_distance`` normiert defaultmäßig auf ``target``; für die mikroskopische
    # Expectancy-Schwelle sprengte ein winziger absoluter Miss den Aktiv-Mittelwert (Distanz ≫ 1)
    # und maskierte alle anderen Signale. Mit ``expectancy_penalty_scale`` landet ein typischer
    # Miss im Bereich der übrigen Terme (≈ 0…1.5). Fehlt der Key ⇒ ``scale=None`` ⇒ Legacy-Pfad
    # (Normierung auf ``target``), bit-identisch (Zero-Hardcoding). Siehe AGENTS.md Pitfall #108.
    expectancy_penalty_scale = _cfg_value(
        weights, tournament_cfg, "expectancy_penalty_scale"
    )

    distances = {
        "oos_min_trades": _shortfall_distance(float(m.oos_total_trades), req_trades),
        "oos_min_total_return": _shortfall_distance(m.oos_total_return, req_return, scale=return_penalty_scale),
        "oos_min_expectancy": _shortfall_distance(
            m.oos_expectancy, req_expectancy, scale=expectancy_penalty_scale
        ),
        # Issue #759 — None (fehlende Beobachtung) wird HIER, an der Konsumstelle, konservativ als
        # 0.0 behandelt (worst-case fuer die Distanzstrafe) — die Parsing-Schicht liefert None jetzt
        # korrekt durch (kein stiller Missing-Data-Sentinel mehr, siehe parsing.TournamentMetrics).
        "oos_min_win_rate": _shortfall_distance(
            m.oos_win_rate if m.oos_win_rate is not None else 0.0, req_win_rate),
        "oos_max_drawdown": _excess_distance(m.oos_max_drawdown, risk_dd_cap),
        "any_condition": _any_condition_distance(m, weights, tournament_cfg),
    }

    # Issue #635 — zusätzliche Gates, die #612 (Sampler-Constraint) sieht, aber die ursprünglichen
    # 6 Kern-Terme (#452) NICHT abdecken: PSR (grosskalig ∈[0,1], #614) und Excess-Return
    # (kleinskalig ~[0,0.05], #552) — exakt das Skalen-Paar aus dem #635-Symptom. PSR hat einen
    # positiven Target (0.75) ⇒ normale Target-Normierung. Excess-Return hat einen LEGITIMEN
    # Target von 0.0 ('schlage Buy&Hold') ⇒ _shortfall_distance's Target>0-Guard (der einen
    # nicht-positiven Target als 'Gate trivial erfüllt' interpretiert) passt hier NICHT — die
    # Distanz wird daher direkt auf return_penalty_scale normiert (dieselbe Return-Einheiten-Skala
    # wie total_return, kein separater Key nötig).
    req_psr = _cfg_value(weights, tournament_cfg, "oos_min_psr")
    if req_psr is not None and float(req_psr) > 0.0 and getattr(m, "oos_psr", None) is not None:
        distances["oos_min_psr"] = _shortfall_distance(float(m.oos_psr), float(req_psr))

    # Issue #666 — Gate/Reward-Parität: dieselbe |Benchmark|-bewusste Regime-Fallunterscheidung wie
    # das Hard-Gate (backtest_runner._evaluate_oos_eligibility). Im Bär-Markt (oos_buyhold_return<0)
    # ist die Distanz die Unterschreitung des risikoadjustierten per-Perioden-Sortino unter 0
    # (skalenfrei) — NICHT der absolute Excess-Return-Rest-Gap, der im Bärenmarkt trivial ≥0 ist und
    # damit KEINE Diskriminierung träge (exakt das #666-Symptom auf der Constraint-Skala gespiegelt).
    # Ein undefinierter sortino_period liefert — konsistent mit dem oos_min_psr-Präzedenzfall oben —
    # KEINEN erfundenen Distanz-Wert (das Hard-Gate selbst behandelt "undefiniert" separat als Fail).
    req_excess = _cfg_value(weights, tournament_cfg, "oos_min_excess_return")
    oos_excess_return = getattr(m, "oos_excess_return", None)
    oos_buyhold_return = getattr(m, "oos_buyhold_return", None)
    if req_excess is not None and oos_excess_return is not None:
        if oos_buyhold_return is not None and oos_buyhold_return < 0.0:
            sortino_p = getattr(m, "oos_sortino_period", None)
            if sortino_p is not None:
                distances["oos_min_excess_return"] = max(0.0, -float(sortino_p))
        else:
            excess_scale = float(return_penalty_scale) if return_penalty_scale else float(req_return)
            if excess_scale > 0.0:
                distances["oos_min_excess_return"] = max(
                    0.0, float(req_excess) - float(oos_excess_return)
                ) / excess_scale

    # Issue #547 (robuster Schutz) — Clip-Obergrenze pro Term: kein einzelner Distanz-Term darf
    # den Aktiv-Mittelwert je dominieren, unabhängig von Kalibrierfehlern der Ziel-Schwellen.
    # Fehlt ``distance_term_cap`` (oder <= 0) ⇒ kein Cap ⇒ bit-identisch zum Legacy-Verhalten.
    term_cap = _cfg_value(weights, tournament_cfg, "distance_term_cap")
    if term_cap is not None and float(term_cap) > 0.0:
        cap = float(term_cap)
        distances = {k: min(v, cap) for k, v in distances.items()}

    return distances


def _constraint_distance_penalty(
    m: "TournamentMetrics",
    weights: dict,
    risk_dd_cap: float | None,
    tournament_cfg: dict | None,
) -> float:
    """Issue #452 — kontinuierliche Distanzstrafe fuer OOS-Constraint-Verletzungen.
    Issue #467 — OOS-Parameter-Isolation und Penalty Conditioning.
    Issue #505 — lineare (nicht quadratische) Distanzen gegen Term-Dominanz.
    Issue #534 — konsistente Normalisierung ausschliesslich ueber die AKTIVEN Dimensionen.

    Nur evaluiert-aber-nicht-eligible Trials nutzen diesen Pfad. Die Strafe ist rein metrisch
    (keine Gate-Flags als Reward), linear in der auf Target/Scale normierten Ziel-Distanz und
    config-gewichtet. Aggregiert wird als MITTELWERT der aktiven Distanzen
    (``sum(active_dists) / len(active_dists)``): eine inaktive (bereits erfuellte) Dimension traegt
    NULL Gewicht im Divisor und darf die effektive Strafe pro aktiver Dimension nicht mehr
    verzerren (Issue #534 — vorher fiktive Division durch die feste Gesamtzahl der Dimensionen).
    Damit erhaelt derselbe Return-Shortfall dieselbe Teilstrafe, unabhaengig davon, wie viele
    Nebengates zufaellig erfuellt sind. So unterscheidet TPE wieder 'knapp gescheitert' von
    'katastrophal gescheitert'. Issue #629 — dieser Wert wird additiv vom gemeinsamen
    Qualitäts-Kern (``compute_reward``) abgezogen; es gibt kein separates Floor-/Ceiling-Band
    mehr, die Feasibility-Rangordnung selbst kommt ausschliesslich vom #612-Sampler-Constraint.
    Issue #635 — die zugrundeliegenden Distanzen kommen jetzt aus der GETEILTEN
    ``_normalized_gate_distances`` (nur die ursprünglichen 6 Kern-Terme, bit-identisch)."""
    all_distances = _normalized_gate_distances(m, weights, risk_dd_cap, tournament_cfg)
    distances = [all_distances[k] for k in _CORE_DISTANCE_KEYS]

    active_dists = [d for d in distances if d > 0.0]
    if not active_dists:
        return 0.0
    # Issue #534 — Normalisierung strikt ueber die AKTIVEN Dimensionen (mittlere aktive Distanz),
    # NICHT ueber die feste Gesamtzahl len(distances)==6. Inaktive (erfuellte) Gates tragen null
    # Gewicht im Divisor ⇒ derselbe Shortfall ergibt dieselbe Teilstrafe, egal wie viele
    # Nebengates zufaellig erfuellt sind (kein Gradientenrauschen bei der TPE-Suche).
    mean_distance = sum(active_dists) / len(active_dists)
    return mean_distance * float(
        weights.get(
            "constraint_distance_penalty_weight", weights["unevaluable_shaping_span"]
        )
    )


def _gate_proximity(m: "TournamentMetrics", weights: dict) -> float:
    """Issue #407 — kontinuierlicher Eligibility-Gradient fuer unevaluable Trials.

    Selbst wenn ein Symbol im OOS nie evaluiert wird (oos_total_trades==0, Pitfall #75), liefert
    die IS-Performance ein normiertes Naehe-Signal zum Gate: ein Symbol mit hohem IS-total_return /
    IS-win_rate ist 'fast eligible' und soll hoeher bewertet werden als eines, das nie performt.

    Jede Komponente wird gegen ihren Target (``shaping_return_target`` / ``shaping_winrate_target``,
    Zero-Hardcoding) auf ``[0, 1]`` normiert und geclippt; negative IS-Rendite traegt 0 bei. Das
    Ergebnis ist der Mittelwert der aktiven Komponenten ⇒ stets ``∈ [0, 1]``. Fehlen beide Targets
    ⇒ ``0.0`` (Legacy-Verhalten). Der Wert ist rein performance-basiert (kein Gate-Flag) ⇒ kein
    Gate-Gaming; durch die ``[0, 1]``-Bindung bleibt das Shaping hart durch ``unevaluable_shaping_span``
    gedeckelt (Anti-Gate-Gaming-Invariante: unevaluable < evaluable-Floor)."""
    components = []
    return_target = weights.get("shaping_return_target")
    if return_target and float(return_target) > 0.0:
        components.append(
            min(1.0, max(0.0, m.is_best_total_return) / float(return_target))
        )
    winrate_target = weights.get("shaping_winrate_target")
    if winrate_target and float(winrate_target) > 0.0:
        components.append(
            min(1.0, max(0.0, m.is_best_win_rate) / float(winrate_target))
        )
    if not components:
        return 0.0

    import math

    val = sum(components) / len(components)
    if math.isnan(val):
        val = 0.0
    return max(0.0, min(1.0, val))


def compute_reward(
    m: "TournamentMetrics",
    universe_size: int,
    weights: dict | None = None,
    risk_dd_cap: float | None = None,
    *,
    sampled: dict | None = None,
    global_params: dict | None = None,
    strategy: str | None = None,
    tournament_cfg: dict | None = None,
    holdout: bool = False,
    return_terms: bool = False,
) -> float | tuple:
    """weights=None  ⇒ aus optimizer.json (penalty_overfit_weight, penalty_dd_weight,
                     bonus_coverage_weight, penalty_unevaluable_oos, sortino_clip_abs).
    risk_dd_cap=None ⇒ aus tournament.json (max_drawdown).
    Falls not m.oos_evaluated oder base_source is None: Unevaluable-Pfad (Penalty + Shaping) —
    diese Trials haben KEINE OOS-Kennzahl, aus der sich irgendeine Qualität ableiten liesse.
    ISSUE #401: Ist ein OOS-Sample evaluated, aber oos_sortino is None (Zero-Loss /
    n < sortino_min_trades), UND weights['oos_sortino_fallback'] == 'total_return', wird statt des
    Unevaluable-Pfades der geclippte oos_total_return als Base genutzt (Flat-Reward-Landscape-Fix).
    Fehlt der Schluessel ⇒ Unevaluable-Pfad. Issue #629 — dieser Fallback gilt seit der Feasibility-
    Constraint-Migration (#612) UNABHÄNGIG von ``m.oos_eligible``: eligible UND evaluated-aber-
    ineligible Trials teilen sich denselben Base-Auflösungspfad (siehe unten).

    ISSUE #629 — Feasibility ist AUSSCHLIESSLICH ein #612-Sampler-Constraint
    (``oos_constraint_violations``), NIEMALS mehr ein Reward-Term. Jeder EVALUIERTE Trial
    (``m.oos_evaluated and base_source is not None``) — ob ``oos_eligible`` oder nicht — durchläuft
    DENSELBEN Qualitäts-Kern (Base − Divergenz − Drawdown − Turnover − Fold-Dispersion + Tie-Breaker,
    KEIN Floor-Clamp mehr). Ein evaluated-aber-ineligible Trial erhält ZUSÄTZLICH die kontinuierliche,
    bereits existierende Near-Miss-Distanzstrafe (``_constraint_distance_penalty``, #452/#505/#534) on
    top — near-miss bleibt damit klar besser bewertet als ein katastrophaler Miss, aber OHNE
    künstliche Untergrenze/Ober­grenze (``evaluable_reward_floor``/``failure_ceiling``/
    ``unevaluable_ceiling`` sind ENTFALLEN). Das Ergebnis ist EIN stetiges, nicht-gesättigtes
    Qualitätsziel über alle evaluierten Trials; die Feasibility-Rangordnung übernimmt Optuna nativ
    über den #612-Constraint (feasible ≻ infeasible in ``best_trial``/TPE-Sampling).

    universe_size > 1 (und reward_mode != 'per_symbol') ⇒ Coverage-Pfad (A4.3/HI-2):
      reward = base - overfit_gap*penalty_overfit_weight - dd_penalty - gate_distance_penalty
               + coverage*bonus_coverage_weight ; coverage = win_count / max(1, universe_size).

    universe_size == 1 ODER weights['reward_mode'] == 'per_symbol' ⇒ Per-Symbol-Pfad (A4.3):
      KEIN Coverage-Term, dafür Shrinkage-Strafe param_pen Richtung global:
      param_pen = lambda_reg * normalized_param_distance(sampled, global_params,
                               bounds.extract_numeric_bounds(strategy))
                  falls (sampled and global_params and strategy), sonst 0.0.
      reward = base - overfit_gap*penalty_overfit_weight - dd_penalty - param_pen - gate_distance_penalty.

    gate_distance_penalty = _constraint_distance_penalty(...) falls NICHT m.oos_eligible, sonst 0.0.
    base = sortino_soft_scale·asinh(oos_sortino/scale)  # Issue #559 — WEICH, kein Hard-Clip (#588).
    overfit_gap = max(0, is_sortino_median - base)      # nur ausserhalb holdout=True (#594).
    dd_penalty = penalty_dd_weight * (oos_max_drawdown / dd_reward_scale)^2  # Issue #597 (nicht Gate-Cap).
    """

    loaded_tournament_cfg = tournament_cfg

    if weights is None:
        from automation.optimizer.trial_config import config_dir

        cfg_path = config_dir() / "optimizer.json"
        with open(cfg_path, "r", encoding="utf-8") as f:
            weights = json.load(f)
        loaded_tournament_cfg = _read_tournament_cfg()

    if risk_dd_cap is None:
        if loaded_tournament_cfg is None:
            loaded_tournament_cfg = _read_tournament_cfg()
        risk_dd_cap = loaded_tournament_cfg["max_drawdown"]

    reward_mode_config = weights.get("reward_mode", "auto") if weights else "auto"
    if reward_mode_config == "pareto":
        res = (
            float(m.oos_total_return),
            float(m.oos_expectancy),
            # Issue #759 — None-fest an der Konsumstelle (analog oos_sortino direkt darunter).
            float(m.oos_win_rate if m.oos_win_rate is not None else 0.0),
            float(m.oos_sortino if m.oos_sortino is not None else 0.0),
            float(m.oos_max_drawdown),
            float(m.oos_total_trades),
        )
        if return_terms:
            return res, {
                "branch": "pareto",
                "base": 0.0,
                "divergence": 0.0,
                "divergence_at_cap": False,
                "dd_penalty": 0.0,
                "param_pen": 0.0,
                "turnover": 0.0,
                "fold_dispersion": 0.0,
                "time_box_penalty": 0.0,
                "tie_breaker": 0.0,
                "floor_clamped": False
            }
        return res

    penalty_unevaluable_oos = weights["penalty_unevaluable_oos"]

    # ISSUE #401 — Zero-Loss/Sub-Threshold-Sortino-Fallback (Flat-Reward-Landscape-Fix).
    # Ein OOS-Sample, das gehandelt hat, dessen Sortino aber mathematisch undefiniert ist
    # (losses_count == 0 oder n < sortino_min_trades ⇒ oos_sortino is None), darf NICHT auf
    # den flachen Unevaluable-Floor (−9.75) kollabieren — das nivelliert die TPE-Reward-
    # Landschaft (Zero-Gradient). Deklarativ gegated ueber optimizer.json['oos_sortino_fallback']
    # (Zero-Hardcoding); fehlt der Schluessel ⇒ Unevaluable-Pfad. Der Reward-WERT bleibt rein
    # performance-basiert (geclippter OOS-total_return), nie das Gate-Flag — damit kein
    # Gate-Gaming (Falle 2); Micro-Sizing-/Risiko-Gates (Pitfall #58) bleiben ueber oos_eligible
    # (als #612-Sampler-Constraint) wirksam.
    # Issue #629 — die ``m.oos_eligible``-Bedingung ist ENTFALLEN: vor #629 lief ein evaluated-
    # aber-ineligible Trial NIE hier durch (er wurde vom jetzt entfernten
    # ``_constraint_failure_reward`` VORHER abgefangen). Seit eligible UND evaluated-aber-
    # ineligible Trials denselben Qualitäts-Kern teilen, muss dieser Fallback beiden offenstehen —
    # sonst kollabiert ein ineligibler Zero-Loss-Trial auf den Unevaluable-Shaping-Pfad, obwohl
    # echte OOS-Performance-Daten vorliegen.
    base_source = m.oos_sortino
    if (
        m.oos_evaluated
        and m.oos_sortino is None
        and weights.get("oos_sortino_fallback") == "total_return"
    ):
        base_source = m.oos_total_return

    if not m.oos_evaluated or base_source is None:
        # Issue #655 — im HOLDOUT-Kontext (``holdout=True``) ist die IS-Eligibility-Shaping-Formel
        # unten (Trade-/IS-Aktivitäts-Näherung Richtung des Gates) KATEGORIAL fehl am Platz: sie
        # existiert, um dem TPE-Sampler während der Study einen Gradienten Richtung Eligibility zu
        # geben (Pitfall #75) — ein Holdout-Backtest ist aber ein EINMALIGER, abgeschlossener Lauf,
        # kein Optimierungsschritt. Ein Symbol/Vektor, der auf dem Holdout NIE OOS-Trades erzeugt
        # (z. B. der globale Vektor auf einem Symbol, für das er strukturell ungeeignet ist —
        # AdxAtrMomentum/TrendPullback, #656), lieferte bislang trotzdem einen NUMERISCHEN Wert nahe
        # ``penalty_unevaluable_oos`` (z. B. exakt −20.0 bei Nullaktivität) — ununterscheidbar von
        # einem echten, sehr schlechten Reward und damit ein GIFTIGER Sentinel in jeder Cross-
        # Strategy-``R_global``-Aggregation/Baseline-Vergleich (#655-Root-Cause). Der Holdout-Reward
        # ist hier schlicht UNDEFINIERT (keine OOS-Performance zum Bewerten) ⇒ ``None`` statt einer
        # Zahl. Aufrufer (``confirm.py``) MÜSSEN ``None`` explizit behandeln, nicht als Zahl.
        if holdout:
            if return_terms:
                return None, {
                    "branch": "unevaluated_holdout",
                    "base": 0.0,
                    "divergence": 0.0,
                    "divergence_at_cap": False,
                    "dd_penalty": 0.0,
                    "param_pen": 0.0,
                    "turnover": 0.0,
                    "fold_dispersion": 0.0,
                    "time_box_penalty": 0.0,
                    "tie_breaker": 0.0,
                    "floor_clamped": False,
                }
            return None
        # Avoid IO if possible:
        oos_min_trades = None
        if weights is not None and "oos_min_trades" in weights:
            val = weights["oos_min_trades"]
            if val is not None:
                oos_min_trades = int(val)

        if oos_min_trades is None:
            oos_min_trades = _read_oos_min_trades()

        # OOS trade progress (existing signal).
        trade_progress = min(1.0, m.oos_total_trades / max(1, oos_min_trades))

        # ISSUE-OPT-375: while no symbol is IS-eligible, oos_total_trades is flat 0, so the
        # penalty is a flat plateau and TPE has no gradient toward the eligibility threshold.
        # Couple the shaping to IS activity (sum of IS trades across the universe) as well, so
        # "almost eligible" becomes distinguishable from "never eligible". shaping_trade_target
        # lives in optimizer.json (zero-hardcoding); if absent, behaviour is the legacy OOS-only path.
        progress = trade_progress
        # Issue #406 (Pitfall #75, Defekt 2): shaping_trade_target=50 ist universe-skaliert
        # (~70 Symbole). Im Per-Symbol-Pfad (universe_size==1 ODER reward_mode=='per_symbol') ist
        # is_total_trades die Fold-Summe EINES Symbols (≫ 50) ⇒ activity saettigt sofort auf 1.0
        # ⇒ Zero-Gradient genau im Bedarfsfall. Dort den dedizierten, groesseren
        # per_symbol_shaping_trade_target nutzen (Fallback auf shaping_trade_target, wenn absent).
        reward_mode_uneval = weights.get("reward_mode", "auto")
        if universe_size == 1 or reward_mode_uneval == "per_symbol":
            shaping_trade_target = weights.get(
                "per_symbol_shaping_trade_target"
            ) or weights.get("shaping_trade_target")
        else:
            shaping_trade_target = weights.get("shaping_trade_target")
        # Issue #488 — Reward Shaping Monotonicity Guard
        proximity = _gate_proximity(m, weights)
        has_proximity_targets = (
            "shaping_return_target" in weights or "shaping_winrate_target" in weights
        )

        if shaping_trade_target:
            activity = min(1.0, m.is_total_trades / max(1, int(shaping_trade_target)))
            if has_proximity_targets:
                activity = min(activity, proximity)
            progress = max(progress, activity)

        progress = max(progress, proximity)

        # Floor invariant: progress ∈ [0, 1] ⇒ shaping ≤ unevaluable_shaping_span, hence every
        # unevaluable trial stays ≤ penalty + span, strictly below the evaluable floor below.
        shaping = weights["unevaluable_shaping_span"] * progress
        rew = penalty_unevaluable_oos + shaping
        if return_terms:
            return rew, {
                "branch": "unevaluable",
                "base": 0.0,
                "divergence": 0.0,
                "divergence_at_cap": False,
                "dd_penalty": 0.0,
                "param_pen": 0.0,
                "turnover": 0.0,
                "fold_dispersion": 0.0,
                "time_box_penalty": 0.0,
                "tie_breaker": 0.0,
                "floor_clamped": False
            }
        return rew

    sortino_clip_abs = weights["sortino_clip_abs"]
    # Issue #559 — WEICHE Sättigung statt Hard-Clip. Der Hard-Clip ist eine stückweise konstante
    # Funktion mit Gradient 0 oberhalb der Klemmgrenze — genau dort, wo die Winner leben (jeder
    # evaluable Trial mit minimalem Edge klemmte annualisiert auf +5.0 ⇒ TPE erhält ein flaches
    # Plateau und kann den robustesten Kandidaten nicht selektieren). asinh ist linear nahe 0 (kein
    # Informationsverlust unterhalb der Skala), logarithmisch in den Extremen und ÜBERALL streng
    # monoton: base = c·asinh(sortino/c) trägt oberhalb der bisherigen Grenze weiterhin Ordnung
    # (50 > 10 > 5 bleibt erhalten). Fehlt sortino_soft_scale (oder <= 0) ⇒ Legacy-Hard-Clip
    # (bit-identisch, Migrations-sicher). Entschärft strukturell auch die #563-Clip-Sättigung.
    soft_scale = None
    soft_scale_val = weights.get("sortino_soft_scale")
    if soft_scale_val is not None and float(soft_scale_val) > 0.0:
        soft_scale = float(soft_scale_val)

    # Issue #614 — REWARD-BASE = PSR (Probabilistic Sharpe/Sortino Ratio), sobald sie definiert ist.
    # Die PSR ist skalenfrei, in [0,1] beschränkt, ANNUALISIERUNGS-INVARIANT (per-Perioden-ŜR) und
    # bezieht T + Schiefe + Kurtosis explizit ein — der annualisierte Sortino ist bei T≈200 eine
    # Reskalierung ohne Informationsgewinn (Signal-Rausch ≈ 0). Die asinh-Sättigung (#559) und die
    # asinh-Overfit-Divergenz (#565/#613) werden damit ÜBERFLÜSSIG: die Small-Sample-Overfit-Strafe,
    # die die Divergenz heuristisch approximierte, steckt jetzt exakt im √(T−1)-Term der PSR. Fehlt
    # ``oos_psr`` (Legacy-JSONs/Fixtures ohne PSR) ⇒ Fallback auf die asinh/Clip-Sortino-Base
    # (bit-identisch, migrations-sicher) inkl. der Divergenz.
    # Issue #630 — psr_z (die unbeschränkte Effektstärke) als Ranking-Base anstelle der
    # sättigenden Wahrscheinlichkeit (CDF) psr. Die CDF bleibt ein reines statistisches Gate.
    psr_base_active = getattr(m, "oos_psr_z", None) is not None
    if psr_base_active:
        base = float(m.oos_psr_z)
    elif soft_scale is not None:
        base = _apply_soft_scale(float(base_source), soft_scale)
    else:
        base = max(-sortino_clip_abs, min(sortino_clip_abs, base_source))

    penalty_overfit_weight = weights["penalty_overfit_weight"]
    penalty_dd_weight = weights["penalty_dd_weight"]
    bonus_coverage_weight = weights["bonus_coverage_weight"]
    penalty_turnover_weight = weights.get("penalty_turnover_weight", 0.0)
    penalty_relative_cap = weights.get("penalty_relative_cap")

    # Issue #591 — der relative Cap bindet an eine POSITIVE Skalenkonstante (sortino_soft_scale bzw.
    # der Legacy-Fallback sortino_clip_abs), NICHT an ``|base|``. Bei negativem base bedeutete
    # ``|base|`` einen Konditionierungsfehler: je schlechter die Base, desto GRÖSSER die erlaubte
    # Strafe. Die Cap-Höhe ist damit vorzeichen-invariant (base=−5 und base=+5 ⇒ gleiche Cap-Höhe).
    cap_scale = soft_scale if soft_scale is not None else float(sortino_clip_abs)

    divergence_at_cap = False
    # Issue #594 — Holdout-Modus: die IS-abhängigen Terme (Overfit-Divergenz, Fold-Dispersion) sind
    # für einen Single-Fold-Holdout ohne IS-Referenz bedeutungslos. Sie werden ABGESCHALTET (nicht mit
    # einem 0.0-Platzhalter gefüttert, der bei negativem base eine fiktive Overfit-Strafe erzeugte).
    # Ausserhalb des Holdout ist ``is_sortino_median is None`` ein Fehler (kein stiller 0.0-Default).
    if holdout or psr_base_active:
        # Issue #594 — Holdout: keine IS-Referenz. Issue #614 — PSR-Base: die Small-Sample-Overfit-
        # Strafe steckt bereits im √(T−1)-Term der PSR ⇒ die separate asinh-Divergenz wäre eine
        # inkohärente Doppelbestrafung auf einer anderen Skala (sortino vs. PSR ∈ [0,1]).
        divergence_penalty = 0.0
    else:
        # Issue #613 — die Divergenz-Strafe nutzt AUSSCHLIESSLICH den GEPOOLTEN IS-Sortino
        # (``is_sortino_pooled``, aus der IS-Equity-Kurve — DERSELBE mtm-Pfad wie ``oos_sortino``/base).
        # Nur so vergleicht ``overfit_gap = is − base`` zwei Grössen DERSELBEN Aggregationsebene. Der
        # Fold-/Symbol-Median (``is_sortino_median``) bleibt rein forensische Telemetrie — als Divergenz-
        # Grösse trieb er den Term systematisch in die Sättigung (#613: corr(is_median, oos_pooled)=0.185,
        # 96 % im oos_luck-Ast, 72 % konstant am Cap ⇒ kein Gradient). Fehlt ``is_sortino_pooled``
        # (Legacy-JSONs/Fixtures) ⇒ Fallback auf ``is_sortino_median`` (bit-identisch, migrations-sicher).
        is_source = (m.is_sortino_pooled if getattr(m, "is_sortino_pooled", None) is not None
                     else m.is_sortino_median)
        if is_source is None:
            raise ValueError(
                "compute_reward: is_sortino_pooled/is_sortino_median is None ohne holdout=True — ein "
                "Platzhalter darf nie in einen Reward-Ausdruck fliessen (#594/#613). Holdout-Rewards "
                "mit holdout=True aufrufen."
            )
        # Issue #565 / #575 — Skalenparität mit asinh erfordert dieselbe Kompression wie für base.
        is_sortino_val = is_source
        if soft_scale is not None:
            is_sortino_val = _apply_soft_scale(is_sortino_val, soft_scale)

        overfit_gap = max(0.0, is_sortino_val - base)
        divergence_mode = weights.get("overfit_divergence_mode")
        if divergence_mode == "symmetric":
            diff = is_sortino_val - base
            if diff >= 0.0:
                divergence_penalty = penalty_overfit_weight * diff
            else:
                oos_luck_w = float(
                    weights.get("overfit_oos_luck_weight", penalty_overfit_weight)
                )
                divergence_penalty = oos_luck_w * (-diff)
        else:
            divergence_penalty = penalty_overfit_weight * overfit_gap

        if penalty_relative_cap is not None:
            cap_val = float(penalty_relative_cap) * cap_scale
            if divergence_penalty >= cap_val:
                divergence_at_cap = True
            divergence_penalty = min(
                divergence_penalty, cap_val
            )

    dd_penalty = _dd_penalty(m, weights, risk_dd_cap)

    # Issue #509 (Cost Drag & Turnover Churning) - Turnover Penalty
    # The penalty increases linearly with the number of OOS trades.
    # Issue #631 — mit penalty_scale_vs_base gegen die realisierte Base-Streuung rekalibriert
    # (siehe _penalty_scale_vs_base). Fehlt der Key ⇒ Faktor 1.0 (Legacy, bit-identisch).
    #
    # Issue #774 — Root-Cause: ``penalty_turnover_weight`` (0.0003 = 3 bps) ist EXPLIZIT aus TSLA
    # (spread 2 bps + commission 1 bps) hergeleitet. Das Universum enthält 8 Krypto-Symbole mit
    # 16 bps Round-Trip-Kosten (spread_bps_by_asset_class.CRYPTO=15) — die angesetzte Strafe war dort
    # 5,3× zu klein, der Optimizer unterschätzte die Kosten hochfrequenter Konfigurationen GENAU dort,
    # wo sie am höchsten sind. Fix: ``round_trip_cost_bps`` (#562, bereits pro Trial gestempelt,
    # dieselbe Quelle wie das kostenrelative Expectancy-Gate) treibt die Strafe, sobald verfügbar —
    # ``penalty_turnover_weight`` wird zum FALLBACK für fehlende Kosten-Telemetrie (Zero-Hardcoding,
    # bit-identischer Legacy-Pfad, kein Verhaltensbruch für Trials ohne die Telemetrie).
    c_rt_bps = getattr(m, "round_trip_cost_bps", None)
    if c_rt_bps is not None:
        turnover_penalty = (m.oos_total_trades * (float(c_rt_bps) / 10_000.0)
                           * _penalty_scale_vs_base(weights))
    else:
        turnover_penalty = m.oos_total_trades * penalty_turnover_weight * _penalty_scale_vs_base(weights)

    # Issue #589/#590 — Fold-Dispersions-Strafe auf den per-Fold-RETURNS (die gut konditionierte
    # Größe; nach #589 NICHT mehr auf den Fold-Sortinos). #590 — normiert über ``oos_folds_total``,
    # nicht über die Zahl valider Folds: fehlende/degenerierte Folds sind MAXIMALE Unsicherheit
    # (missing_fold_penalty_scale), keine Auslassung — sonst umgeht der Optimierer die Strafe, indem
    # er die Bewertung löscht (Fold-Degeneration). Im Holdout (Single-Fold) abgeschaltet.
    fold_dispersion_penalty = 0.0
    w_disp = weights.get("fold_dispersion_weight")
    fold_returns = list(getattr(m, "oos_fold_returns", None) or [])
    n_total = int(getattr(m, "oos_folds_total", 0) or 0)
    if w_disp and not holdout and n_total >= 2:
        n_valid = len(fold_returns)
        base_disp = statistics.pstdev(fold_returns) if n_valid >= 2 else 0.0
        # Issue #616 — NORMIERUNGSSKALA (analog dd_reward_scale, #597): ``pstdev(fold_returns)`` lebt auf
        # der Roh-Return-Skala (0.001–0.05), ``base`` auf der PSR-/asinh-Sortino-Skala ⇒ ohne Normierung
        # war der Term drei Grössenordnungen zu klein (Median 0.036, struktureller Blindgänger — exakt der
        # Pre-#597-dd_penalty-Fehler). ``fold_dispersion_scale`` (realisierte Fold-Return-Streuung ≈ 0.03)
        # hebt ihn in den Bereich der übrigen Terme. Fehlt der Key ⇒ Roh-Skala (Legacy, bit-identisch).
        _fds = weights.get("fold_dispersion_scale")
        fold_dispersion_scale = float(_fds) if _fds and float(_fds) > 0.0 else None
        norm_disp = (base_disp / fold_dispersion_scale) if fold_dispersion_scale else base_disp
        if n_valid < n_total:
            # #616 — die Missing-Fold-Strafe auf DERSELBEN normierten Skala (sonst wieder blind).
            miss_scale = float(weights.get("missing_fold_penalty_scale", 0.0))
            frac_missing = (n_total - n_valid) / n_total
            fold_dispersion_penalty = float(w_disp) * (norm_disp + miss_scale * frac_missing)
        else:
            fold_dispersion_penalty = float(w_disp) * norm_disp
        # Issue #631 — mit penalty_scale_vs_base gegen die realisierte Base-Streuung rekalibriert.
        # Fehlt der Key ⇒ Faktor 1.0 (Legacy, bit-identisch).
        fold_dispersion_penalty *= _penalty_scale_vs_base(weights)
        if penalty_relative_cap is not None:
            fold_dispersion_penalty = min(
                fold_dispersion_penalty, float(penalty_relative_cap) * cap_scale
            )

    # Issue #711 (Req-04, chirurgisch) — additiver Time-Box-Penalty-Term (Deadline-Näherung), rein
    # metrisch skaliert, KEIN Gate-Ersatz (siehe _time_box_penalty-Docstring). Fehlt der Key oder
    # m.oos_median_bars_held ⇒ 0.0 (Default, bit-identisch/Legacy).
    time_box_penalty = _time_box_penalty(m, weights)

    # Issue #559/#638 — return-Tie-Breaker: bricht Rest-Plateaus im eligiblen Ast ökonomisch sinnvoll
    # auf (oos_total_return streut, wo die Base nicht mehr differenziert). Issue #638 — w_ret MUSS so
    # klein sein, dass σ(w_ret·return) ≪ σ(base) bleibt: auf der alten, breiter gestreuten asinh-
    # Sortino-Base war w_ret=2.0 ein echter Tie-Breaker, auf der seit #614/#630 viel enger gestreuten
    # psr_z-Base überstimmte derselbe Wert die Base-Ordnung (Return-Chasing durch die Hintertür).
    # Fehlt w_ret ⇒ 0.0.
    w_ret = float(weights.get("w_ret", 0.0))
    return_tie_breaker = w_ret * float(m.oos_total_return)

    # Issue #629 — near-miss OOS-Gate-Distanzstrafe: NUR für evaluated-aber-ineligible Trials, additiv
    # auf denselben Qualitäts-Kern, den ein eligibler Trial mit identischen Kennzahlen erhielte (kein
    # separates Floor/Ceiling-Band mehr). Ein knapper Miss bleibt so nahe an einem gleich guten
    # eligiblen Trial, ein katastrophaler Miss fällt weit ab — die Feasibility-RANGORDNUNG selbst
    # kommt ausschliesslich vom #612-Sampler-Constraint (``oos_constraint_violations``).
    gate_distance_penalty = 0.0
    if not m.oos_eligible:
        # Issue #467/#468 (strikte OOS-Isolation): _constraint_distance_penalty verlangt die
        # OOS-Schwellen (oos_min_*) und wirft fail-loud, wenn sie fehlen. Wird compute_reward mit
        # explizitem ``weights``, aber ohne ``tournament_cfg`` aufgerufen (DI-/confirm-Pfade,
        # universe_size==1), bleibt loaded_tournament_cfg sonst None ⇒ die kanonischen oos_min_*
        # aus tournament.json wären unsichtbar. Hier die Single-Source-of-Truth-Config nachladen
        # (idempotent, gecached), bevor die Strafe greift.
        if loaded_tournament_cfg is None:
            loaded_tournament_cfg = _read_tournament_cfg()
        gate_distance_penalty = _constraint_distance_penalty(
            m, weights, risk_dd_cap, loaded_tournament_cfg
        )

    # A4.3: per-symbol reward path. The coverage term (win_count/universe_size) degenerates
    # when the universe is a single symbol, so drop it and add a shrinkage penalty toward the
    # global optimum instead (Gate 2 in reward space). Triggered by universe_size == 1, or
    # explicitly via reward_mode == 'per_symbol'.
    reward_mode = weights.get("reward_mode", "auto")
    if universe_size == 1 or reward_mode == "per_symbol":
        param_pen = 0.0
        if sampled and global_params and strategy:
            from automation.optimizer import bounds

            b = bounds.extract_numeric_bounds(strategy)
            # Issue #631 — mit penalty_scale_vs_base gegen die realisierte Base-Streuung rekalibriert.
            # Fehlt der Key ⇒ Faktor 1.0 (Legacy, bit-identisch).
            param_pen = (
                weights["lambda_reg"]
                * bounds.normalized_param_distance(sampled, global_params, b)
                * _penalty_scale_vs_base(weights)
            )
        reward = (
            base
            - divergence_penalty
            - dd_penalty
            - param_pen
            - turnover_penalty
            - fold_dispersion_penalty
            - gate_distance_penalty
            - time_box_penalty
            + return_tie_breaker
        )
        if return_terms:
            return reward, {
                "branch": "per_symbol" if m.oos_eligible else "failure",
                "base": base,
                "divergence": divergence_penalty,
                "divergence_at_cap": divergence_at_cap,
                "dd_penalty": dd_penalty,
                "param_pen": param_pen,
                "turnover": turnover_penalty,
                "fold_dispersion": fold_dispersion_penalty,
                "time_box_penalty": time_box_penalty,
                "tie_breaker": return_tie_breaker,
                "gate_distance_penalty": gate_distance_penalty,
                "floor_clamped": False
            }
        return reward

    # Coverage path (universe_size > 1).
    coverage = m.win_count / max(1, universe_size)
    coverage_bonus = coverage * bonus_coverage_weight
    reward = (
        base
        - divergence_penalty
        - dd_penalty
        + coverage_bonus
        - turnover_penalty
        - fold_dispersion_penalty
        - gate_distance_penalty
        - time_box_penalty
        + return_tie_breaker
    )
    if return_terms:
        return reward, {
            "branch": "eligible" if m.oos_eligible else "failure",
            "base": base,
            "divergence": divergence_penalty,
            "divergence_at_cap": divergence_at_cap,
            "dd_penalty": dd_penalty,
            "param_pen": 0.0,
            "turnover": turnover_penalty,
            "fold_dispersion": fold_dispersion_penalty,
            "time_box_penalty": time_box_penalty,
            "tie_breaker": return_tie_breaker,
            "gate_distance_penalty": gate_distance_penalty,
            "floor_clamped": False
        }
    return reward


# Issue #631 — deklaratives Kalibrier-Fixture für ``assert_penalty_scale_calibrated``: repräsentiert
# eine realistische Eligible-Kohorte (psr_z knapp über dem Gate-Quantil Φ⁻¹(0.75)≈0.6745, realisierte
# Drawdown-Spanne 0.6–2.4 % aus #597, Turnover-Spanne 20–120 Trades, vier leicht streuende Fold-
# Returns pro Trial aus #616). Rein & deterministisch — KEIN I/O, KEIN Zufall.
_CALIBRATION_FIXTURE_PSR_Z = (0.6745, 0.7245, 0.7745, 0.8245, 0.8745, 0.9245, 0.9745, 1.0245)
_CALIBRATION_FIXTURE_DD = (0.006, 0.010, 0.014, 0.018, 0.020, 0.022, 0.024, 0.008)
_CALIBRATION_FIXTURE_TRADES = (20, 35, 50, 65, 80, 95, 110, 120)
_CALIBRATION_FIXTURE_FOLD_RETURNS = (
    (0.010, 0.012, 0.009, 0.011), (0.020, -0.010, 0.015, 0.005),
    (0.008, 0.008, 0.009, 0.007), (0.030, -0.020, 0.025, -0.010),
    (0.011, 0.010, 0.012, 0.009), (0.015, 0.005, 0.020, 0.000),
    (0.009, 0.011, 0.010, 0.010), (0.025, -0.015, 0.020, -0.005),
)
# Issue #711 — realistische Haltedauer-Spanne (Bars, 1h) von kurzen Micro-Yield-Exits bis nahe der
# 24-Bar-Zeitbox (GR-01, #714), für die time_box_penalty-Kalibrierprüfung.
_CALIBRATION_FIXTURE_BARS_HELD = (2.0, 4.0, 6.0, 8.0, 10.0, 14.0, 18.0, 22.0)
_CALIBRATION_PENALTY_TERM_KEYS = ("dd_penalty", "turnover", "fold_dispersion", "time_box_penalty")


def assert_penalty_scale_calibrated(weights: dict) -> None:
    """Issue #631 — Fail-loud-Kalibrier-Assertion beim Config-Load.

    Berechnet ``compute_reward`` über das deklarative Kalibrier-Fixture (oben) und prüft:
    ``median(σ_penalty_terms) ≤ σ_base``. Verletzt eine Config diese Grenze, überstimmen die
    additiven Strafterme (dd_penalty/turnover/fold_dispersion/time_box_penalty, #711) strukturell die
    Base-Streuung — exakt der #631-Root-Cause-Fehler (Strafterme wurden gegen die alte, weiter
    gestreute asinh-Sortino-Base kalibriert und dominieren seit #614/#630 die viel engere psr_z-
    Streuung). ``ValueError`` bei Verletzung; kein Effekt auf den Reward-Wert selbst (reine Config-
    Validierung, wie ``assert_any_condition_parity``, #593)."""
    # Defensiv: ein unvollständiges/leeres ``weights`` (z. B. fehlende optimizer.json in Tests/
    # Sonderpfaden) ist NICHT das, was diese Assertion prüfen soll — compute_reward selbst wirft dafür
    # bereits fail-loud KeyErrors an der eigentlichen Aufrufstelle. Diese Kalibrier-Prüfung ist ein
    # No-Op, solange die für den Qualitäts-Kern nötigen Kern-Keys fehlen (Zero-Hardcoding).
    required = ("penalty_unevaluable_oos", "sortino_clip_abs", "penalty_overfit_weight",
                "penalty_dd_weight", "bonus_coverage_weight")
    if any(k not in weights for k in required):
        return

    tcfg = {
        "oos_min_trades": 10, "oos_min_total_return": 0.0, "oos_min_expectancy": 0.0,
        "oos_min_win_rate": 0.0, "max_drawdown": 0.3,
    }
    all_terms = []
    for z, dd, n_tr, folds, bars_held in zip(
        _CALIBRATION_FIXTURE_PSR_Z, _CALIBRATION_FIXTURE_DD,
        _CALIBRATION_FIXTURE_TRADES, _CALIBRATION_FIXTURE_FOLD_RETURNS,
        _CALIBRATION_FIXTURE_BARS_HELD,
    ):
        from automation.optimizer.parsing import TournamentMetrics

        m = TournamentMetrics(
            oos_evaluated=True, oos_eligible=True, is_sortino_median=0.0,
            oos_sortino=z, oos_max_drawdown=dd, oos_total_trades=n_tr, win_count=1,
            fully_eligible_pairs=1, is_total_trades=n_tr, oos_total_return=0.01,
            oos_win_rate=0.4, oos_profit_factor=1.3, oos_psr_z=z,
            oos_fold_returns=folds, oos_folds_total=4,
            oos_median_bars_held=bars_held,
        )
        _, terms = compute_reward(
            m, universe_size=1, weights=weights, risk_dd_cap=tcfg["max_drawdown"],
            tournament_cfg=tcfg, return_terms=True,
        )
        all_terms.append(terms)

    base_vals = [t["base"] for t in all_terms]
    sigma_base = statistics.pstdev(base_vals)
    if sigma_base <= 0.0:
        return  # degeneriertes Fixture (kann bei extremen Custom-Weights auftreten) ⇒ kein Urteil

    penalty_sigmas = [
        statistics.pstdev([t.get(k, 0.0) for t in all_terms])
        for k in _CALIBRATION_PENALTY_TERM_KEYS
    ]
    # Issue #711 — analog #534 (_constraint_distance_penalty: "Mittelwert der AKTIVEN Distanzen"):
    # nur Terme mit sigma > 0 (für DIESE Config überhaupt wirksam) gehen in den Median ein. Ein
    # strukturell inaktiver Term (z. B. penalty_time_box_weight fehlt/ist 0.0 in einer Config, die
    # den Key noch nicht kennt) darf die Guard-Schärfe für die ÜBRIGEN, tatsächlich konfigurierten
    # Terme nicht verwässern — sonst senkt jeder künftige neue additive Term (der für eine gegebene
    # Config zufällig inaktiv ist) strukturell die Sensitivität für alle bereits aktiven Terme.
    active_sigmas = [s for s in penalty_sigmas if s > 0.0]
    if not active_sigmas:
        return  # keine aktiven Strafterme in dieser Config ⇒ nichts zu kalibrieren
    median_penalty_sigma = statistics.median(active_sigmas)
    if median_penalty_sigma > sigma_base:
        raise ValueError(
            f"PENALTY_SCALE_MISCALIBRATED (#631): median(σ_penalty_terms)={median_penalty_sigma:.6f} "
            f"> σ_base={sigma_base:.6f} auf dem Kalibrier-Fixture — dd_penalty/turnover/"
            f"fold_dispersion überstimmen strukturell die Base-Streuung, statt sie nur zu "
            f"modifizieren (Straf-Terme dominieren die Base — Rekalibrierung nötig, #631). "
            f"penalty_scale_vs_base (optimizer.json) nachschärfen."
        )
