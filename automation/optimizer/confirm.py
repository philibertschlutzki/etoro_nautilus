import datetime as dt
import json
import hashlib
from collections import Counter
from pathlib import Path
from automation.optimizer.trial_config import build_trial, config_dir, freeze_study_config, resolve_wf_settings
from automation.optimizer.runner import run_backtest, BacktestRunError
from automation.optimizer.parsing import parse_tournament
from automation.optimizer.reward import compute_reward
from automation.optimizer.manifest import WORK, catalog_fingerprint
from automation.log_manager import emit_execution_event
from automation.optimizer import invariants as _inv
from automation.optimizer import _contracts
from automation.optimizer import reward as _reward

# Issue #659 — gültige Werte für tournament.json['promotion_correction_mode']. "conjunction"
# (Default, fehlt der Key) ist bit-identisch zum Pre-#659-Verhalten (DSR UND Bootstrap-CI UND PBO
# müssen ALLE bestehen); "dsr_or_robust_pair" ersetzt die Konjunktion durch eine der beiden
# unabhängigen Bestätigungen (DSR ODER (PBO + Bootstrap-CI)). Ein unbekannter Wert bricht fail-loud
# ab (kein stiller Fallback auf eine möglicherweise falsch geschriebene Modus-Zeichenkette).
_VALID_PROMOTION_CORRECTION_MODES = frozenset({"conjunction", "dsr_or_robust_pair"})

# Issue #865 (P2, Katalog #862-#865, GitHub-Issue #759, Pitfall #277) — gültige Werte für
# tournament.json['deflation_heterogeneity_policy']. Root-Cause: die N_PERIODS_HETEROGENEOUS-Prüfung
# (Issue #845) griff bislang ERST an der Export-Kohärenzgrenze — weit NACH der ``promote``-
# Entscheidung (siehe unten) — und nullte ``deflation_sr0``/``deflation_dsr``/``deflation_dsr_z`` nur
# noch für die TELEMETRIE. Die Promotion selbst hatte die DSR bereits aus der (inkommensurablen)
# gepoolten Kohorte verwendet: eine nicht mehr aussagekräftige Korrektur bestand STILLSCHWEIGEND als
# Test. "suppress_dsr" nullt die DSR VOR der Promotion-Entscheidung (das bereits bestehende
# ``deflation_dsr is None ⇒ REJECT_HOLDOUT_DSR_DROP``-Gate greift dann korrekt fail-closed).
# "reject" behandelt die Heterogenität selbst als eigenständigen, unbedingten Ablehnungsgrund
# (``REJECT_DEFLATION_HETEROGENEOUS``), unabhängig vom sonstigen Gate-Ausgang. "per_stratum"
# (Default, siehe Issue-Text) berechnet SR₀/DSR NICHT aus der vollen Kohorte, sondern aus dem
# Quartils-Stratum von ``oos_n_periods``, dem der promotete Trial selbst angehört — die
# kommensurable TEIL-Kohorte bleibt nutzbar, statt die Korrektur komplett zu verwerfen.
_VALID_DEFLATION_HETEROGENEITY_POLICIES = frozenset({"suppress_dsr", "reject", "per_stratum"})


def resolve_promotion_multiplicity(route: str, *, deflation_n_family: int | None) -> tuple[int, dict]:
    """Issue #887 (Pitfall #278) — die EINE Quelle für die Multiple-Testing-Multiplizität ``N``
    einer Deflation, ABHÄNGIG von der Promotion-Route:

    - ``'symbol_eligible'`` (die reguläre Selektion, #625/#652): ``N = deflation_n_family`` — die
      Zahl der in Stufe 1 (studienweite Eligibility) tatsächlich durchsuchten/eligiblen Kandidaten.
      Bit-identisch zum Vorzustand.
    - ``'global_default'`` (#682/#783 — kein einziger symbol-eligibler Trial, der GLOBALE Default
      wird stattdessen gegen das Symbol-Holdout getestet): ``N = 1``. Der geprüfte Parametervektor
      ist der globale Default aus ``strategy_defaults.json`` — er wurde NICHT aus
      ``deflation_n_family`` Kandidaten ausgewählt (er hat an dieser Selektion nicht
      teilgenommen); die Multiple-Testing-Korrektur darf nur die tatsächlich stattgefundene Auswahl
      bestrafen (Root-Cause #887: vorher wurde die volle Stufe-1-Familiengrösse — 99, 117 — auf
      einen Kandidaten angewendet, der diese Suche nie durchlaufen hat, was die Deflationsschwelle
      strukturell unerreichbar machte).

    Rückgabe: ``(n, rationale)`` — ``rationale`` ist das Telemetriefeld
    ``deflation_multiplicity_rationale`` (#887 Fix Punkt 4), ohne das die Entscheidung im
    Nachhinein nicht überprüfbar wäre (#847-Klasse). Ein unbekannter ``route``-Wert bricht
    fail-loud ab (kein stiller Fallback auf eine falsch geschriebene Route-Zeichenkette)."""
    if route == "global_default":
        return 1, {
            "route": "global_default", "n_used": 1,
            "n_family_available": int(deflation_n_family or 0),
            "reason": "candidate did not participate in stage-1 selection",
        }
    if route == "symbol_eligible":
        n = int(deflation_n_family or 0)
        return n, {
            "route": "symbol_eligible", "n_used": n,
            "n_family_available": n,
            "reason": "candidate was selected from the full stage-1 family",
        }
    raise ValueError(
        f"resolve_promotion_multiplicity: unbekannte promotion route {route!r} — erwartet "
        "'symbol_eligible' oder 'global_default'."
    )


def _stratify_cohort_by_n_periods(cohort_sr_np_pairs, anchor_n_periods, *, n_strata=4):
    """Issue #865 — quartilsbasierte Stratifizierung der DSR-Kohorte nach ``oos_n_periods``: liefert
    nur die Teil-Kohorte (Sortino-Werte), deren ``oos_n_periods`` im selben Quartils-Stratum liegt wie
    ``anchor_n_periods`` (der promotete Trial). Motivation identisch zu #845 (gepoolte Varianz über
    eine stark streuende ``oos_n_periods``-Achse ist nicht kommensurabel) — statt die Korrektur ganz
    zu verwerfen (``suppress_dsr``), wird nur der kommensurable Teil verwendet.

    Bei zu wenigen Kohorten-Mitgliedern für ``n_strata`` volle Quartile wird die Strata-Zahl auf
    ``len(cohort_sr_np_pairs)`` REDUZIERT (nicht auf 1 — ein einziges Stratum wäre die volle
    gepoolte, inkommensurable Kohorte und würde exakt den #845/#865-Bug reproduzieren, den diese
    Funktion beheben soll). Bei einer stark streuenden Zwei-Trial-Kohorte (z. B. n_periods=[50,
    2500]) liefert das je EIN Singleton-Stratum pro Trial — der Aufrufer erkennt ein Stratum mit
    < 2 Mitgliedern und fällt dokumentiert auf das ``suppress_dsr``-Verhalten zurück.

    Returns ``(stratum_sr, stratum_id, stratum_n_periods)`` — ``stratum_n_periods`` ist die Liste der
    ``oos_n_periods``-Werte im gewählten Stratum (für den #653-T-Median der Teil-Kohorte).
    """
    import statistics as _st

    n_values = sorted(np_ for _, np_ in cohort_sr_np_pairs)
    n_strata_effective = max(1, min(n_strata, len(n_values)))
    if n_strata_effective < 2:
        return (
            [sr for sr, _ in cohort_sr_np_pairs],
            0,
            [np_ for _, np_ in cohort_sr_np_pairs],
        )
    cut_points = _st.quantiles(n_values, n=n_strata_effective)

    def _stratum_of(x):
        s = 0
        for cp in cut_points:
            if x > cp:
                s += 1
            else:
                break
        return s

    anchor_stratum = _stratum_of(anchor_n_periods)
    stratum_sr = [sr for sr, np_ in cohort_sr_np_pairs if _stratum_of(np_) == anchor_stratum]
    stratum_n_periods = [np_ for _, np_ in cohort_sr_np_pairs if _stratum_of(np_) == anchor_stratum]
    return stratum_sr, anchor_stratum, stratum_n_periods


def _holdout_bootstrap_ci_passes(metrics, *, confidence: float = 0.95) -> tuple[bool | None, float | None]:
    """Issue #619 — Stationary-Bootstrap-CI auf dem per-Perioden-Sortino des Holdout (Politis/Romano).

    Statt des Punktschätzers (``oos_sortino > 0``) prüft das Gate die UNTERE CI-Grenze
    (``ci_lower(sortino) > 0``) — die statistisch saubere Version dessen, was ``deflated_selection``
    heuristisch approximiert, und auf 21 Trades EHRLICH (das CI wird breit sein — genau die Information).
    Rückgabe ``(passes, ci_lower)``.

    Issue #1005 (Katalog #858, Pitfall #343) — ``passes=None`` bei zu wenigen Returns (< 5, CI nicht
    schätzbar). VORHER lieferte diese Funktion ``True`` in diesem Fall ("kein Zusatz-Veto"); in einer
    KONJUNKTIVEN Verwendung (Aufrufer prüft ``if holdout_passed and ...``) ist das unproblematisch, weil
    das ``oos_sortino``-Punkt-Gate bereits vorher bestand. In einer DISJUNKTIVEN Bestätigung
    (``dsr_or_robust_pair``, confirm_on_holdout weiter unten) macht ``True`` eine NICHT SCHÄTZBARE
    Prüfung fälschlich zu einer BESTANDENEN — ``None`` ist keine bestandene Prüfung (bool(None) is
    False), jeder Aufrufer behandelt sie explizit als nicht bestanden."""
    rets = list(getattr(metrics, "oos_period_returns", ()) or ())
    if len(rets) < 5:
        return None, None
    from automation.optimizer.bootstrap import bootstrap_ci, sortino_statistic, ci_lower_bound_passes
    _, lo, _ = bootstrap_ci(rets, lambda a: sortino_statistic(a, mar=0.0, annualization=1.0),
                            confidence=confidence, seed=42)
    return ci_lower_bound_passes(lo, 0.0), lo


def _holdout_gate_passed(metrics, risk_dd_cap: float, *, sortino_fallback_enabled: bool) -> bool:
    """Issue #533 — Single Source of Truth für die Holdout-Gate-Entscheidung inkl.
    ``oos_sortino_fallback``-Parität zu ``reward.py`` (die »evaluable-but-sortino-undefined«-Regel).

    Der Sortino ist per Definition ``None`` auf einem verlustfreien OOS-Fold (``losses_count == 0``,
    ``backtest_runner.py``). Vor diesem Fix blockierte das Holdout-Gate eine solche verlustfreie,
    profitable OOS-Periode fälschlich (``None → 0.0``, ``0.0 > 0.0`` = False), obwohl sie das beste
    denkbare Ergebnis ist. Analog zu ``reward.py['oos_sortino_fallback'] == 'total_return'``
    (``reward.py`` compute_reward) greift jetzt der ``oos_total_return > 0`` als Pass-Kriterium,
    sobald der Sortino mathematisch undefiniert ist. ``oos_total_return <= 0`` passiert NIEMALS —
    kein Gate-Gaming; Micro-Sizing-/Risiko-Gates bleiben über ``oos_eligible`` und den
    ``risk_dd_cap`` wirksam.
    """
    if not (metrics.oos_evaluated and metrics.oos_eligible):
        return False

    # Drawdown-Cap (None-safe: ein fehlender Drawdown gilt als schlechtestmöglich ⇒ blockiert).
    max_dd = metrics.oos_max_drawdown if metrics.oos_max_drawdown is not None else 1.0
    if max_dd > risk_dd_cap:
        return False

    if metrics.oos_sortino is not None:
        return metrics.oos_sortino > 0.0

    # Sortino undefiniert (Zero-Loss-Fold): total_return-Fallback nur bei aktiviertem Flag
    # (Parität zu reward.py; fehlt/deaktiviert ⇒ Legacy-Verhalten, blockiert).
    if sortino_fallback_enabled:
        return metrics.oos_total_return > 0.0
    return False


def confirm_on_holdout(
    study,
    strategy: str,
    *,
    run_backtest=run_backtest,
    build_trial=build_trial
) -> dict:
    """
    Trial mit holdout_days=0, n_folds=1 (Holdout = reguläres OOS).
    Liest risk_dd_cap aus tournament.json.
    passed = oos_evaluated & oos_eligible & oos_sortino>0 & oos_max_drawdown<=cap.
    Rückgabe: {'passed': bool, 'metrics': dict, 'trial_dir': str}.
    """
    best_trial = study.best_trials[0] if len(getattr(study, "directions", ["maximize"])) > 1 else study.best_trial
    sampled = best_trial.user_attrs.get("sampled_params", best_trial.params)

    cfg_dir = config_dir()
    optimizer_path = cfg_dir / "optimizer.json"
    seed = 42
    oos_sortino_fallback = None
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            opt_data = json.load(f)
            seed = opt_data.get("seed", 42)
            # Issue #533 — oos_sortino_fallback-Parität zu reward.py (Zero-Hardcoding).
            oos_sortino_fallback = opt_data.get("oos_sortino_fallback")

    # Dynamisch Holdout-Tage auslesen (Zero-Hardcoding)
    backtest_path = cfg_dir / "backtest.json"
    holdout_days_cfg = 45
    if backtest_path.exists():
        with open(backtest_path, "r", encoding="utf-8") as f:
            bt_data = json.load(f)
            holdout_days_cfg = bt_data.get("walk_forward", {}).get("holdout_days", 45)

    # Issue #796 — EINE eingefrorene Config fuer diesen Holdout-Trial statt einer Pro-Trial-Kopie
    # (der Holdout-Studyname ist eindeutig je Confirm-Aufruf, daher genuegt ein einmaliges Freeze).
    holdout_study_name = f"{study.study_name}_holdout"
    holdout_wf_settings = resolve_wf_settings(
        cfg_dir, holdout_days=0, n_folds=1, oos_window_days_override=holdout_days_cfg)
    holdout_cfg_dir = freeze_study_config(holdout_study_name, holdout_wf_settings, base_cfg=cfg_dir)

    # Erzeuge Holdout-Trial mit holdout_days=0 und n_folds=1, aber OOS override auf Holdout-Länge
    trial_dir, manifest_path = build_trial(
        strategy_class=strategy,
        sampled=sampled,
        study_name=holdout_study_name,
        trial_number=best_trial.number,
        seed=seed,
        holdout_days=0,
        n_folds=1,
        oos_window_days_override=holdout_days_cfg,
        copy_config=False,
        study_config_dir=holdout_cfg_dir,
    )

    # Subprozess/Backtest ausführen
    try:
        output_path = run_backtest(trial_dir, manifest_path, config_dir=holdout_cfg_dir)
        metrics = parse_tournament(output_path)
    except BacktestRunError:
        return {
            "passed": False,
            "metrics": {},
            "trial_dir": str(trial_dir),
            "reason": "holdout_subprocess_failed"
        }

    tournament_path = cfg_dir / "tournament.json"
    risk_dd_cap = 0.30  # Fallback
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            t_data = json.load(f)
            risk_dd_cap = t_data.get("max_drawdown", 0.30)

    # Issue #533 — Holdout-Gate mit oos_sortino_fallback-Parität: ein verlustfreier (sortino=None),
    # profitabler OOS-Fold passiert über oos_total_return>0, statt fälschlich blockiert zu werden.
    passed = _holdout_gate_passed(
        metrics, risk_dd_cap,
        sortino_fallback_enabled=(oos_sortino_fallback == "total_return"),
    )

    return {
        "passed": passed,
        "metrics": {
            "oos_sortino": metrics.oos_sortino,
            "oos_total_return": metrics.oos_total_return,
            "oos_max_drawdown": metrics.oos_max_drawdown,
            "oos_evaluated": metrics.oos_evaluated,
            "oos_eligible": metrics.oos_eligible,
        },
        "trial_dir": str(trial_dir)
    }


_PBO_DEFAULT_MIN_CONFIGS = 10
_PBO_DEFAULT_N_GROUPS = 12
_PBO_MIN_GROUPS = 8


_PBO_DEFAULT_CLUSTER_THRESHOLD = 0.99


def _group_split_metric(arr, boundaries) -> list:
    """Issue #683 — annualisierungsfreier PER-GRUPPEN-Sortino (Mittelwert / Downside-Deviation der
    Gruppen-Subserie) als CSCV-Split-Metrik, statt des rohen Gruppen-Mittelwerts (Pre-#683).

    Root-Cause (#683): zwischen Trades ist eine Config in Cash (Bar-Return ≈ 0) — die per-Bar-
    Return-Serie ist von Nullen dominiert. Der GRUPPEN-MITTELWERT dieser meist-flachen Serie macht
    den CSCV-Vergleich zu einem Timing-Lotto (welche Config war zufällig während der grössten
    Kursbewegung positioniert), nicht zu einem Parameter-Sensitivitäts-Test. Der Gruppen-Sortino
    normiert den Mittelwert auf die tatsächlich realisierte Abwärts-Streuung DIESER Gruppe — zwei
    Configs mit identischem Timing-Zufallstreffer, aber unterschiedlicher Streuung, werden dadurch
    unterscheidbar; eine Gruppe OHNE jeden Verlust-Bar behält den rohen Mittelwert (kein erfundener,
    unendlicher Sortino).

    Rein, deterministisch. Rückgabe: Liste der Split-Metrik-Werte je Gruppe (NaN für leere
    Gruppen — vom Aufrufer über den vorhandenen NaN-Ausschluss behandelt)."""
    import numpy as _np
    out = []
    for start, end in boundaries:
        if end <= start:
            out.append(float("nan"))
            continue
        seg = arr[start:end]
        mean_r = float(seg.mean())
        downside = seg[seg < 0.0]
        if downside.size == 0:
            out.append(mean_r)
            continue
        dd = float(_np.sqrt((downside ** 2).mean()))
        out.append(mean_r / dd if dd > 0.0 else mean_r)
    return out


def _study_pbo(study, *, tournament_cfg: dict | None = None,
               min_configs: int | None = None, n_groups: int | None = None) -> tuple[float | None, dict]:
    """Issue #663/#619/#683 — Probability of Backtest Overfitting (Bailey/López de Prado, CSCV) über
    die Study, auf einer EIGENEN, feineren CSCV-Partition der GEPOOLTEN OOS-Per-Perioden-Return-Serie
    jedes eligiblen Trials (S ≥ 8–16 kontiguierliche Gruppen) — NICHT mehr auf den 4
    Walk-Forward-Folds (Pre-#663-Verhalten).

    Root-Cause (#663): 4 Walk-Forward-Folds ⇒ nur ``C(4,2)=6`` CSCV-Pfade sind statistisch grob
    (Bailey/López de Prado empfehlen S≳10–16); bei regime-heterogenen Folds (z. B. Fold 1–3
    strukturell defunkt, Fold 4 tradeable) misst die CSCV faktisch *Regime-Transfer* zwischen
    Folds, nicht Overfit — PBO→1.0 selbst bei Configs mit identischem Rang-Profil, nur einem
    Level-Offset (Artefakt, keine echte Selektions-Überanpassung).

    Root-Cause (#683): die per-Bar-MtM-Return-Serie (``oos_period_returns``) ist zwischen Trades
    mit Nullen durchsetzt (Config in Cash) — der GRUPPEN-MITTELWERT dieser Serie misst faktisch
    Regime-TIMING (welche Config zufällig während der grössten Bewegung positioniert war), nicht
    Config-Robustheit; zusätzlich erzeugen Near-Duplicate-Configs (dichtes Suchraum-Grid)
    near-identische Return-Vektoren, wodurch die EFFEKTIVE Anzahl unabhängiger Strategien ≪ der
    nominellen Trial-Zahl liegt und die CSCV-Rangstatistik verzerrt.

    Fix (#683): (1) Split-Metrik ist der PER-GRUPPEN-SORTINO (``_group_split_metric`` — annualisierungs-
    frei, konsistent mit ``oos_fold_sortino_periods``, #665) statt des rohen Gruppen-Mittelwerts
    (``pbo_metric='group_sortino'``, vorher ``'period_return'``). (2) Near-Duplicate-Configs werden
    VOR der CSCV-Partitionierung auf effektiv-unabhängige Vertreter reduziert
    (``cpcv.cluster_effective_configs`` — Pearson-Korrelation der VOLLEN per-Perioden-Return-Serie
    ``> pbo_cluster_threshold``, Default 0.99).

    Rückgabe ``(pbo, telemetry)``. ``pbo`` ist ``None`` (kein PBO-Veto — das Punkt-/DSR-Gate bleibt
    maßgeblich), wenn zu wenige eligible Configs (``< min_configs``, Default ``pbo_min_configs``=10)
    ODER zu wenige Gruppen (``< 8``) vorliegen — statt eines rausch-getriebenen Hard-Stops.
    ``telemetry`` trägt ``pbo_n_groups``/``pbo_n_configs``/``pbo_n_configs_raw``/``pbo_metric`` für
    den Winner-Eintrag, IMMER gefüllt (auch wenn ``pbo is None``, zur Diagnose WARUM kein Urteil
    gefällt wurde). ``pbo_n_configs`` ist die EFFEKTIVE (nach Clustering) Config-Zahl;
    ``pbo_n_configs_raw`` die rohe, vor dem Clustering gezählte."""
    import numpy as _np
    import optuna

    tournament_cfg = tournament_cfg or {}
    if min_configs is None:
        min_configs = int(tournament_cfg.get("pbo_min_configs", _PBO_DEFAULT_MIN_CONFIGS))
    if n_groups is None:
        n_groups = int(tournament_cfg.get("pbo_n_groups", _PBO_DEFAULT_N_GROUPS))
    cluster_threshold = float(tournament_cfg.get("pbo_cluster_threshold", _PBO_DEFAULT_CLUSTER_THRESHOLD))

    rows = []
    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE or not t.user_attrs.get("oos_eligible"):
            continue
        rets = t.user_attrs.get("oos_period_returns") or []
        if rets:
            rows.append([float(x) for x in rets])

    telemetry = {
        "pbo_n_groups": 0, "pbo_n_configs": len(rows), "pbo_n_configs_raw": len(rows),
        "pbo_metric": "group_sortino",
    }
    if len(rows) < min_configs:
        return None, telemetry

    n_obs = min(len(r) for r in rows)

    from automation.optimizer.cpcv import (
        cpcv_group_boundaries, cpcv_paths, cluster_effective_configs,
        probability_of_backtest_overfitting)

    # Issue #683 — Near-Duplicate-Reduktion VOR der Gruppenbildung, auf der VOLLEN per-Perioden-
    # Return-Serie (höhere Auflösung als die grobe Gruppen-Matrix). ``pbo_n_configs_raw`` bleibt die
    # rohe (pre-Clustering) Zahl für die Diagnose, wie stark reduziert wurde.
    truncated_rows = [r[:n_obs] for r in rows]
    keep_idx = cluster_effective_configs(truncated_rows, threshold=cluster_threshold)
    rows = [rows[i] for i in keep_idx]
    telemetry["pbo_n_configs_raw"] = len(truncated_rows)
    telemetry["pbo_n_configs"] = len(rows)
    if len(rows) < 2:
        return None, telemetry

    # Kann nicht mehr Gruppen bilden als gepoolte Perioden-Beobachtungen vorliegen.
    n_groups_eff = min(int(n_groups), n_obs)
    if n_groups_eff < _PBO_MIN_GROUPS:
        telemetry["pbo_n_groups"] = n_groups_eff
        return None, telemetry

    boundaries = cpcv_group_boundaries(n_obs, n_groups_eff)

    group_returns = []
    for r in rows:
        arr = _np.asarray(r[:n_obs], dtype=float)
        group_returns.append(_group_split_metric(arr, boundaries))
    mat = _np.array(group_returns, dtype=float)   # (n_configs, n_groups)

    # Issue #663 — degenerierte (NaN, z. B. eine leere Gruppe) Config-Zeilen EXPLIZIT ausschliessen,
    # statt sie über den alten min/max-1e-12-Clamp in probability_of_backtest_overfitting lautlos
    # zu maskieren (die Zeile wäre sonst als reguläre "flache" Config in die CSCV eingegangen).
    valid_mask = ~_np.isnan(mat).any(axis=1)
    mat = mat[valid_mask]
    telemetry["pbo_n_groups"] = n_groups_eff
    telemetry["pbo_n_configs"] = int(mat.shape[0])
    if mat.shape[0] < 2:
        return None, telemetry

    k_test = max(1, n_groups_eff // 2)
    if not (0 < k_test < n_groups_eff):
        return None, telemetry
    is_rows, oos_rows = [], []
    for train, test in cpcv_paths(n_groups_eff, k_test):
        is_rows.append(mat[:, list(train)].mean(axis=1))    # IS-Perf je Config (Train-Gruppen)
        oos_rows.append(mat[:, list(test)].mean(axis=1))    # OOS-Perf je Config (Test-Gruppen)
    pbo = probability_of_backtest_overfitting(_np.array(is_rows), _np.array(oos_rows))
    if pbo != pbo:  # NaN-Guard (z. B. < 2 verbleibende Configs nach dem NaN-Ausschluss)
        return None, telemetry
    return float(pbo), telemetry


def _median_rank_index(values) -> int:
    """Issue #594 — Index des Laufs mit dem MEDIANEN Rang nach ``values`` (z. B. oos_total_return).

    Dokumentierte, benannte Aggregation mit expliziter Tie-Breaking-Regel (ersetzt das
    undokumentierte ``_lower_median_or_none``): aufsteigend nach ``value`` sortiert (``None`` ⇒ −inf,
    schlechtestmöglich), bei gerader Anzahl der UNTERE Median (konservativ, ``order[(n-1)//2]``). Der
    Rückgabe-Index adressiert EINEN real gelaufenen Backtest, dessen VOLLSTÄNDIGER, kohärenter
    Metrikvektor (nicht ein komponentenweiser Frankenstein-Median) in die Bewertung geht.
    Deterministisch (stabiler Sekundär-Sort nach Original-Index)."""
    n = len(values)
    if n == 0:
        raise ValueError("_median_rank_index: leere Werteliste")
    order = sorted(
        range(n),
        key=lambda i: (float("-inf") if values[i] is None else float(values[i]), i),
    )
    return order[(n - 1) // 2]


def _rescue_eligible_trials_under_any_arm_policy(study, tournament_cfg: dict) -> tuple[list, dict]:
    """Issue #668 — wenn die STATISCHE Eligibility-Selektion leer ist (die Study würde
    ``HOLDOUT_NO_ELIGIBLE_TRIALS`` auslösen), aber der Grund dafür AUSSCHLIESSLICH der strukturell
    unerreichbare ``min_win_rate``-OR-Arm war (#660), und ``any_arm_unreachable_policy`` ∈
    ``{'drop_arm', 'recalibrate'}`` konfiguriert ist, werden die BEREITS ABGESCHLOSSENEN Trials
    dieser Study retroaktiv unter der angepassten Policy neu bewertet — aus den bereits
    gestempelten ``oos_gate_deltas``/``oos_win_rate`` User-Attrs, OHNE einen Re-Backtest.

    Ein Trial gilt unter der Policy als eligible, wenn (a) jede ``eligible_requires_all``-Klausel,
    für die ein Delta gestempelt ist, weiterhin erfüllt war (Delta >= 0) UND (b) die
    ``eligible_requires_any``-Klausel unter der Policy erfüllt ist: der verbleibende Arm
    (``min_profit_factor``) reicht allein (Delta >= 0), ODER — nur bei ``'recalibrate'`` —
    ``oos_win_rate`` erreicht die symbol-spezifisch rekalibrierte Schwelle.

    Rückgabe ``(rescued_trials, decision)`` — ``rescued_trials`` absteigend nach ``t.value``
    sortiert (leer, wenn nichts zu retten ist); ``decision`` die ``resolve_any_arm_policy``-
    Rückgabe (für die Winner-Eintrag-Telemetrie, auch wenn nichts gerettet wurde)."""
    import optuna
    from automation.optimizer.reward import resolve_any_arm_policy

    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    live_win_rates = [t.user_attrs.get("oos_win_rate") for t in completed
                      if t.user_attrs.get("oos_win_rate") is not None]
    decision = resolve_any_arm_policy(tournament_cfg, {"min_win_rate": live_win_rates})
    if decision["policy"] == "warn":
        return [], decision
    if not decision["dropped_clauses"] and not decision["recalibrated_thresholds"]:
        return [], decision  # der Arm war ohnehin erreichbar ⇒ nichts zu retten

    any_conditions = tournament_cfg.get("eligible_requires_any") or []
    if "min_win_rate" not in any_conditions:
        return [], decision  # die Policy betrifft nur den konfigurierten min_win_rate-Arm

    recalibrated_wr = decision["recalibrated_thresholds"].get("oos_min_win_rate")
    require_all = tournament_cfg.get("eligible_requires_all") or []

    rescued = []
    for t in completed:
        if t.value is None:
            continue
        deltas = t.user_attrs.get("oos_gate_deltas") or {}
        all_ok = True
        for cond in require_all:
            key = cond if cond.startswith("oos_") else f"oos_{cond}"
            if key in deltas and deltas[key] < 0.0:
                all_ok = False
                break
        if not all_ok:
            continue

        pf_delta = deltas.get("oos_min_profit_factor")
        any_ok = pf_delta is not None and pf_delta >= 0.0
        if not any_ok and recalibrated_wr is not None:
            wr = t.user_attrs.get("oos_win_rate")
            any_ok = wr is not None and wr >= recalibrated_wr
        if any_ok:
            rescued.append(t)

    rescued.sort(key=lambda t: t.value, reverse=True)
    return rescued, decision


def _metrics_dict(m) -> dict:
    """Serialisierbare Teilmenge der Holdout-Metriken (analog confirm_on_holdout)."""
    return {
        "oos_sortino": m.oos_sortino,
        "oos_max_drawdown": m.oos_max_drawdown,
        "oos_evaluated": m.oos_evaluated,
        "oos_eligible": m.oos_eligible,
        "oos_total_trades": m.oos_total_trades,
        # Issue #832 Fix Punkt 2/3 (Katalog #828-#835, GitHub-Issue #751) — monetäre Holdout-
        # Kennzahlen, die summary_de.py Abschnitt 2 ("Monetäres Ergebnis") ausschliesslich aus dem
        # #742-Report-JSON lesen können muss (kein zweiter Datenzugriff auf tournament_result.json/
        # trial_dir). Dieselbe Single Source of Truth wie das restliche metrics_symbol-Dict —
        # TournamentMetrics parst sie bereits (parsing.py), sie waren nur bislang nicht Teil DIESER
        # kuratierten Teilmenge. ``getattr``-defensiv (analog ``oos_gate_deltas`` unten): manche
        # Aufrufer/Tests übergeben ein minimales Metrics-Double ohne diese Felder.
        "oos_total_return": getattr(m, "oos_total_return", None),
        "oos_expectancy": getattr(m, "oos_expectancy", None),
        # Issue #1031 (Katalog #866) — additive, nennerausreisser-robuste Expectancy-Telemetrie
        # neben dem (weiterhin unveraendert definierten) oos_expectancy.
        "oos_expectancy_capital_weighted": getattr(m, "oos_expectancy_capital_weighted", None),
        "oos_expectancy_winsorized": getattr(m, "oos_expectancy_winsorized", None),
        "oos_expectancy_outlier_count": getattr(m, "oos_expectancy_outlier_count", 0),
        "oos_expectancy_notional_degenerate_count": getattr(
            m, "oos_expectancy_notional_degenerate_count", 0),
        # Issue #1042 (Katalog #866) E-1/E-3 — Kosten-Stressband + CVaR/ES-Tail-Risiko, additiv
        # neben den unveraenderten Basis-Kennzahlen.
        "oos_expectancy_cost_stress_1_5x": getattr(m, "oos_expectancy_cost_stress_1_5x", None),
        "oos_expectancy_cost_stress_2x": getattr(m, "oos_expectancy_cost_stress_2x", None),
        "oos_cvar_95": getattr(m, "oos_cvar_95", None),
        "oos_es_99": getattr(m, "oos_es_99", None),
        "oos_win_rate": getattr(m, "oos_win_rate", None),
        "oos_profit_factor": getattr(m, "oos_profit_factor", None),
        # Issue #1004 (Katalog #858) — Zensur-Telemetrie neben dem (weiterhin gecappten) Wert.
        "oos_profit_factor_censored": bool(getattr(m, "oos_profit_factor_censored", False)),
        "oos_profit_factor_raw": getattr(m, "oos_profit_factor_raw", None),
        "oos_buyhold_return": getattr(m, "oos_buyhold_return", None),
        "oos_excess_return": getattr(m, "oos_excess_return", None),
        # Issue #850 — Anteil der Holdout-Fenster-Zeit mit offener Position, damit summary_de.py
        # Abschnitt 2.3 einen Excess-Return gegen einen fallenden Benchmark von echtem Alpha
        # unterscheiden kann.
        "oos_exposure_fraction": getattr(m, "oos_exposure_fraction", None),
        # Issue #786 — dieselbe Struktur wie ``oos_gate_deltas`` der OOS-Trials (der Holdout-
        # Backtest durchlaeuft denselben Aggregationspfad, ``parsing.TournamentMetrics`` parst sie
        # bereits), hier auf dem HOLDOUT-Fenster statt der OOS-Folds.
        "holdout_gate_deltas": dict(getattr(m, "oos_gate_deltas", None) or {}),
    }


def _active_holdout_gate_deltas(gate_deltas: dict | None, tournament_cfg: dict | None) -> dict:
    """Issue #1075 — filtert ein rohes ``holdout_gate_deltas``-Dict auf die aktuell AKTIVE
    Gate-Menge (``reward._active_gate_collinearity_keys`` — dieselbe Laufzeit-Quelle wie #1074s
    Binding-Attribution in ``report._split_near_miss_deltas``, KEINE zweite gepflegte Liste).
    Deltas zu inaktiven/deaktivierten Gates bleiben im ROHEN ``holdout_gate_deltas`` als Telemetrie
    erhalten, sind aber für die Attribution (``_holdout_binding_gate``/``_holdout_tightest_margin``)
    nicht mehr sichtbar — dieselbe #790-Garantie wie bei der OOS-seitigen Binding-Attribution.

    Fehlt ``tournament_cfg`` GANZ, oder trägt es keinen ``eligible_requires_all``-Schlüssel (Legacy-
    /Test-Aufrufer, die die aktive Gate-Menge nicht kennen), bleibt der Filter WIRKUNGSLOS (jedes
    numerische Delta gilt als aktiv) — bit-identisch zum Pre-#1075-Verhalten. Ein tatsächlich
    KONFIGURIERTER (auch leerer) ``eligible_requires_all``-Schlüssel wird dagegen ernst genommen."""
    if not gate_deltas:
        return {}
    numeric = {k: v for k, v in gate_deltas.items() if isinstance(v, (int, float))}
    if tournament_cfg is None or "eligible_requires_all" not in tournament_cfg:
        return numeric
    active_keys = set(_reward._active_gate_collinearity_keys(tournament_cfg))
    return {k: v for k, v in numeric.items() if k in active_keys}


def _holdout_binding_gate(gate_deltas: dict | None, tournament_cfg: dict | None = None) -> str | None:
    """Issue #786 — das Gate mit dem NEGATIVSTEN normierten Delta (das am staerksten verfehlte) ueber
    die tatsaechlich numerisch vorliegenden, AKTIVEN Eintraege von ``holdout_gate_deltas``. ``None``
    bei leerem/fehlendem Dict (kein Urteil moeglich).

    Issue #1075 (Pitfall #375-Klasse, dieselbe Ursache wie #1074) — normiert die Deltas
    (``reward.normalize_gate_deltas_for_binding``) VOR dem ``argmin``: ``oos_min_expectancy`` hat
    die kleinsten natürlichen Einheiten und gewann vorher IMMER, unabhängig davon, ob der Holdout
    tatsächlich an dieser Grösse scheiterte. Filtert zusätzlich auf die AKTIVE Gate-Menge
    (``_active_holdout_gate_deltas``) — ein seit #697 deaktiviertes Gate (z. B.
    ``oos_min_expectancy``, kein ``eligible_requires_all``-Mitglied mehr) kann nicht mehr
    "binden"."""
    active = _active_holdout_gate_deltas(gate_deltas, tournament_cfg)
    if not active:
        return None
    normalized = _reward.normalize_gate_deltas_for_binding(active, tournament_cfg or {})
    if not normalized:
        return None
    return min(normalized, key=lambda k: normalized[k])


def _holdout_tightest_margin(gate_deltas: dict | None, tournament_cfg: dict | None = None) -> dict | None:
    """Issue #1075 — die semantisch EHRLICHE Aussage für einen BESTANDENEN Holdout: kein Gate
    "bindet" (nichts ist gescheitert), aber eines lag am nächsten an der Schwelle. Rückgabe
    ``{'gate': ..., 'margin_normalized': ...}`` — das Gate mit dem KLEINSTEN normierten Delta ÜBER
    ALLEN nicht-negativen (bestandenen) aktiven Deltas. ``None``, wenn keine aktiven,
    numerisch positiven Deltas vorliegen (z. B. alles fehlt)."""
    active = _active_holdout_gate_deltas(gate_deltas, tournament_cfg)
    if not active:
        return None
    normalized = _reward.normalize_gate_deltas_for_binding(active, tournament_cfg or {})
    passing = {k: v for k, v in normalized.items() if v >= 0.0}
    if not passing:
        return None
    tightest = min(passing, key=lambda k: passing[k])
    return {"gate": tightest, "margin_normalized": round(passing[tightest], 4)}


def _holdout_metrics_for_params(strategy: str, symbol: str, params: dict,
                                *, run_backtest=run_backtest, build_trial=build_trial,
                                catalog_newest_ns: int | None = None):
    """Führt einen Holdout-Backtest für genau `symbol` mit `params` aus und parst die Metriken.

    Wie confirm_on_holdout (holdout_days=0, n_folds=1, oos_window_days_override=holdout_days),
    aber single-symbol (instruments=[symbol]) und mit beliebigem Param-Vektor — so lassen sich
    der symbol-getunte und der globale Vektor auf demselben, nie-optimierten Holdout vergleichen.
    """
    cfg_dir = config_dir()
    seed = 42
    optimizer_path = cfg_dir / "optimizer.json"
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            seed = (json.load(f) or {}).get("seed", 42)

    holdout_days_cfg = 45
    backtest_path = cfg_dir / "backtest.json"
    if backtest_path.exists():
        with open(backtest_path, "r", encoding="utf-8") as f:
            holdout_days_cfg = (json.load(f) or {}).get("walk_forward", {}).get("holdout_days", 45)

    # Deterministischer Discriminator, damit symbol- und global-Lauf nicht in dasselbe trial_dir schreiben.
    tag = hashlib.sha1(json.dumps(params or {}, sort_keys=True, default=str).encode()).hexdigest()[:8]
    study_name = f"confirm_{strategy}_{symbol.replace('.', '_')}_{tag}"

    # Issue #796 — EINE eingefrorene Config statt einer Pro-Trial-Kopie (der Studyname ist per
    # Param-Hash eindeutig, ein einmaliges Freeze genuegt).
    holdout_wf_settings = resolve_wf_settings(
        cfg_dir, holdout_days=0, n_folds=1, oos_window_days_override=holdout_days_cfg)
    holdout_cfg_dir = freeze_study_config(study_name, holdout_wf_settings, base_cfg=cfg_dir)

    trial_dir, manifest_path = build_trial(
        strategy_class=strategy,
        sampled=params,
        study_name=study_name,
        trial_number=0,
        seed=seed,
        holdout_days=0,
        n_folds=1,
        oos_window_days_override=holdout_days_cfg,
        instruments=[symbol],
        catalog_newest_ns=catalog_newest_ns,
        copy_config=False,
        study_config_dir=holdout_cfg_dir,
    )
    output_path = run_backtest(trial_dir, manifest_path, config_dir=holdout_cfg_dir)
    m = parse_tournament(output_path)
    # Issue #615 — die trial_dir-Identität AN die Metriken heften (kein Signatur-Bruch ⇒ bestehende
    # Mocks von _holdout_metrics_for_params bleiben gültig). Macht die Kohärenz-Invariante
    # (symbol_params/R_symbol/holdout_passed stammen aus DEMSELBEN trial_dir) nachweisbar.
    try:
        m.holdout_trial_dir = str(trial_dir)
    except Exception:
        pass
    return m



def confirm_per_symbol_promotion(study, strategy: str, symbol: str, global_params: dict,
                                 *, run_backtest=run_backtest, build_trial=build_trial,
                                 catalog_newest_ns: int | None = None,
                                 deflation_n_family: int | None = None,
                                 deflation_family_period_returns: list | None = None,
                                 deflation_n_family_excluded_no_statistic: dict | None = None,
                                 study_invariant_results: list[dict] | None = None,
                                 run_id: str | None = None) -> dict:
    """Gate 3 — das entscheidende Per-Symbol-Promotion-Gate.

    Ein instrument_override wird nur promotet, wenn der symbol-getunte Vektor auf dem
    ungesehenen Holdout (a) das Holdout-Gate selbst besteht UND (b) das globale Baseline um
    `promotion_margin` (optimizer.json) schlägt.

    ``deflation_n_family`` (Issue #652) — die familienweite Multiple-Testing-Multiplizität (Σ
    eligibler Trials über ALLE Strategien-Studies desselben Symbols, VOR dieser Promotion bekannt).
    Die Selektion wählt den besten von mehreren Strategien-Studies je Symbol; das per-Study N
    (``deflation_n``, weiterhin als Telemetrie erhalten) unterschätzt die tatsächliche Anzahl
    „Schüsse aufs Tor" der Gesamt-Selektion. ``None``/fehlend ⇒ bit-identisch zum Pre-#652-Verhalten
    (per-Study-N, Legacy-Aufrufer wie Unit-Tests bleiben unverändert grün).

    ``deflation_family_period_returns`` (Issue #695) — die ROHE Familien-Zahl (``deflation_n_family``)
    ist eine un-declusterte Summe eligibler Trials über ALLE Studies: near-identische Configs (z. B.
    aus einem dichten Optuna-Suchraum-Grid) zählen darin als ebenso viele „unabhängige Schüsse aufs
    Tor" wie tatsächlich unabhängige Parametrisierungen — derselbe Root-Cause, den der PBO-Pfad
    (``_study_pbo``) bereits je Study via ``cpcv.cluster_effective_configs`` korrigiert. Ist diese
    Liste (Issue #813 — je ``oos_evaluated`` Trial ALLER Studies des Symbols dessen
    ``oos_period_returns``, seit #813 NICHT mehr nur je eligiblem Trial — siehe
    ``sweep._family_period_returns_from_studies``, dieselbe erweiterte Menge wie ``deflation_n_
    family`` [#784] selbst; die Coverage-Lücke [``deflation_cluster_coverage``, #813] zwischen einer
    ELIGIBLE-only- und einer EVALUATED-Stempelung war Root-Cause einer systematischen Über-Deflation)
    übergeben, wird sie VOR der SR₀-Berechnung auf die Pearson-Korrelation ihrer vollen Return-Serie
    (Schwelle ``pbo_cluster_threshold``) reduziert ⇒ ``deflation_n_family_effective`` (declusterte
    Familienzahl), die ANSTELLE der rohen Zahl das ``E[max_N]`` in ``sr0_multiple_testing_robust``
    speist. Fehlt die Liste (Legacy-Aufrufer/Unit-Tests, die nur den Skalar ``deflation_n_family``
    kennen) ⇒ ``deflation_n_family_effective == deflation_n_family_raw`` (kein Clustering möglich,
    bit-identisch zum Pre-#695-Verhalten). Die
    #652-Invariante bleibt gewahrt: ``deflation_n_effective = max(deflation_n, deflation_n_family_
    effective)`` unterschreitet NIE das lokal bekannte per-Study-N.

    **Verbindliche Design-Entscheidung:** Der Vergleichs-Score ist die *rohe* risikoadjustierte
    Performance — `compute_reward(..., universe_size=1)` OHNE `sampled`/`global_params` ⇒
    `param_pen = 0`. param_pen ist ein Such-Regularisierer, kein Performance-Maß, und würde den
    fairen Edge-Test verzerren.

    Rückfrage-Klärung: Fällt der globale Vektor im Holdout durch das Risk-Cap, liefert
    compute_reward dennoch einen (entsprechend niedrigen) endlichen R_global; ein symbol-getunter
    Vektor, der das Holdout-Gate selbst besteht und R_global + Marge schlägt, gilt damit als Edge.
    Issue #655 — erzeugt der globale Vektor auf DIESEM Symbol/Holdout NIE OOS-Trades (strukturell
    ungeeignet, z. B. #656), ist ``R_global`` KEINE Zahl mehr (``None`` statt des alten −20-Sentinels)
    — es gibt dann keine Baseline zu schlagen, die R-Edge-Bedingung gilt trivial als erfüllt (siehe
    unten).

    status ∈ {'READY_FOR_PR', 'REJECTED_NO_EDGE_OVER_GLOBAL', 'REJECTED_ON_HOLDOUT'}.

    ``study_invariant_results`` (Issue #1007, Katalog #858) — vorberechnete ``InvariantResult.
    to_dict()``-Einträge (``name``/``passed``/``severity``/...), deren Scope DIESE Study betrifft.
    VOR diesem Fix waren Invarianten ein reiner Report-Nachlauf (``report.generate_sweep_report``)
    UND ein Sweep-Abbruchkriterium (``fail_fast_invariants``), aber KEIN Eingang in diese Funktion —
    eine Study konnte gleichzeitig als informationsfrei (``severity='blocking'``-FAIL) markiert UND
    als Gewinner exportiert werden. Jeder Eintrag mit ``severity == 'blocking'`` und
    ``passed is False`` löst ``REJECT_STUDY_INVARIANT_BLOCKING`` aus — ein unbedingter Hard-Stop wie
    ``REJECT_HOLDOUT_GATE`` (kein Ersatzpfad, auch nicht ``promotion_correction_mode=
    'dsr_or_robust_pair'``, kann ihn unterlaufen). Scope-Regel (wie bei ``fail_fast_invariants``/
    ``build_probe_report`` bereits etabliert — ``InvariantResult`` selbst trägt kein eigenes
    ``scope``-Feld): der AUFRUFER filtert die Liste auf das, was für DIESE Study gilt — eine
    global-scoped Invariante (z. B. ``check_config_key_registry``) betrifft jede Study des Laufs,
    sofern ihre ``detail``-Meldung nicht selbst eine engere Teilmenge benennt. ``None``/leer (Default,
    Legacy-Aufrufer/Unit-Tests) ⇒ bit-identisches Verhalten zu vorher (kein Zusatz-Veto)."""
    # Issue #773 — eine Study, deren #756-Renditeserien-Identitaet (run_optimization.check_
    # study_coherence_violation_rate) fail-loud verletzt wurde, wird NICHT promotet — der
    # Kohaerenz-Defekt kontaminiert den gesamten Deflations-/Bootstrap-/PBO-Pfad (#771), ein
    # Confirm-Lauf darauf waere ein kontaminiertes Urteil.
    if bool((getattr(study, "user_attrs", None) or {}).get("coherence_violation_rate_exceeded")):
        import logging as _logging
        emit_execution_event(_logging.getLogger("optimizer"), "STUDY_REJECTED_ON_COHERENCE_VIOLATION", {
            "symbol": symbol, "strategy": strategy,
        })
        return {
            "promote": False,
            "status": "REJECTED_ON_HOLDOUT",
            "is_rejection_detail_override": "REJECT_COHERENCE_VIOLATION",
            "promotion_route": None,
            "symbol_params": {},
            "R_symbol": 0.0,
            "R_global": None,
            "promotion_margin": 0.0,
            "holdout_passed": False,
            "trial_dir": None,
            "metrics_symbol": {},
            "metrics_global": {},
        }

    cfg_dir = config_dir()
    backtest_path = cfg_dir / "backtest.json"
    wf_cfg = {}
    if backtest_path.exists():
        with open(backtest_path, "r", encoding="utf-8") as f:
            wf_cfg = (json.load(f) or {}).get("walk_forward", {})

    is_window_days = wf_cfg["is_window_days"]
    holdout_days = wf_cfg["holdout_days"]
    oos_window_days_cfg = wf_cfg["oos_window_days"]
    n_folds = wf_cfg["splits"]
    embargo_period_days = wf_cfg["embargo_period_days"]

    if catalog_newest_ns is not None:
        now = dt.datetime.now(dt.timezone.utc)
        from automation.optimizer.trial_config import compute_walk_forward_window
        # Issue #548 — der Embargo MUSS auch hier im Fenster-Span reserviert werden (dieselbe
        # Single-Source-of-Truth-Funktion wie build_trial), sonst divergiert die Reachability-
        # Geometrie von der real gebauten Holdout-Geometrie. Mit reserviertem Embargo gilt
        # ``oos_lo_ns == end`` (die exakte äussere Fenster-Grenze), konsistent zu #548.
        window_start, _ = compute_walk_forward_window(
            now=now,
            holdout_days=holdout_days,
            is_window_days=is_window_days,
            oos_window_days=oos_window_days_cfg,
            n_folds=n_folds,
            embargo_period_days=embargo_period_days,
            catalog_newest_ns=catalog_newest_ns,
        )
        oos_lo_ns = int((window_start + dt.timedelta(days=is_window_days + (n_folds * oos_window_days_cfg) + embargo_period_days)).timestamp() * 1_000_000_000)
        # verfügbar, sobald catalog_newest_ns >= holdout_oos_start_ns
        if catalog_newest_ns < oos_lo_ns:
            return {
                "promote": False,
                "status": "REJECTED_ON_HOLDOUT",
                "is_rejection_detail_override": "REJECT_HOLDOUT_UNREACHABLE",
                "symbol_params": {},
                "R_symbol": 0.0,
                "R_global": 0.0,
                "promotion_margin": 0.0,
                "metrics_symbol": {},
                "metrics_global": {}
            }

    cfg_dir = config_dir()
    risk_dd_cap = 0.30
    tournament_path = cfg_dir / "tournament.json"
    _early_tournament_cfg: dict = {}
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            _early_tournament_cfg = json.load(f) or {}
            risk_dd_cap = _early_tournament_cfg.get("max_drawdown", 0.30)

    # Issue #839/#857 — eine Study, deren Anteil zeitbox-verletzender Trials den Study-
    # Toleranzwert überschreitet, wird VOR jedem statistischen Gate verworfen (und VOR dem teuren
    # Holdout-Backtest des globalen Baseline-Vektors weiter unten) — bei DIESEM Anteil ist der
    # Exit-Pfad selbst defekt (Bug, #836/#837), nicht mehr nur eine tolerierbare Ausführungslatenz
    # einzelner Trials (die werden seit #857 bereits JE TRIAL ausgeschlossen — siehe
    # run_optimization.make_symbol_objective —, bevor sie hier überhaupt ankommen; die einzelnen
    # verletzenden Trials tragen ``oos_evaluated=False``/``oos_invalid_reason='TIMEBOX_VIOLATION'``
    # und kontaminieren ihre sauberen Geschwister nicht mehr, Pitfall #272). Issue #858 —
    # ``timebox_violation_study_tolerance`` (Default 0.25) ist DEUTLICH lockerer als die frühere
    # ``timebox_violation_tolerance=0.0``: dieser Anteil ist kein Ausführungsrauschen mehr, sondern
    # der Nachweis eines strukturell defekten Exit-Pfads (dieselbe Schwelle wie
    # ``invariants.check_holding_time_cap``, #861-Unifikation).
    import logging as _logging_early
    _timebox_study_tolerance = float(
        _early_tournament_cfg.get("timebox_violation_study_tolerance", 0.25))
    _timebox_trial_attrs = [dict(getattr(t, "user_attrs", {}) or {}) for t in (getattr(study, "trials", None) or [])]
    # Issue #902 — bar_seconds ist Pflichtparameter; #900s per-Symbol median_delta_t_s ist an dieser
    # Stelle (Confirm läuft je Study, nicht mit dem Sweep-Preflight verdrahtet) nicht verfügbar —
    # derselbe dokumentierte 1h-Bar-Default wie report.py, jetzt aus der EINEN Quelle.
    _timebox = _inv.compute_trial_timebox_violations(
        _timebox_trial_attrs, strategy=strategy, bar_seconds=_contracts.BAR_SECONDS_DEFAULT)
    # Issue #903 Fix 2 — die #878-Study-Toleranz wirkt auf der ROUND-TRIP-(Trade-)Ebene, nicht auf
    # der Trial-Ebene: eine Study, in der ein kleiner Anteil der TRADES die Box überschreitet, ist
    # kein defekter Exit-Pfad, auch wenn ein grosser Anteil der TRIALS davon berührt ist (jeder
    # Trial hat viele Trades — ein einziger Ausreisser-Trade genügt, um den ganzen Trial als
    # "trial-violating" zu markieren).
    if _timebox["timebox_round_trip_violation_fraction"] > _timebox_study_tolerance:
        emit_execution_event(_logging_early.getLogger("optimizer"), "STUDY_REJECTED_ON_TIMEBOX_VIOLATION", {
            "symbol": symbol, "strategy": strategy,
            "timebox_violation_fraction": _timebox["timebox_violation_fraction"],
            "timebox_violating_trials": _timebox["timebox_violating_trials"],
            "timebox_evaluated_trials": _timebox["timebox_evaluated_trials"],
            # Issue #903 Fix 1/4 — Round-Trip-(Trade-)Ebene, die tatsächliche Entscheidungsgrundlage
            # oben. ``timebox_trials_invalidated`` entfällt (war wertgleich mit
            # ``timebox_violation_trades`` unter zweitem Namen, #903 Symptom).
            "timebox_violating_round_trips": _timebox["timebox_violating_round_trips"],
            "timebox_evaluated_round_trips": _timebox["timebox_evaluated_round_trips"],
            "timebox_round_trip_violation_fraction": _timebox["timebox_round_trip_violation_fraction"],
            "timebox_violation_study_tolerance": _timebox_study_tolerance,
        }, level=_logging_early.ERROR)
        return {
            "promote": False,
            "status": "REJECTED_ON_HOLDOUT",
            "is_rejection_detail_override": "REJECT_INVALID_TIMEBOX",
            "promotion_route": None,
            "symbol_params": {},
            "R_symbol": 0.0,
            "R_global": None,
            "promotion_margin": 0.0,
            "holdout_passed": False,
            "trial_dir": None,
            "metrics_symbol": {},
            "metrics_global": {},
        }

    promotion_margin = 0.10
    optimizer_path = cfg_dir / "optimizer.json"
    global_weights = None
    if optimizer_path.exists():
        with open(optimizer_path, "r", encoding="utf-8") as f:
            global_weights = json.load(f)
            promotion_margin = global_weights.get("promotion_margin", 0.10)
            global_weights["reward_mode"] = "auto"
    # Issue #533 — oos_sortino_fallback-Parität zu reward.py / confirm_on_holdout (Zero-Hardcoding).
    oos_sortino_fallback = global_weights.get("oos_sortino_fallback") if global_weights else None

    m_global = _holdout_metrics_for_params(strategy, symbol, global_params,
                                           run_backtest=run_backtest, build_trial=build_trial,
                                           catalog_newest_ns=catalog_newest_ns)
    # Issue #594 — Holdout-Reward über denselben Codepfad mit holdout=True (IS-Terme abgeschaltet,
    # kein Platzhalter). Study-Reward und Holdout-Reward laufen damit nachweislich über compute_reward.
    R_global = (compute_reward(m_global, universe_size=1, weights=global_weights, holdout=True)
                if global_weights else compute_reward(m_global, universe_size=1, holdout=True))

    import optuna
    import logging

    # Tournament cfg (für Top-k + Deflation) — VOR der Eligible-Selektion laden (top_k wird bereits im
    # Leer-Fall gebraucht). ``holdout_top_k`` ist in tournament.json deklariert (Zero-Hardcoding, #615).
    tournament_cfg = {}
    if tournament_path.exists():
        with open(tournament_path, "r", encoding="utf-8") as f:
            tournament_cfg = json.load(f) or {}
    top_k = int(tournament_cfg.get("holdout_top_k", 5))

    # 1. Top-k-Holdout-Selektion (Issue #576/#615). Filter auf die BEREITS GESTEMPELTE OOS-Eligibility
    # (run_optimization.make_symbol_objective, #615) — NICHT auf ``is_rejection_detail is None``: der
    # gestempelte Wert ist der STRING "NONE" (IS_REJECTION_NONE), nie Python-``None`` ⇒ der alte Filter
    # war in JEDER Study leer ⇒ Top-k (#576) lief nie (faktisch k=1) und der Median-Vektor (#594) war
    # inert (Index immer 0). ``oos_eligible`` ist die kohärente, direkt filterbare Grösse (identisch zu
    # ``is_rejection_detail == IS_REJECTION_NONE``). ``t.value`` ist der (korrigierte) Reward.
    eligible_trials = [t for t in study.trials
                       if t.state == optuna.trial.TrialState.COMPLETE
                       and t.user_attrs.get("oos_eligible")
                       and t.value is not None]
    eligible_trials.sort(key=lambda t: t.value, reverse=True)

    # Issue #615 — FAIL-LOUD statt stillem Floor-Trial-Fallback. Keine eligiblen Trials ⇒ strukturiertes
    # HOLDOUT_NO_ELIGIBLE_TRIALS-Event + Rejection; es wandert KEIN Floor-Trial (argmax reward über ALLE
    # Trials, evtl. ein evaluable_reward_floor-Trial) unbemerkt in den Holdout. Der frühere
    # ``study.best_trial``-Fallback promotete im Leer-Fall genau solche nie-validierten Parameter.
    #
    # Issue #668 — BEVOR endgültig REJECTED wird: ist die statische Leere ausschliesslich dem
    # strukturell unerreichbaren ``min_win_rate``-OR-Arm geschuldet (#660) UND
    # ``any_arm_unreachable_policy`` explizit auf 'drop_arm'/'recalibrate' konfiguriert, werden die
    # bereits abgeschlossenen Trials retroaktiv unter der angepassten Policy neu bewertet (KEIN
    # Re-Backtest) — die Reduktion/Rekalibrierung wird EXPLIZIT im Winner-Eintrag telemetriert,
    # statt den OR-Arm lautlos auf ein reines PF-Gate kollabieren zu lassen (#668-Root-Cause).
    any_arm_decision = None
    if not eligible_trials:
        any_arm_decision = None
        if tournament_cfg.get("any_arm_unreachable_policy") in ("drop_arm", "recalibrate"):
            rescued, any_arm_decision = _rescue_eligible_trials_under_any_arm_policy(study, tournament_cfg)
            if rescued:
                eligible_trials = rescued
                logging.getLogger("optimizer").warning(
                    f"[#668] {symbol}: HOLDOUT_NO_ELIGIBLE_TRIALS unter dem statischen Gate — "
                    f"any_arm_unreachable_policy={any_arm_decision['policy']} rettet "
                    f"{len(rescued)} Trial(s) (dropped={any_arm_decision['dropped_clauses']}, "
                    f"recalibrated={any_arm_decision['recalibrated_thresholds']})."
                )

    if not eligible_trials:
        # Issue #682 — bevor endgültig REJECTED wird: ein global-viabler Default ohne
        # symbol-eligiblen Trial darf nicht lautlos als HOLDOUT_NO_ELIGIBLE_TRIALS enden. m_global/
        # R_global (oben bereits berechnet — der GLOBALE Parametervektor auf GENAU DIESEM
        # Symbol-Holdout) sind eine legitime, wenn auch UNGETUNTE Deployment-Route: besteht der
        # globale Default das Symbol-Holdout-Gate SELBST und ist sein Reward positiv, gibt es
        # nichts Symbol-Spezifisches zu vergleichen (keine eligiblen Trials ⇒ auch kein
        # Micro-Tuning-Anspruch) — aber eine reale, testbare Kante auf diesem Symbol. Root-Cause
        # (#565/#682): der Per-Symbol-Pfad verlangte bislang einen symbol-eligiblen Trial als
        # ZWINGENDE Voraussetzung, auch wenn der globale Vektor selbst längst eligible war.
        #
        # Issue #783 — DREI Haertungen gegen den #742-Befund (alle 37 promoteten Studies dieser
        # Route hatten n_eligible=0 UND einen VORZEITIGEN #768-Plateau-Abbruch, ununterscheidbar
        # von einer holdout-validierten Promotion im Report):
        # (1) EIGENER Status ('PROMOTE_GLOBAL_DEFAULT', nicht 'READY_FOR_PR' — das Label bleibt
        #     ausschliesslich fuer holdout-validierte Symbol-Kandidaten reserviert, AGENTS.md
        #     Pitfall #234).
        # (2) Budget-Vorbedingung: ein Plateau-Abbruch (#768/#769) darf keine Deployment-
        #     Entscheidung ausloesen — die Route ist nur zulaessig, wenn budget_executed_fraction
        #     ueber der deklarativen Schwelle liegt; sonst greift der reguläre
        #     HOLDOUT_NO_ELIGIBLE_TRIALS-Pfad unveraendert.
        # (3) Deflation: R_global durchlaeuft dieselbe DSR-Kette wie ein symbolgetunter Kandidat.
        #     Ohne lokale eligible-Trial-Kohorte fuer DIESE Study faellt die Shrinkage-Gewichtung
        #     vollstaendig auf die theoretische Lo-2002-Referenz zurueck (variance_n_trials=0 ⇒
        #     λ=1) — dieselbe Funktion (sr0_multiple_testing_robust), dieselbe Formel wie der
        #     reguläre Pfad, nur ohne empirische Kohortenvarianz. Multiplizitaet = familienweite
        #     Zahl (der globale Vektor wurde ueber das GESAMTE Universum selektiert, mindestens so
        #     gross wie eine Per-Symbol-Suche, nicht null).
        budget_execution = None
        try:
            from automation.optimizer.run_optimization import compute_budget_execution as _cbe
            _study_attrs = getattr(study, "user_attrs", None) or {}
            budget_execution = _cbe(
                list(getattr(study, "trials", None) or []),
                n_trials_budget=_study_attrs.get("n_trials_budget"),
                n_startup_trials=_study_attrs.get("n_startup_trials"),
                study_user_attrs=_study_attrs,
                # Issue #1027 (Katalog #866) — vorher fehlte ``run_id`` an dieser Aufrufstelle: die
                # PROMOTE_GLOBAL_DEFAULT-Budget-Vorbedingung (unten, ``min_budget_for_global_default``)
                # entschied auf der UNGEFILTERTEN Study-Historie, waehrend Report und Invariante
                # (``report.py``/``run_optimization.py:2356``, beide seit #1015 gefiltert) eine ANDERE
                # Zahl derselben Kennzahl sahen — exakt die Konstellation, die #670 verhindern sollte.
                run_id=run_id,
            )
        except Exception:
            budget_execution = None
        min_budget_for_global_default = float(
            (global_weights or {}).get("global_default_promotion_min_budget_execution", 0.9))
        budget_sufficient = bool(
            budget_execution and budget_execution.get("budget_executed_fraction") is not None
            and budget_execution["budget_executed_fraction"] >= min_budget_for_global_default)

        global_default_promotable = bool(
            budget_sufficient
            and _holdout_gate_passed(
                m_global, risk_dd_cap,
                sortino_fallback_enabled=(oos_sortino_fallback == "total_return"),
            )
            and R_global is not None and R_global > 0.0
        )
        g_sr0 = g_dsr = g_dsr_z = None
        if global_default_promotable:
            # Issue #783 (3) — Deflation auf R_global mit familienweiter Multiplizitaet, voll
            # theoretischer Varianz-Referenz (keine lokale Kohorte fuer diese Study).
            from automation.optimizer.deflation import (
                sr0_multiple_testing_robust, bootstrap_psr_z, psr_from_z, psr_z, deflated_sharpe_ratio)
            g_deflated_selection = bool(tournament_cfg.get("deflated_selection", False))
            g_deflation_min_cohort = int(tournament_cfg.get("deflation_min_cohort", 10))
            g_deflation_confidence = float(tournament_cfg.get("deflation_confidence", 0.95))
            g_inference_method = None
            # Issue #887 — die EINE Quelle fuer N: der globale Default hat an der Stufe-1-Selektion
            # NICHT teilgenommen (route='global_default'), N=1, nicht die volle Familiengroesse.
            g_n_family, g_multiplicity_rationale = resolve_promotion_multiplicity(
                "global_default", deflation_n_family=deflation_n_family)
            g_sr_period = getattr(m_global, "oos_sortino_period", None)
            g_n_periods = getattr(m_global, "oos_n_periods", None)
            if g_deflated_selection and g_n_family > 0 and g_sr_period is not None and g_n_periods:
                g_sr0, _, _, _ = sr0_multiple_testing_robust(
                    0.0, g_n_family, min_cohort=g_deflation_min_cohort,
                    n_periods=int(g_n_periods), variance_n_trials=0)
                g_period_returns = getattr(m_global, "oos_period_returns", None) or ()
                if len(g_period_returns) >= 5:
                    g_dsr_z, _ = bootstrap_psr_z(
                        g_period_returns, sr_star=g_sr0,
                        n_boot=int(tournament_cfg.get("psr_bootstrap_resamples", 200)))
                    g_dsr = psr_from_z(g_dsr_z)
                    g_inference_method = "stationary_bootstrap"
                else:
                    g_ret_skew = getattr(m_global, "oos_ret_skew", 0.0)
                    g_ret_kurtosis = getattr(m_global, "oos_ret_kurtosis", 3.0)
                    g_dsr = deflated_sharpe_ratio(
                        g_sr_period, int(g_n_periods), sr0=g_sr0,
                        skew=g_ret_skew, kurtosis=g_ret_kurtosis)
                    g_dsr_z = psr_z(g_sr_period, int(g_n_periods),
                                   skew=g_ret_skew, kurtosis=g_ret_kurtosis, sr_star=g_sr0)
                    g_inference_method = "sharpe_formula_fallback"
                if g_dsr is not None and g_dsr < g_deflation_confidence:
                    logging.getLogger("optimizer").warning(
                        f"[#783] {symbol}/{strategy}: PROMOTE_GLOBAL_DEFAULT-Kandidat scheitert an "
                        f"der Deflation (DSR={g_dsr:.4f} < {g_deflation_confidence}, "
                        f"N_family={g_n_family}) ⇒ HOLDOUT_NO_ELIGIBLE_TRIALS bleibt bestehen."
                    )
                    global_default_promotable = False
            # Issue #887 Fix Punkt 3 — die Lockerung auf N=1 darf keine Hintertuer werden: ein
            # globaler Default, der die Deflation nur deshalb passiert, weil seine Multiplizitaet 1
            # ist, muss ZUSAETZLICH einen materiellen Holdout-Sortino nachweisen (annualisiert,
            # dieselbe Groesse wie das Holdout-Gate selbst, _holdout_gate_passed).
            if global_default_promotable:
                g_min_holdout_sortino = float(
                    tournament_cfg.get("global_default_min_holdout_sortino", 0.5))
                g_holdout_sortino = getattr(m_global, "oos_sortino", None)
                if g_holdout_sortino is None or g_holdout_sortino < g_min_holdout_sortino:
                    logging.getLogger("optimizer").warning(
                        f"[#887] {symbol}/{strategy}: PROMOTE_GLOBAL_DEFAULT-Kandidat scheitert an "
                        f"global_default_min_holdout_sortino (Holdout-Sortino="
                        f"{g_holdout_sortino!r} < {g_min_holdout_sortino}) — die gelockerte "
                        f"Multiplizitaet (N=1) ersetzt keine materielle Qualitaetsschwelle."
                    )
                    global_default_promotable = False

        if global_default_promotable:
            emit_execution_event(logging.getLogger("optimizer"), "PROMOTE_GLOBAL_DEFAULT_ON_SYMBOL", {
                "symbol": symbol,
                "strategy": strategy,
                "R_global": R_global,
                "n_trials": len(getattr(study, "trials", []) or []),
                "budget_executed_fraction": budget_execution.get("budget_executed_fraction") if budget_execution else None,
            })
            logging.getLogger("optimizer").info(
                f"[#682/#783] {symbol}/{strategy}: kein symbol-eligibler Trial, aber der GLOBALE "
                f"Default besteht das Symbol-Holdout-Gate selbst (R_global={R_global:.4f} > 0, "
                f"budget_executed_fraction={budget_execution.get('budget_executed_fraction') if budget_execution else None}) "
                f"⇒ PROMOTE_GLOBAL_DEFAULT (R_symbol := R_global, kein Micro-Tuning-Anspruch — "
                f"EXPLIZITE Entscheidung, EIGENER Status statt READY_FOR_PR)."
            )
            metrics_global_out = _metrics_dict(m_global)
            # Issue #1075 (Pitfall #375-Klasse) — dieser Zweig wird NUR erreicht, wenn der globale
            # Default das Holdout-Gate bereits bestanden hat (global_default_promotable) — kein
            # Gate hat "gebunden" (nichts ist gescheitert). holdout_binding_gate bleibt None;
            # holdout_tightest_margin ist die semantisch ehrliche Aussage (welches Gate am
            # nächsten an der Schwelle lag).
            metrics_global_out["holdout_binding_gate"] = None
            metrics_global_out["holdout_tightest_margin"] = _holdout_tightest_margin(
                metrics_global_out["holdout_gate_deltas"], tournament_cfg)
            if g_sr0 is not None:
                metrics_global_out["deflated_sr0"] = g_sr0
                metrics_global_out["deflated_dsr"] = g_dsr
                metrics_global_out["deflation_dsr_z"] = g_dsr_z
                metrics_global_out["deflation_inference_method"] = g_inference_method
                metrics_global_out["deflation_n_family"] = g_n_family
                # Issue #887 Fix Punkt 4 — ohne dieses Feld ist die Multiplizitaets-Entscheidung im
                # Nachhinein nicht ueberpruefbar (#847-Klasse).
                metrics_global_out["deflation_multiplicity_rationale"] = g_multiplicity_rationale
            return {
                "promote": True,
                "status": "PROMOTE_GLOBAL_DEFAULT",
                "is_rejection_detail_override": None,
                "promotion_route": "global_default_on_symbol",
                "symbol_params": dict(global_params),
                "R_symbol": R_global,
                "R_global": R_global,
                "promotion_margin": promotion_margin,
                "holdout_passed": True,
                "trial_dir": None,
                "budget_executed_fraction": budget_execution.get("budget_executed_fraction") if budget_execution else None,
                "metrics_symbol": metrics_global_out,
                "metrics_global": _metrics_dict(m_global),
            }

        emit_execution_event(logging.getLogger("optimizer"), "HOLDOUT_NO_ELIGIBLE_TRIALS", {
            "symbol": symbol,
            "strategy": strategy,
            "n_trials": len(getattr(study, "trials", []) or []),
            "holdout_top_k": top_k,
        })
        return {
            "promote": False,
            "status": "REJECTED_ON_HOLDOUT",
            "is_rejection_detail_override": "HOLDOUT_NO_ELIGIBLE_TRIALS",
            "symbol_params": {},
            "R_symbol": 0.0,
            "R_global": R_global,
            "promotion_margin": promotion_margin,
            "holdout_passed": False,
            "trial_dir": None,
            "metrics_symbol": {},
            "metrics_global": _metrics_dict(m_global),
        }

    best_trials = eligible_trials[:top_k]

    # 2. Deflations-Vorfilter (Issue #576/#592) — auf der REWARD-Skala (dem tatsächlichen
    # Selektionskriterium argmax(reward)), NICHT auf dem geklemmten Sortino. Der frühere
    # 50.0-Sentinel-Filter (hartcodierte Zahl, die seit der Clip-Änderung #588 ins Leere
    # griff) ist ersatzlos entfallen. baseline = median(rewards) (die Reward-Skala ist nicht
    # nullzentriert). Ein Numerik-Guard-Trial (#588) hat value=None ⇒ fällt komplett aus der Kohorte.
    # Issue #611/#618 — Deflated Sortino Ratio (DSR) statt Reward-Skalen-Deflation. Die alte Kohorte
    # (ALLE oos_evaluated-Trials) war eine Zwei-Punkt-Mischung (Failure-Masse ≈ −13 + eligible Modus ≈ +2);
    # ihr σ = pstdev(bimodal) ≈ gap·√(p(1−p)) war die BERNOULLI-Standardabweichung der Gate-Passrate
    # (maximal bei p=0.5 ⇒ je besser die Strategie, desto höher die Hürde — anti-monoton), und der
    # Median war stets der Rejection-Floor. Fix #611: Kohorte = die ELIGIBLEN Trials, die tatsächlich um
    # argmax konkurrieren. Fix #618: die Statistik ist die vollständige DSR auf der PER-PERIODEN-Sortino-
    # Skala (SR₀ = √V[ŜR_trials]·E[max_N], DSR = PSR relativ zu SR₀), nicht die Reward-Skala.
    deflated_selection = bool(tournament_cfg.get("deflated_selection", False))
    deflation_confidence = float(tournament_cfg.get("deflation_confidence", 0.95))
    # Issue #636 — Mindest-Kohorte für eine belastbare V[ŜR_trials]-Schätzung; darunter greift der
    # dokumentierte konservative Varianz-Floor (siehe deflation.sr0_multiple_testing_robust).
    deflation_min_cohort = int(tournament_cfg.get("deflation_min_cohort", 10))
    # Issue #845 — max(oos_n_periods)/min(oos_n_periods) innerhalb der DSR-Kohorte (Faktor 45 in
    # der Praxis beobachtet): ``deflation_var = pvariance(cohort_sr)`` poolt die per-Trial-Sortinos
    # so, als seien sie über eine konstante Stichprobengrösse T beobachtet (dieselbe Annahme, die
    # den Lo-2002-T-bewussten Varianz-Floor motiviert) — eine stark streuende Kohorte verletzt diese
    # Kommensurabilitäts-Voraussetzung und macht DSR/PSR über die Kohorte hinweg nicht vergleichbar.
    deflation_max_n_periods_ratio = float(tournament_cfg.get("deflation_max_n_periods_ratio", 4.0))
    deflation_n_periods_ratio = None
    # Issue #865 — Policy-Entscheidung wird UNABHAENGIG von ``deflated_selection``/``deflation_n``
    # gelesen (Fail-Loud auf einen unbekannten Wert soll auch dann greifen, wenn die Kohorte am Ende
    # zu klein für jede Heterogenitäts-Frage ist — ein Tippfehler im Config-Wert darf nicht dadurch
    # verschleiert werden, dass er nie ausgewertet wird).
    deflation_heterogeneity_policy = tournament_cfg.get("deflation_heterogeneity_policy", "per_stratum")
    if deflation_heterogeneity_policy not in _VALID_DEFLATION_HETEROGENEITY_POLICIES:
        raise ValueError(
            f"tournament.json: deflation_heterogeneity_policy={deflation_heterogeneity_policy!r} "
            f"unbekannt. Erlaubt: {sorted(_VALID_DEFLATION_HETEROGENEITY_POLICIES)}."
        )
    deflation_heterogeneous = False
    deflation_stratum_id = None
    deflation_stratum_n = None
    # Issue #1013 (Katalog #858, Fix Punkt 2) — die Ratio INNERHALB des gewählten Stratums (nicht
    # der vollen, heterogenen Kohorte). Ohne dieses Feld ist die 'per_stratum'-Politik nicht
    # prüfbar: invariants.check_family_n_periods_homogeneity muss nachrechnen können, ob das
    # Stratum selbst kommensurabel ist, nicht nur, dass die volle Kohorte es nicht war.
    deflation_stratum_n_periods_ratio = None
    deflation_sr0 = deflation_dsr = deflation_dsr_z = None
    # Issue #757/#758 — welche Inferenzmethode die DSR tatsächlich lieferte ("stationary_bootstrap"
    # im Regelfall; "sharpe_formula_fallback" nur bei < 5 persistierten Perioden-Returns, siehe
    # unten). Telemetriert, damit der #742-Report Eligibility- und Promotion-Inferenzmethode
    # nebeneinander ausweisen kann (#758-Doppelstandard-Nachweis).
    deflation_inference_method = None
    deflation_n = 0
    deflation_n_effective = 0
    # Issue #695/#696 — rohe (Σ eligibler Trials) vs. declusterte (korrelations-reduzierte)
    # familienweite Config-Zahl, getrennt telemetriert (siehe Docstring/#696-Feldnamen unten).
    deflation_n_family_raw = 0
    deflation_n_family_effective = 0
    # Issue #813 — Anteil der gezaehlten Kandidaten (deflation_n_family, die rohe oos_evaluated-
    # Zaehlung, #784), fuer die ueberhaupt eine Renditeserie vorlag (len(family_rows), VOR dem
    # Decluster). None, solange keine Familien-Kohorte vorliegt (nicht anwendbar).
    deflation_cluster_coverage = None
    deflation_var = None
    # Issue #670 — ``deflation_used_var_floor`` (Rückwärtskompat-Name) bedeutet AUSSCHLIESSLICH
    # "Shrinkage-Gewicht λ ≥ 0.5" (die theoretische Referenz dominiert die Blend-Gewichtung),
    # NICHT "die var_floor-Konstante wurde verwendet" — siehe deflation.sr0_multiple_testing_robust-
    # Docstring. ``deflation_lambda``/``deflation_theoretical_var_source`` sind die präzisen Grössen.
    deflation_used_var_floor = False
    deflation_lambda = None
    deflation_theoretical_var_source = None

    if deflated_selection:
        cohort_sr = [
            t.user_attrs.get("oos_sortino_period") for t in study.trials
            if t.state == optuna.trial.TrialState.COMPLETE
            and t.user_attrs.get("oos_eligible")
            and t.user_attrs.get("oos_sortino_period") is not None
        ]
        deflation_n = len(cohort_sr)
        # Issue #695 — die rohe familienweite Zahl (Σ eligibler Trials über ALLE Strategien-Studies
        # des Symbols) VOR jeder SR₀-Berechnung auf effektiv-unabhängige Configs declustern — exakt
        # der Mechanismus, den ``_study_pbo`` bereits PER STUDY anwendet (``cluster_effective_
        # configs``, Pearson ρ > ``pbo_cluster_threshold``). Ohne diesen Fix zählt E[max_N] near-
        # identische Configs (dichtes Suchraum-Grid) als ebenso viele unabhängige "Schüsse aufs
        # Tor" wie tatsächlich unabhängige Parametrisierungen und überschätzt damit systematisch die
        # Multiple-Testing-Hürde (Root-Cause #695: derselbe Confirm-Lauf declustert für PBO, aber
        # nicht für DSR — inkonsistente Config-Zählung zweier Korrekturen im selben Pfad).
        # Issue #904 Fix 1 (Pitfall #289) — deflation_n_family_raw IST die vom Aufrufer
        # uebergebene, scope-aufgeloeste Zahl (N1 unter promotion_family_scope='per_strategy') —
        # PUNKT. Die vorherige ``max(deflation_n_family_raw, len(family_rows))``-Zeile korrigierte
        # sie stillschweigend auf die symbolweite Renditeserien-Zahl hoch (#695s Declusterungs-
        # Matrix war NIE scope-gefiltert, siehe sweep.py-Docstring), wodurch der #826-Scope aktiv
        # rueckgaengig gemacht statt nur ignoriert wurde. Ein Aufrufer, der eine zu kleine Zahl
        # liefert, ist ein Aufrufer-Bug und wird dort behoben, nicht hier stillschweigend
        # ueberschrieben.
        deflation_n_family_counted = int(deflation_n_family or 0)
        deflation_n_family_raw = deflation_n_family_counted
        deflation_n_family_effective = deflation_n_family_raw
        if deflation_family_period_returns:
            cluster_threshold = float(
                tournament_cfg.get("pbo_cluster_threshold", _PBO_DEFAULT_CLUSTER_THRESHOLD))
            family_rows = [[float(x) for x in r] for r in deflation_family_period_returns if r]
            # Issue #813/#904 — Coverage bezieht sich auf die GEZAEHLTE (scope-aufgeloeste)
            # Kandidatenzahl. deflation_n_family_raw wird NICHT MEHR ueberschrieben (Fix 1) — ein
            # coverage > 1 (mehr Renditeserien als gezaehlte Kandidaten) ist jetzt ein SICHTBARES
            # Symptom eines Aufrufer-Scope-Mismatches, statt lautlos zur neuen Wahrheit erklaert zu
            # werden.
            if deflation_n_family_counted > 0:
                deflation_cluster_coverage = len(family_rows) / deflation_n_family_counted
            if deflation_cluster_coverage is not None and deflation_cluster_coverage < 1.0:
                # Issue #904 Fix 3 (Pitfall #290) — eine unvollstaendige Declusterung (weniger
                # Renditeserien als gezaehlte Kandidaten) darf die Multiplizitaet nie ERHOEHEN: die
                # Declusterung wird ausgesetzt (effective bleibt roh) statt ein aus einer
                # Teilmenge berechnetes len(keep_idx) als Wahrheit ueber die volle Familie
                # auszugeben.
                deflation_n_family_effective = deflation_n_family_raw
            elif len(family_rows) >= 2:
                from automation.optimizer.cpcv import cluster_effective_configs
                n_obs_family = min(len(r) for r in family_rows)
                truncated_family_rows = [r[:n_obs_family] for r in family_rows]
                keep_idx = cluster_effective_configs(truncated_family_rows, threshold=cluster_threshold)
                deflation_n_family_effective = len(keep_idx)
            elif len(family_rows) == 1:
                deflation_n_family_effective = 1
            # Issue #904 Fix 3 — eine Declusterung darf ``deflation_n_family_raw`` in keinem Fall
            # uebersteigen (dieselbe Invariante wie oben, jetzt als harte Untergrenze statt nur als
            # Konsequenz des Kontrollflusses — schuetzt auch gegen zukuenftige cluster_effective_
            # configs-Aenderungen).
            deflation_n_family_effective = min(deflation_n_family_effective, deflation_n_family_raw)
        # Issue #652 — die PROMOTIONS-relevante Multiplizität ist familienweit (bester von mehreren
        # Strategien-Studies je Symbol), nicht per-Study. ``deflation_n`` bleibt die per-Study-
        # Telemetrie (Stichprobengrösse der V[ŜR_trials]-Schätzung); ``deflation_n_effective`` ist
        # NIE kleiner als das per-Study-N (max(...)) — eine fehlende/kleinere Familien-Zahl kann das
        # bereits lokal bekannte N nie unterschreiten. Issue #695 — die familienweite Seite dieses
        # max() ist jetzt die DECLUSTERTE Zahl (``deflation_n_family_effective``), nicht mehr die
        # rohe Σ — das ist der eigentliche Fix (E[max_N] auf effektiver statt roher Config-Zahl).
        deflation_n_effective = max(deflation_n, deflation_n_family_effective)
        if deflation_n >= 2:
            import statistics as _st
            from automation.optimizer.deflation import sr0_multiple_testing_robust
            deflation_var = _st.pvariance([float(s) for s in cohort_sr])
            # Issue #653 — T (OOS-Perioden) der Kohorte für den Lo-2002-Varianz-Floor. Der Median
            # über die eligiblen Trials ist die robuste zentrale T-Schätzung (T ist bei fixer
            # Walk-Forward-Geometrie über die Kohorte annähernd konstant; Ausreisser durch
            # Degeneration einzelner Trials beeinflussen den Median nicht).
            # Issue #865 — dieselbe Filterbedingung wie ``cohort_n_periods`` unten, aber als
            # (sortino, n_periods)-PAAR statt zweier separat aufgebauter Listen — die #865-
            # Stratifizierung braucht die Zuordnung Sortino↔n_periods, die zwei unabhaengige
            # Listen-Comprehensions (bei sonst identischem Filter, aber theoretisch abweichender
            # Reihenfolge/Länge) nicht GARANTIERT liefern.
            cohort_sr_np_pairs = [
                (t.user_attrs.get("oos_sortino_period"), t.user_attrs.get("oos_n_periods"))
                for t in study.trials
                if t.state == optuna.trial.TrialState.COMPLETE
                and t.user_attrs.get("oos_eligible")
                and t.user_attrs.get("oos_sortino_period") is not None
                and t.user_attrs.get("oos_n_periods")
            ]
            cohort_n_periods = [np_ for _, np_ in cohort_sr_np_pairs]
            deflation_t_periods = int(_st.median(cohort_n_periods)) if cohort_n_periods else None
            # Issue #845 — Heterogenitäts-Ratio der Kohorte, unabhängig davon, ob die DSR unten
            # ueberhaupt berechnet wird (die Ratio selbst ist bereits am Median-Ersatz #701 nicht
            # ablesbar, der einen einzelnen Ausreisser nivelliert).
            if len(cohort_n_periods) >= 2:
                _n_periods_min, _n_periods_max = min(cohort_n_periods), max(cohort_n_periods)
                if _n_periods_min > 0:
                    deflation_n_periods_ratio = _n_periods_max / _n_periods_min
            # Issue #865 — die Heterogenitäts-Politik entscheidet weiter unten (sobald der promotete
            # Trial bekannt ist, siehe ``_stratify_cohort_by_n_periods``), WIE mit dieser Kohorte
            # verfahren wird; das Flag selbst hängt nur an der Ratio, nicht an der Politik.
            deflation_heterogeneous = bool(
                deflation_n_periods_ratio is not None
                and deflation_n_periods_ratio > deflation_max_n_periods_ratio
            )
            # Issue #701 — ``n_periods`` ist seit #701 ein PFLICHT-Parameter von
            # ``sr0_multiple_testing_robust`` (der var_floor-Fallback ohne T wurde als tot verifiziert
            # und entfernt, siehe deflation.py-Docstring). ``deflation_t_periods`` ist NIE ``None``,
            # wenn ``deflation_n >= 2`` (jeder Trial in ``cohort_sr`` hat ein definiertes
            # ``oos_sortino_period`` UND — aus DEMSELBEN backtest_runner-Berechnungsblock — ein
            # truthy ``oos_n_periods``); der Guard bleibt als Defense-in-Depth gegen eine künftige,
            # heute unbekannte Datenanomalie erhalten, OHNE die gesamte Promotion-Pipeline
            # abstürzen zu lassen (DSR bleibt dann schlicht unberechnet, wie bei ``deflation_n < 2``).
            if deflation_t_periods is None:
                logging.getLogger("optimizer").warning(
                    f"[DSR #701] {symbol}: deflation_t_periods fehlt trotz deflation_n={deflation_n} "
                    f">= 2 — sollte laut Invariante (oos_sortino_period impliziert oos_n_periods) "
                    f"unerreichbar sein. DSR bleibt für diesen Lauf unberechnet (kein Crash)."
                )
            else:
                # Issue #652 — ``n_trials=deflation_n_effective`` (familienweit) treibt NUR E[max_N];
                # ``variance_n_trials=deflation_n`` (per-Study) treibt das #653-Shrinkage-Gewicht — die
                # Verlässlichkeit von V[ŜR_trials] hängt von der TATSÄCHLICH beobachteten Kohorte ab,
                # nicht von der (grösseren) familienweiten Multiplizität (siehe deflation.py-Docstring).
                # Issue #814 — der explizite, dokumentierte Ersatz fuer die vorherige stille
                # Aufblaehung von deflation_n_effective auf das geplante Budget
                # (deflation_family_floor_mode='budgeted', seit #814 nicht mehr Default): ein
                # additiver SR0-Term statt einer Verzerrung von E[max_N] selbst. None/0 (Default)
                # ⇒ bit-identisch ohne den Term.
                deflation_search_space_penalty = tournament_cfg.get("deflation_search_space_penalty")
                (deflation_sr0, deflation_used_var_floor, deflation_lambda,
                 deflation_theoretical_var_source) = sr0_multiple_testing_robust(
                    deflation_var, deflation_n_effective,
                    min_cohort=deflation_min_cohort,
                    n_periods=deflation_t_periods, variance_n_trials=deflation_n,
                    search_space_penalty=deflation_search_space_penalty,
                )
                logging.getLogger("optimizer").info(
                    f"[DSR #618/#653/#670] {symbol}: SR₀={deflation_sr0:.4f} via stetigem "
                    f"Shrinkage λ={deflation_lambda:.3f} Richtung theoretischer Referenz Lo-2002 (T="
                    f"{deflation_t_periods}) (N_eligible={deflation_n}, "
                    f"N_family_raw={deflation_n_family_raw}, "
                    f"N_family_effective={deflation_n_family_effective}, "
                    f"N_effective={deflation_n_effective}, "
                    f"V[ŜR]_beobachtet={deflation_var:.6f})"
                )

    # Evaluiere den Holdout ueber die Top-k Trials
    holdout_metrics_list = []

    for trial in best_trials:
        symbol_params = trial.user_attrs.get("sampled_params", trial.params)
        m_symbol = _holdout_metrics_for_params(strategy, symbol, symbol_params,
                                               run_backtest=run_backtest, build_trial=build_trial,
                                               catalog_newest_ns=catalog_newest_ns)
        holdout_metrics_list.append((trial, symbol_params, m_symbol))

    # Issue #594/#615 — KOHÄRENTE Promotion aus EINEM Lauf. Der Lauf mit dem MEDIANEN Rang (nach
    # oos_total_return, dokumentierte Tie-Break-Regel via _median_rank_index) liefert seinen
    # VOLLSTÄNDIGEN, kohärenten Metrikvektor UND wird VOLLSTÄNDIG promotet: Params, Gate, R_symbol und
    # der Deflations-Check stammen ALLE aus DIESEM einen trial_dir. #615-Verbindlichkeit: ein gemischter
    # Vektor (Params von Trial Y, Gate/Reward von Trial X) ist unzulässig — er exportierte nie-validierte
    # Parameter (Params[Y]), während Gate-Entscheidung und R_symbol von Trial X stammten. Der Median-Rang
    # (nicht das argmax) ist die bewusste Robustheits-Wahl (#576/#594): er filtert Holdout-Glück doppelt.
    median_idx = _median_rank_index([m.oos_total_return for _, _, m in holdout_metrics_list])
    promoted_trial, promoted_symbol_params, promoted_m_symbol = holdout_metrics_list[median_idx]
    # Issue #615 — der EINE trial_dir, aus dem der gesamte promotete Vektor stammt (Invarianten-Beleg).
    promoted_trial_dir = getattr(promoted_m_symbol, "holdout_trial_dir", None)
    if not isinstance(promoted_trial_dir, str):
        promoted_trial_dir = None

    # Issue #594 — Holdout-Reward über DENSELBEN Codepfad (compute_reward) mit holdout=True: die
    # IS-abhängigen Terme werden ABGESCHALTET (kein 0.0-Platzhalter, der bei negativem base eine
    # fiktive Overfit-Strafe von 0.5·|base| erzeugte). promoted_m_symbol ist ein REALER Lauf.
    R_symbol = (compute_reward(promoted_m_symbol, universe_size=1, weights=global_weights, holdout=True)
                if global_weights else compute_reward(promoted_m_symbol, universe_size=1, holdout=True))

    # Gate-Evaluation ZWINGEND über denselben promoteten (Median-Rang-)Holdout-Vektor.
    holdout_passed = _holdout_gate_passed(
        promoted_m_symbol, risk_dd_cap,
        sortino_fallback_enabled=(oos_sortino_fallback == "total_return"),
    )
    # Issue #654 — die TATSÄCHLICHE blockierende Ursache verfolgen (statt am Ende nur "irgendein
    # früheres Gate hat schon abgelehnt"). Jeder der folgenden Blöcke, der ``holdout_passed`` auf
    # False setzt, schreibt hier seinen spezifischen Grund; da jeder Block nur greift, wenn
    # ``holdout_passed`` an dieser Stelle NOCH True ist, kann genau EIN Grund gewinnen — keine
    # Ambiguität. Default (Symbol-Holdout-Gate selbst gescheitert): REJECT_HOLDOUT_GATE.
    holdout_reject_detail = None if holdout_passed else "REJECT_HOLDOUT_GATE"

    # Issue #958 (Katalog D, P0) — ein Trial ohne eine einzige OOS-Periode (``oos_n_periods == 0``)
    # hat keinen Sortino, keine PSR, keinen Profit-Faktor und keine Expectancy — er kann nicht "am
    # besten" sein. VOR diesem Fix konnte ``_median_rank_index`` (der nach ``oos_total_return``
    # rankt, nicht nach ``oos_n_periods``) einen solchen Trial trotzdem zum ``promoted_trial``
    # machen; downstream bildete die Deflations-Stratifizierung das auf "Stratum None" ab (#865),
    # statt die eigentliche Ursache — ein unzulaessiger Kandidat — sichtbar zu machen. Bewusst NICHT
    # als Filter VOR der Median-Rang-Auswahl umgesetzt (das haette ``eligible_trials``/die Top-k-
    # Kohorte fuer die vielen bestehenden Tests veraendert, die ``oos_selection_statistic_available``
    # nicht setzen); stattdessen ein narrower Guard genau an der Stelle, an der die tatsaechliche
    # Promotions-Entscheidung faellt — derselbe #654-Musterblock wie jedes andere Holdout-Gate.
    if holdout_passed and not (getattr(promoted_m_symbol, "oos_n_periods", 0) or 0) >= 1:
        holdout_passed = False
        holdout_reject_detail = "REJECT_PROMOTED_TRIAL_INADMISSIBLE"
        logging.getLogger("optimizer").error(
            "[#958] %s/%s: promoted_trial hat oos_n_periods=%r (< 1) — Promotion verweigert statt "
            "eines degenerierten Study-Besten (Stratum-None-Klasse).",
            strategy, symbol, getattr(promoted_m_symbol, "oos_n_periods", None),
        )

    # Issue #1007 (Katalog #858) — eine Study kann bislang gleichzeitig als informationsfrei
    # (severity='blocking'-Invariante FAIL, z. B. check_selection_statistic_availability,
    # check_guard_reference_stability) markiert UND als Gewinner exportiert werden, weil Invarianten
    # bislang ein reiner Report-Nachlauf sind, kein Eingang in diese Entscheidung. Unbedingter
    # Hard-Stop wie REJECT_HOLDOUT_GATE — KEIN Ersatzpfad (auch nicht dsr_or_robust_pair, siehe
    # unten) darf ihn unterlaufen, deshalb hier VOR jeder DSR/PBO/Boundary-Verzweigung geprüft.
    blocking_invariant_names = sorted({
        str(r.get("name")) for r in (study_invariant_results or [])
        if r.get("severity") == "blocking" and r.get("passed") is False and r.get("name")
    })
    if holdout_passed and blocking_invariant_names:
        holdout_passed = False
        holdout_reject_detail = "REJECT_STUDY_INVARIANT_BLOCKING"
        logging.getLogger("optimizer").warning(
            "[#1007] %s/%s: blockierende Invariante(n) %s ⇒ REJECT_STUDY_INVARIANT_BLOCKING "
            "(kein Ersatzpfad).", strategy, symbol, blocking_invariant_names,
        )

    # Issue #865 (Pitfall #277) — die Heterogenitäts-Politik wird JETZT angewendet, VOR der DSR-
    # Berechnung/-Entscheidung unten — nicht erst an der Export-Kohärenzgrenze (die dortige #845-
    # Prüfung kam bislang zu spät: die Promotion hatte die DSR bereits aus der inkommensurablen
    # gepoolten Kohorte verwendet, siehe Docstring von ``_VALID_DEFLATION_HETEROGENEITY_POLICIES``).
    if deflation_heterogeneous and deflation_sr0 is not None:
        if deflation_heterogeneity_policy == "per_stratum":
            _promoted_n_periods_anchor = getattr(promoted_m_symbol, "oos_n_periods", None)
            _stratum_sr, _stratum_id, _stratum_n_periods = (
                _stratify_cohort_by_n_periods(cohort_sr_np_pairs, _promoted_n_periods_anchor)
                if _promoted_n_periods_anchor else ([], None, [])
            )
            deflation_stratum_id = _stratum_id
            deflation_stratum_n = len(_stratum_sr)
            if deflation_stratum_n >= 2:
                import statistics as _st_stratum
                from automation.optimizer.deflation import sr0_multiple_testing_robust
                _stratum_var = _st_stratum.pvariance([float(s) for s in _stratum_sr])
                _stratum_t = (int(_st_stratum.median(_stratum_n_periods))
                              if _stratum_n_periods else deflation_t_periods)
                # Issue #1013 Fix Punkt 2 — Ratio INNERHALB des Stratums, dieselbe max/min-
                # Definition wie ``deflation_n_periods_ratio`` auf der vollen Kohorte oben.
                if _stratum_n_periods and min(_stratum_n_periods) > 0:
                    deflation_stratum_n_periods_ratio = max(_stratum_n_periods) / min(_stratum_n_periods)
                (deflation_sr0, deflation_used_var_floor, deflation_lambda,
                 deflation_theoretical_var_source) = sr0_multiple_testing_robust(
                    _stratum_var, deflation_n_effective,
                    min_cohort=deflation_min_cohort,
                    n_periods=_stratum_t, variance_n_trials=deflation_stratum_n,
                    search_space_penalty=deflation_search_space_penalty,
                )
                deflation_var = _stratum_var
                deflation_n = deflation_stratum_n
                deflation_t_periods = _stratum_t
                logging.getLogger("optimizer").info(
                    f"[#865] {symbol}: n_periods-Kohorte heterogen (ratio="
                    f"{deflation_n_periods_ratio:.2f} > {deflation_max_n_periods_ratio}) — SR₀ auf "
                    f"Stratum {deflation_stratum_id} beschränkt (n={deflation_stratum_n}, "
                    f"neues SR₀={deflation_sr0:.4f})."
                )
            else:
                logging.getLogger("optimizer").warning(
                    f"[#865] {symbol}: Stratum {deflation_stratum_id} des promoteten Trials "
                    f"(n_periods={_promoted_n_periods_anchor}) hat < 2 Mitglieder "
                    f"(n={deflation_stratum_n}) — SR₀ unterdrückt (Fallback wie 'suppress_dsr')."
                )
                deflation_sr0 = None
                deflation_used_var_floor = False
                deflation_lambda = None
                deflation_theoretical_var_source = None
        else:
            logging.getLogger("optimizer").warning(
                f"[#865] {symbol}: n_periods-Kohorte heterogen (ratio={deflation_n_periods_ratio:.2f} "
                f"> {deflation_max_n_periods_ratio}, policy={deflation_heterogeneity_policy}) — "
                f"SR₀/DSR NICHT aus der Kohorte übernommen (fail-closed statt stiller Spät-Suppress)."
            )
            deflation_sr0 = None
            deflation_used_var_floor = False
            deflation_lambda = None
            deflation_theoretical_var_source = None
        if deflation_heterogeneity_policy == "reject" and deflation_sr0 is None:
            # Issue #865 — 'reject' ist ein UNBEDINGTER Veto-Modus: im Unterschied zu 'suppress_dsr'
            # (die DSR wird None, das bestehende ``deflation_dsr is None ⇒ REJECT_HOLDOUT_DSR_DROP``-
            # Gate greift NUR, wenn ``holdout_passed`` bislang True war) erzwingt 'reject' den Reject
            # SOFORT und mit einem EIGENEN Grund, der die spätere 'dsr_or_robust_pair'-Ersatzroute
            # NICHT unterlaufen darf — eine undefinierte Korrektur ist kein bestandener Test (siehe
            # Katalog-Fix-Item 2, Pitfall #277), unabhängig davon, ob PBO/Bootstrap-CI sonst passen.
            holdout_passed = False
            holdout_reject_detail = "REJECT_DEFLATION_HETEROGENEOUS"
            logging.getLogger("optimizer").warning(
                f"[#865] {symbol}: policy='reject' ⇒ REJECT_DEFLATION_HETEROGENEOUS (unbedingt, "
                f"kein Ersatzpfad über promotion_correction_mode='dsr_or_robust_pair')."
            )

    # Issue #993 — ``ci_lo`` vorab initialisiert (statt nur im bedingten Bootstrap-CI-Block), damit
    # der untere CI-Wert unabhaengig vom Gate-Ausgang persistierbar ist (deployment_gate.py braucht
    # den Rohwert, nicht nur den daraus abgeleiteten Pass/Fail-Effekt auf ``holdout_passed``).
    ci_lo = None

    # Issue #1005 (Katalog #858) — vorab initialisiert, damit der ``dsr_or_robust_pair``-Ersatzpfad
    # (unten) auditierbar bleibt, AUCH wenn er nie betreten wird (Route bleibt None) oder das
    # promotete Symbol den Pfad gar nicht erreicht (Export-Block unten liest diese Variablen
    # unbedingt).
    promotion_correction_route = None
    promotion_correction_pbo_ok = None
    promotion_correction_ci_lower = None
    promotion_correction_n_period_returns = None
    promotion_correction_alpha_effective = None

    # Issue #618/#636 — DSR-BERECHNUNG von der Pass-Kette ENTKOPPELT: vorher lief dieser Block nur
    # ``if holdout_passed and ...`` — aber JEDE Strategie scheiterte an einem FRÜHEREN Holdout-Gate
    # (Excess-Return via #629, negativer Holdout-Sortino), sodass ``holdout_passed`` bereits False
    # war, BEVOR die DSR exerziert wurde ⇒ ``deflated_dsr`` blieb in ALLEN Proposals None (die DSR
    # war die letzte Hürde einer Kette, die vorher schon alles ablehnte — nie erreicht, Telemetrie
    # uninformativ). Fix: der WERT wird IMMER berechnet, sobald genug Kohorte + ein definierter
    # promoteter per-Perioden-Sortino vorliegen — unabhängig vom bisherigen ``holdout_passed``. Der
    # DROP-EFFEKT (``holdout_passed=False``) bleibt an ``holdout_passed`` gekoppelt: ein Trial, der
    # ohnehin schon abgelehnt ist, wird nicht zusätzlich "gedroppt", aber seine DSR ist trotzdem
    # sichtbar (Diagnose: "hätte er die früheren Gates geschafft, wäre er an der DSR gescheitert?").
    if deflated_selection and deflation_n >= 2:
        promoted_sr_period = getattr(promoted_m_symbol, "oos_sortino_period", None)
        if promoted_sr_period is not None:
            # Issue #701 — ``deflation_sr0`` kann in dem (nach Invariante unerreichbaren, aber
            # defensiv abgefangenen) Fall None sein, dass ``deflation_t_periods`` oben fehlte. OHNE
            # SR0 gibt es keine Referenz für DSR/psr_z (``float(None)`` würde crashen) — die
            # BERECHNUNG wird dann übersprungen, aber die REJECTION-Prüfung unten bleibt aktiv:
            # ``deflation_dsr`` bleibt in diesem Fall ``None`` und zählt konservativ als "DSR nicht
            # nachgewiesen" (fail-closed, dieselbe Semantik wie ein regulär berechnetes, zu
            # niedriges DSR — kein stiller Promotions-Freifahrtschein durch eine Datenanomalie).
            if deflation_sr0 is not None:
                from automation.optimizer.deflation import (
                    deflated_sharpe_ratio, psr_z, bootstrap_psr_z, psr_from_z)
                promoted_n_periods = getattr(promoted_m_symbol, "oos_n_periods", 0)
                promoted_skew = getattr(promoted_m_symbol, "oos_ret_skew", 0.0)
                promoted_kurtosis = getattr(promoted_m_symbol, "oos_ret_kurtosis", 3.0)
                promoted_period_returns = getattr(promoted_m_symbol, "oos_period_returns", None) or ()
                # Issue #651 — dasselbe (bereits gefloorte) ``deflation_sr0`` speist SOWOHL die
                # Entscheidung (``deflation_dsr``, hier) ALS AUCH die Telemetrie (``deflation_dsr_z``,
                # unten). Ein Aufruf, EIN SR₀ — bit-identisch.
                #
                # Issue #757 — BOOTSTRAP-Standardfehler statt psr_z-Sharpe-Sampling-Varianz (dieselbe
                # Root-Cause wie die Eligibility-PSR, backtest_runner.py): ``promoted_sr_period`` ist
                # ein Sortino-Punktschätzer, für den psr_z/lo2002_sharpe_variance nicht hergeleitet
                # sind. Nutzt dieselbe Bootstrap-Infrastruktur wie der Holdout-Bootstrap-CI (#619,
                # ``promoted_m_symbol.oos_period_returns``) — beseitigt zugleich den #758-Inferenz-
                # Doppelstandard (Eligibility und Promotion rechnen jetzt auf DERSELBEN Methode).
                # Fallback auf die Sharpe-Formel NUR, wenn zu wenige Perioden-Returns persistiert
                # wurden (< 5, Legacy-/Testfixtures ohne oos_period_returns) — sonst käme kein
                # Bootstrap-SE zustande.
                if len(promoted_period_returns) >= 5:
                    deflation_dsr_z, _dsr_se_boot = bootstrap_psr_z(
                        promoted_period_returns, sr_star=deflation_sr0,
                        n_boot=int(tournament_cfg.get("psr_bootstrap_resamples", 200)))
                    deflation_dsr = psr_from_z(deflation_dsr_z)
                    deflation_inference_method = "stationary_bootstrap"
                else:
                    deflation_dsr = deflated_sharpe_ratio(
                        promoted_sr_period, promoted_n_periods,
                        sr0=deflation_sr0,
                        skew=promoted_skew, kurtosis=promoted_kurtosis)
                    deflation_inference_method = "sharpe_formula_fallback"
                    deflation_dsr_z = psr_z(
                        promoted_sr_period, promoted_n_periods,
                        skew=promoted_skew, kurtosis=promoted_kurtosis, sr_star=deflation_sr0)
            if holdout_passed and (deflation_dsr is None or deflation_dsr < deflation_confidence):
                holdout_passed = False
                holdout_reject_detail = "REJECT_HOLDOUT_DSR_DROP"
                # Issue #701 — die Log-Message darf nicht am ``.4f``-Format crashen, wenn
                # ``deflation_sr0`` None blieb (kein sekundärer Bug on top eines bereits
                # abgefangenen Primärfalls).
                sr0_repr = f"{deflation_sr0:.4f}" if deflation_sr0 is not None else "None"
                logging.getLogger("optimizer").warning(
                    f"[DSR-Drop #618] {symbol}: DSR={deflation_dsr} < {deflation_confidence} "
                    f"(SR₀={sr0_repr}, N={deflation_n}) ⇒ HOLD"
                )

    # Issue #619 — Stationary-Bootstrap-CI (opt-in) auf dem promoteten Holdout-Sortino: die UNTERE
    # CI-Grenze muss > 0 sein (ci_lower(sortino) > 0), nicht nur der Punktschätzer. Fehlen genug
    # Returns (< 5) ⇒ kein Zusatz-Veto (das Punkt-Gate bleibt maßgeblich).
    if holdout_passed and bool(tournament_cfg.get("holdout_bootstrap_ci", False)):
        ci_ok, ci_lo = _holdout_bootstrap_ci_passes(promoted_m_symbol, confidence=deflation_confidence)
        if not ci_ok:
            holdout_passed = False
            holdout_reject_detail = "REJECT_HOLDOUT_BOOTSTRAP_CI"
            logging.getLogger("optimizer").warning(
                f"[Bootstrap-CI #619] {symbol}: ci_lower(sortino)={ci_lo} ≤ 0 ⇒ HOLD"
            )

    # Issue #619/#663 — Sweep-Level-PBO (Selektions-Overfit) auf der feineren, gepoolten
    # OOS-Perioden-CSCV-Partition (siehe _study_pbo-Docstring): PBO > 0.5 ⇒ der IS-Gewinner ist OOS
    # schlechter als der Median ⇒ Promotion ist per Definition Selektions-Overfit ⇒ HARD-STOP.
    study_pbo, pbo_telemetry = _study_pbo(study, tournament_cfg=tournament_cfg)
    pbo_overfit = bool(study_pbo is not None and study_pbo > 0.5)
    if pbo_overfit:
        logging.getLogger("optimizer").warning(
            f"[PBO #619] {symbol}: PBO={study_pbo:.3f} > 0.5 ⇒ REJECTED_SELECTION_OVERFIT"
        )

    # Issue #697 — Fail-Loud-Konsument des #679-Gate-Kollinearitäts-Alarms: prüft AUS DER STUDY
    # SELBST (dieselbe ``oos_gate_deltas``-Quelle wie run_optimization._emit_study_summary), ob
    # ``eligible_requires_all`` noch ein Gate enthält, das die LIVE Kohorte als redundant gegenüber
    # einem höher priorisierten Konjunktions-Mitglied ausweist. Reine Diagnose (ändert keine
    # Promotion-Entscheidung); Ergebnis (leer im Regelfall nach #697) sichtbar im Winner-Eintrag.
    from automation.optimizer.reward import assert_eligible_requires_all_not_redundant
    gate_deltas_cohort = [getattr(t, "user_attrs", {}).get("oos_gate_deltas") for t in study.trials]
    unconsolidated_gates = assert_eligible_requires_all_not_redundant(
        gate_deltas_cohort, tournament_cfg.get("eligible_requires_all", []), tournament_cfg)

    # Issue #659 — gestapelte Multiple-Testing-Korrekturen kompoundieren den Type-II-Fehler. Die
    # Promotion verlangt per Default (``promotion_correction_mode`` fehlt/"conjunction", bit-
    # identisch zum Pre-#659-Verhalten) DSR **UND** Bootstrap-CI **UND** PBO gleichzeitig — auf einem
    # kurzen Holdout (z. B. 37 Trades) ist die 95 %-DSR-Schwelle ALLEIN bereits streng; die
    # KONJUNKTIVE Verknüpfung mehrerer, teils redundanter Korrekturen (PBO und DSR sind
    # UNTERSCHIEDLICHE, teils widersprüchliche Multiple-Testing-/Overfit-Bestätigungen) lehnt
    # strukturell auch reale Edges ab. Opt-in ``promotion_correction_mode="dsr_or_robust_pair"``
    # (tournament.json): EINE der beiden UNABHÄNGIGEN Bestätigungen genügt — entweder die DSR selbst
    # ODER (PBO sicher UND Bootstrap-CI besteht) — TROTZ eines DSR-Miss. Ein gescheitertes
    # Symbol-Holdout-Gate SELBST (``REJECT_HOLDOUT_GATE``) bleibt IMMER ein Hard-Stop — kein
    # Ersatzpfad ersetzt das Basisgate. Die KONKRETE Wahl (Modus + ``deflation_confidence``) ist
    # empirisch aus einem dedizierten Kalibrierlauf abzuleiten (siehe tournament.json-Schema) —
    # dieser Mechanismus stellt nur die STRUKTUR bereit, erzwingt aber keine Schwelle.
    correction_mode = tournament_cfg.get("promotion_correction_mode", "conjunction")
    if correction_mode not in _VALID_PROMOTION_CORRECTION_MODES:
        raise ValueError(
            f"tournament.json: promotion_correction_mode={correction_mode!r} unbekannt. "
            f"Erlaubt: {sorted(_VALID_PROMOTION_CORRECTION_MODES)}."
        )
    if (correction_mode == "dsr_or_robust_pair"
            and not holdout_passed
            and holdout_reject_detail in ("REJECT_HOLDOUT_DSR_DROP", "REJECT_HOLDOUT_BOOTSTRAP_CI")):
        dsr_ok = deflation_dsr is not None and deflation_dsr >= deflation_confidence
        # Issue #1005 (Katalog #858, Pitfall #343) — Fail-closed: ``study_pbo is None`` (PBO nicht
        # schätzbar, z. B. < ``pbo_min_configs``) ist KEINE bestandene Prüfung. Der frühere
        # ``not pbo_overfit`` konflationierte "PBO ≤ 0.5" mit "PBO unbekannt" — beide Fälle liessen
        # ``robust_pair_ok=True`` zu, obwohl im zweiten Fall keinerlei Evidenz vorliegt. ``pbo_overfit``
        # selbst (Zeile oben) bleibt unverändert — dieser Fix betrifft ausschliesslich die ERSATZ-
        # Bestätigung, nicht das allgemeine PBO-Veto.
        pbo_ok = bool(study_pbo is not None and study_pbo <= 0.5)
        n_period_returns_recheck = len(
            list(getattr(promoted_m_symbol, "oos_period_returns", ()) or ()))
        ci_ok_recheck = True
        ci_lo_recheck = None
        alpha_effective = None
        if bool(tournament_cfg.get("holdout_bootstrap_ci", False)):
            # Issue #1005 Fix 3 — ``robust_pair`` ersetzt die DSR (eine bereits multiplizitäts-
            # korrigierte Grösse über N durchsuchte Trials) durch PBO+Bootstrap-CI. Damit dieser
            # Ersatz selbst multiplizitätskorrigiert ist (Pitfall #344 — "ein Ersatz für eine
            # Multiplizitätskorrektur muss selbst multiplizitätskorrigiert sein"), wird die CI nicht
            # auf ``1 − α``, sondern auf ``1 − α/N_eff`` gezogen (Bonferroni über die deklusterte
            # Familiengrösse, dieselbe ``pbo_n_configs``, die ``_study_pbo`` bereits über
            # ``cpcv.cluster_effective_configs`` berechnet hat).
            n_eff_configs = max(1, int(pbo_telemetry.get("pbo_n_configs") or 1))
            alpha_base = 1.0 - deflation_confidence
            alpha_effective = alpha_base / n_eff_configs
            confidence_effective = 1.0 - alpha_effective
            ci_ok_recheck, ci_lo_recheck = _holdout_bootstrap_ci_passes(
                promoted_m_symbol, confidence=confidence_effective)
        # Issue #1005 Fix 2 — ``_holdout_bootstrap_ci_passes`` liefert bei zu wenigen Returns jetzt
        # ``(None, None)``; ``pbo_ok and None`` ist ``None``, ``bool(None)`` ist ``False`` — eine
        # nicht schätzbare CI fällt hier korrekt als "nicht bestanden", nicht mehr als
        # stillschweigend bestanden.
        robust_pair_ok = bool(pbo_ok and ci_ok_recheck)
        promotion_correction_pbo_ok = pbo_ok
        promotion_correction_ci_lower = ci_lo_recheck
        promotion_correction_n_period_returns = n_period_returns_recheck
        promotion_correction_alpha_effective = alpha_effective
        if dsr_ok:
            promotion_correction_route = "dsr"
        elif robust_pair_ok:
            promotion_correction_route = "robust_pair"
        if dsr_ok or robust_pair_ok:
            holdout_passed = True
            holdout_reject_detail = None
            logging.getLogger("optimizer").info(
                f"[#659/#1005] {symbol}: promotion_correction_mode=dsr_or_robust_pair — Route="
                f"{promotion_correction_route} (dsr_ok={dsr_ok}, pbo={study_pbo}, pbo_ok={pbo_ok}, "
                f"ci_ok={ci_ok_recheck}, ci_lower={ci_lo_recheck}, "
                f"alpha_effective={alpha_effective}) ⇒ Holdout-Gate reinstated."
            )

    # Issue #622 — Randlösungs-Veto: klebt der Gewinner an > 30 % der Suchraumgrenzen, ist die Lösung
    # keine Lösung (Bounds falsch ODER der Reward drückt in die Ecke) ⇒ FAIL-LOUD, kein READY_FOR_PR
    # (vorher nur eine folgenlose #597-WARNING). Lazy-Import (run_optimization importiert confirm).
    boundary_frac = None
    boundary_directions = None
    try:
        from automation.optimizer.run_optimization import (
            _boundary_hit_fraction, _boundary_hit_directions)
        boundary_frac = _boundary_hit_fraction(study, strategy)
        boundary_directions = _boundary_hit_directions(study, strategy)
    except Exception:
        boundary_frac = None
        boundary_directions = None
    boundary_overfit = bool(boundary_frac is not None and boundary_frac > 0.3)
    if boundary_overfit:
        logging.getLogger("optimizer").warning(
            f"[Boundary #622] {symbol}: boundary_hit_fraction={boundary_frac:.2f} > 0.3 ⇒ "
            f"REJECTED_BOUNDARY_SOLUTION (Bounds prüfen ODER Reward-Konditionierung)"
        )

    # Issue #763 — >= 0.5 ist ein STÄRKERES Signal als der #622-Veto (die HÄLFTE der Gewinner-
    # Parameter klebt an der Grenze): statt eines terminalen REJECTED_BOUNDARY_SOLUTION erzeugt das
    # HOLD_BOUNDARY_UNRESOLVED — nur ein Re-Run mit ausgeweiteten Bounds kann entscheiden, ob der
    # Rand ein echtes Optimum oder ein Suchraum-Artefakt ist.
    boundary_unresolved = bool(boundary_frac is not None and boundary_frac >= 0.5)
    if boundary_unresolved:
        directions_str = ", ".join(f"{p}={d}" for p, d in sorted((boundary_directions or {}).items()))
        logging.getLogger("optimizer").warning(
            f"[Boundary #763] {symbol}: boundary_hit_fraction={boundary_frac:.2f} >= 0.5 "
            f"({directions_str}) ⇒ HOLD_BOUNDARY_UNRESOLVED — Re-Run mit ausgeweiteten Bounds nötig, "
            f"bevor Rand-Optimum vs. Suchraum-Artefakt unterscheidbar ist."
        )

    # Issue #777 — der Bounds-Vorschlag wird für JEDE Randlösung geschrieben (``boundary_overfit``,
    # frac > 0.3 — dieselbe Schwelle wie die #622-Warnung), nicht mehr nur für die STÄRKERE
    # ``boundary_unresolved``-Schwelle (>= 0.5). Root-Cause #777: `[#597]` meldete 32 Studies mit
    # frac 0,33–0,50 — GENAU der Bereich zwischen den beiden Schwellen, für den vor diesem Fix nie
    # ein Vorschlag entstand (die #622-Warnung blieb reine Prosa). Der Vorschlag geht über
    # ``record_diagnosed_pair`` in denselben `#761`-Cache, den ``spaces._load_auto_proposed_bounds``
    # bereits liest — derselbe Auto-Override-Mechanismus wie bei einem 'signal_frequency'-Kollaps,
    # kein manueller Config-Edit nötig, bevor der nächste Sweep das Paar erneut versucht.
    if boundary_overfit:
        try:
            from automation.optimizer.sweep_diagnostics import (
                propose_bounds_from_boundary_hits, record_diagnosed_pair)
            _widen_fraction = float((global_weights or {}).get("bounds_widening_factor", 0.3))
            proposed_bounds = propose_bounds_from_boundary_hits(
                boundary_directions or {}, strategy, widen_fraction=_widen_fraction)
            if proposed_bounds:
                # Issue #831 Fix Punkt 4 — fraction/params zusätzlich zum Vorschlag selbst
                # gestempelt, damit report._boundary_solutions_section() die volle
                # {strategy, symbol, fraction, params, proposed_bounds}-Form ausweisen kann.
                try:
                    _boundary_params = dict(getattr(study.best_trial, "params", {}) or {})
                except Exception:
                    _boundary_params = {}
                record_diagnosed_pair({
                    "strategy": strategy, "symbol": symbol,
                    "action": "search_space_override",
                    "binding_cause": "boundary_solution",
                    "proposed_bounds": proposed_bounds,
                    "boundary_hit_fraction": boundary_frac,
                    "boundary_params": _boundary_params,
                })
        except Exception:
            logging.getLogger("optimizer").warning(
                f"[Boundary #763] {symbol}: proposed_bounds konnten nicht in den #761-Cache "
                f"geschrieben werden (non-fatal, {'HOLD_BOUNDARY_UNRESOLVED' if boundary_unresolved else 'REJECTED_BOUNDARY_SOLUTION'} bleibt wirksam)."
            )

    # Issue #655 — R_symbol/R_global sind seit #655 ``None`` (statt eines numerischen −20-Sentinels),
    # sobald der jeweilige Holdout-Backtest NIE OOS-evaluierbar war (compute_reward(holdout=True)
    # liefert dann ``None``, keine Zahl). ``R_symbol`` ist praktisch immer definiert (der promotete
    # Trial stammt aus der eligiblen Kohorte, hat also per Definition OOS-Performance) — defensiv
    # dennoch behandelt. Ein UNDEFINIERTES ``R_global`` (der globale Vektor erzeugte auf DIESEM
    # Symbol/Holdout nie OOS-Trades) bedeutet: es gibt KEINE Baseline zu schlagen ⇒ die R-Edge-
    # Bedingung gilt trivial als erfüllt (dokumentiert, ersetzt den impliziten Effekt des alten
    # −20-Sentinels OHNE die kontaminierte Zahl in Telemetrie/Aggregation zu tragen). Ein
    # UNDEFINIERTES ``R_symbol`` kann dagegen NIE einen Edge belegen ⇒ die Bedingung scheitert.
    if R_symbol is None:
        r_edge_satisfied = False
    elif R_global is None:
        r_edge_satisfied = True
    else:
        r_edge_satisfied = R_symbol > R_global + promotion_margin

    promote = bool(holdout_passed and not pbo_overfit and not boundary_overfit and r_edge_satisfied)

    # Issue #654 — ``is_rejection_detail`` im Proposal war bislang der modale IS-Study-Trial-Grund
    # (via ``_dominant_is_rejection_detail``-Fallback, da ``is_rejection_detail_override`` hier
    # unconditional ``None`` war), NICHT die tatsächliche Promotion-Ursache. DynamicBreakout zeigte
    # z. B. ``REJECT_OOS_MIN_TOTAL_RETURN`` (70/76 IS-Trials), obwohl das Symbol-Holdout-Gate
    # bestanden war und die Promotion AUSSCHLIESSLICH am DSR-Drop scheiterte — die Forensik wies die
    # falsche Ursache aus. Fix: jeder Promotion-Ausgang setzt seinen EXAKTEN Grund; der modale
    # IS-Grund bleibt separat als ``dominant_is_rejection_detail`` in ``export_symbol_proposal``
    # (Study-Diagnose) erhalten — vermischt aber NICHT mehr mit der Promotion-Entscheidung.
    if pbo_overfit:
        status = "REJECTED_SELECTION_OVERFIT"
        is_rejection_detail_override = "REJECT_SELECTION_PBO"
    elif boundary_unresolved:
        # Issue #763 — >= 0.5 ist STRIKT stärker als der #622-Veto (impliziert boundary_overfit=True)
        # und muss daher VOR der #622-Verzweigung geprüft werden: ein HOLD ist etwas anderes als ein
        # terminaler REJECT — der proposed_bounds-Kandidat liegt bereits im #761-Cache (oben).
        status = "HOLD_BOUNDARY_UNRESOLVED"
        is_rejection_detail_override = "HOLD_BOUNDARY_UNRESOLVED"
    elif boundary_overfit:
        status = "REJECTED_BOUNDARY_SOLUTION"
        is_rejection_detail_override = "REJECT_BOUNDARY_SOLUTION"
    elif not holdout_passed:
        status = "REJECTED_ON_HOLDOUT"
        is_rejection_detail_override = holdout_reject_detail
    elif promote:
        status = "READY_FOR_PR"
        is_rejection_detail_override = None
    else:
        status = "REJECTED_NO_EDGE_OVER_GLOBAL"
        is_rejection_detail_override = "REJECT_NO_EDGE_OVER_GLOBAL"

    best_result = {
        "promote": promote,
        "status": status,
        "is_rejection_detail_override": is_rejection_detail_override,
        # Issue #783 (Akzeptanzkriterium #3) — JEDER promotete Kandidat traegt eine nicht-leere
        # promotion_route, nicht nur die #682-Default-Route: unterscheidbar von einem kuenftigen,
        # noch unbenannten Promotions-Pfad, und macht das Feld selbst zu einem verlaesslichen
        # Pflichtfeld statt eines nur gelegentlich gesetzten Sonderfalls.
        "promotion_route": "per_symbol_holdout_validated" if promote else None,
        # Issue #615 — Params, R_symbol, holdout_passed und trial_dir stammen ALLE aus promoted_m_symbol.
        "symbol_params": promoted_symbol_params,
        "R_symbol": R_symbol,
        "R_global": R_global,
        "promotion_margin": promotion_margin,
        "holdout_passed": bool(holdout_passed),
        "trial_dir": promoted_trial_dir,
        "metrics_symbol": _metrics_dict(promoted_m_symbol),
        "metrics_global": _metrics_dict(m_global),
        # Issue #993 — der Datenstand, auf dem DIESE Promotion beruht (dieselbe Quelle wie
        # champions._build_entry_from_promotion's ``integrity.data_snapshot_sha256``). Deployment-
        # Grenze (deployment_gate.py) vergleicht dies gegen den Datenstand ZUM Deployment-Zeitpunkt —
        # eine Promotion auf einem aelteren Snapshot darf nicht auf einem neueren deployt werden,
        # ohne neu bestaetigt zu werden.
        "data_snapshot_sha256": catalog_fingerprint(),
    }
    # Issue #993 — Rohgroessen, die bislang nur transient innerhalb dieser Funktion existierten
    # (PSR des promoteten Holdout-Vektors, untere Bootstrap-CI-Grenze, Boundary-Hit-Anteil), werden
    # additiv in ``metrics_symbol`` gestempelt. Keine dieser drei Groessen aendert eine bestehende
    # Promotion-Entscheidung (rein additive Telemetrie) — sie macht die acht Deployment-Klauseln aus
    # deployment_gate.py erstmals aus dem persistierten Promotion-Record rekonstruierbar, statt nur
    # als Pass/Fail-Nebenwirkung auf ``holdout_passed``/``status`` sichtbar zu sein.
    best_result["metrics_symbol"]["oos_psr"] = getattr(promoted_m_symbol, "oos_psr", None)
    best_result["metrics_symbol"]["holdout_ci_lower_sortino"] = ci_lo
    best_result["metrics_symbol"]["boundary_hit_fraction"] = boundary_frac
    # Issue #786 — das bindende Holdout-Gate (negativstes normiertes Delta) fuer JEDE Study mit
    # einem erreichten Holdout-Lauf, nicht nur bei REJECT_HOLDOUT_GATE — macht die haeufigste
    # Ablehnungsursache des gesamten Systems (74,5% der Studies mit eligiblen Trials) attribuierbar.
    # Issue #1075 (Pitfall #375-Klasse) — NUR gesetzt, wenn die Holdout-Stufe TATSAECHLICH FAILt
    # (holdout_passed is False): ein bestandener Holdout hat kein "bindendes" Gate (nichts ist
    # gescheitert) — holdout_tightest_margin ersetzt es dann als ehrliche Aussage.
    if holdout_passed:
        best_result["metrics_symbol"]["holdout_binding_gate"] = None
        best_result["metrics_symbol"]["holdout_tightest_margin"] = _holdout_tightest_margin(
            best_result["metrics_symbol"]["holdout_gate_deltas"], tournament_cfg)
    else:
        best_result["metrics_symbol"]["holdout_binding_gate"] = _holdout_binding_gate(
            best_result["metrics_symbol"]["holdout_gate_deltas"], tournament_cfg)
        best_result["metrics_symbol"]["holdout_tightest_margin"] = None

    # Issue #668 — die explizite Any-Arm-Policy-Entscheidung im Winner-Eintrag, sobald sie eine
    # Wirkung hatte (Trials wurden gerettet oder wären ohne Policy gerettet worden) — sichtbar,
    # statt den OR-Arm-Kollaps lautlos geschehen zu lassen.
    if any_arm_decision and (any_arm_decision.get("dropped_clauses")
                              or any_arm_decision.get("recalibrated_thresholds")):
        best_result["metrics_symbol"]["any_arm_unreachable_policy"] = any_arm_decision.get("policy")
        best_result["metrics_symbol"]["any_arm_reduced"] = any_arm_decision.get("dropped_clauses")
        best_result["metrics_symbol"]["any_arm_recalibrated_thresholds"] = (
            any_arm_decision.get("recalibrated_thresholds"))

    # Issue #619/#663 — PBO-Telemetrie (Selektions-Overfit-Diagnose). ``pbo_n_groups``/
    # ``pbo_n_configs``/``pbo_metric`` IMMER gefüllt (auch bei ``study_pbo is None``), damit
    # nachvollziehbar bleibt, WARUM kein PBO-Urteil gefällt wurde (zu wenige Configs/Gruppen).
    if study_pbo is not None:
        best_result["metrics_symbol"]["pbo"] = study_pbo
    best_result["metrics_symbol"]["pbo_n_groups"] = pbo_telemetry.get("pbo_n_groups")
    best_result["metrics_symbol"]["pbo_n_configs"] = pbo_telemetry.get("pbo_n_configs")
    # Issue #683 — die ROHE (pre-Clustering) Config-Zahl separat sichtbar, damit nachvollziehbar
    # bleibt, wie stark die Near-Duplicate-Reduktion N tatsächlich reduziert hat.
    best_result["metrics_symbol"]["pbo_n_configs_raw"] = pbo_telemetry.get("pbo_n_configs_raw")
    best_result["metrics_symbol"]["pbo_metric"] = pbo_telemetry.get("pbo_metric")
    # Issue #847 — explizit benannter Alias auf die bereits declusterte Config-Zahl
    # (``pbo_n_configs`` ist laut ``_study_pbo``-Docstring bereits die effektive Zahl; der
    # ausdrückliche Name macht das ohne Docstring-Lektüre nachvollziehbar) + die Schwelle, GEGEN
    # DIE ``pbo_overfit`` oben entschieden wurde — ohne sie ist ein exportiertes ``pbo``-Urteil
    # nicht nachprüfbar (Root-Cause #847: PBO=0.89 war nicht von einem Cluster-Artefakt
    # unterscheidbar ohne die effektive Konfigurationszahl UND die Schwelle nebeneinander).
    best_result["metrics_symbol"]["pbo_n_configs_effective"] = pbo_telemetry.get("pbo_n_configs")
    if study_pbo is not None:
        best_result["metrics_symbol"]["pbo_threshold"] = 0.5
    # Issue #1005 (Katalog #858, Fix-Item 4) — jede Promotion protokolliert, WELCHER Zweig des
    # ``dsr_or_robust_pair``-Ersatzpfads getragen hat (``None`` ausserhalb dieses Modus oder wenn der
    # Ersatzpfad nie betreten wurde, weil die Konjunktion bereits bestand). Ohne dieses Feld ist eine
    # über den Ersatzpfad reinstated Promotion nicht auditierbar.
    if promotion_correction_route is not None:
        best_result["metrics_symbol"]["promotion_correction_route"] = promotion_correction_route
        best_result["metrics_symbol"]["promotion_correction_pbo_ok"] = promotion_correction_pbo_ok
        best_result["metrics_symbol"]["promotion_correction_ci_lower"] = promotion_correction_ci_lower
        best_result["metrics_symbol"]["promotion_correction_n_period_returns"] = (
            promotion_correction_n_period_returns)
        best_result["metrics_symbol"]["promotion_correction_alpha_effective"] = (
            promotion_correction_alpha_effective)
    # Issue #1007 (Katalog #858) — die Namen der blockierenden Invarianten exportiert, WENN der
    # Aufrufer ``study_invariant_results`` tatsaechlich uebergeben hat (auch als leere Liste — "0
    # blockierende Invarianten" ist ein gueltiges Ergebnis). Ist das Feld ABWESEND (Aufrufer hat gar
    # nichts uebergeben, z. B. der Sweep-Dispatch VOR einer kuenftigen Live-Verdrahtung), MUSS
    # deployment_gate.py das als "nicht ueberprueft" lesen (fail-closed, dieselbe Konvention wie
    # jede andere Klausel dieses Moduls) statt es mit "geprueft und sauber" zu verwechseln.
    if study_invariant_results is not None:
        best_result["metrics_symbol"]["blocking_invariant_names"] = blocking_invariant_names
    # Issue #697 — sichtbar machen, falls die Konjunktion trotz #697-Konsolidierung noch ein von der
    # LIVE-Kohorte als redundant markiertes Gate enthält (Regelfall: leere Liste).
    if unconsolidated_gates:
        best_result["metrics_symbol"]["gate_collinearity_unconsolidated"] = unconsolidated_gates
    # Issue #846 (Regressionswaechter #651, 4. Katalog) — Root-Cause der 115x check_sr0_coherence-
    # FAILs: deflation_sr0 wird aus der COHORT-Varianz berechnet (oben, deflation_n >= 2), waehrend
    # deflated_dsr/deflation_dsr_z aus dem SPEZIFISCHEN promoteten Trial (promoted_sr_period)
    # abgeleitet werden — zwei GETRENNTE Gates auf zwei GETRENNTEN Datenmengen. Faellt der promotete
    # Trial selbst durch sein Gate (kein oos_sortino_period), bleibt deflation_sr0 gesetzt, obwohl
    # dsr/dsr_z nie berechnet wurden. Statt eines atomaren DeflationResult-Objekts (grössere
    # Restrukturierung, siehe Katalog) erzwingt dieser Waechter dieselbe Kohaerenz-Garantie an der
    # EXPORT-Grenze: kein Teilzustand verlaesst confirm.py.
    deflation_skipped_reason = None
    if deflated_selection:
        # Issue #845/#865 — VOR der #846-Kohärenzprüfung: eine Kohorte, deren oos_n_periods stärker
        # als ``deflation_max_n_periods_ratio`` streut, macht die gepoolte Kohorten-Varianz
        # (deflation_var) selbst inkommensurabel — unabhängig davon, ob der promotete Trial sein
        # eigenes Gate besteht (der #846-Fall unten). Seit #865 wird die Heterogenität bereits VOR der
        # Promotion-Entscheidung behandelt (``deflation_heterogeneity_policy``, siehe oben); dieser
        # Block hier ist nur noch die EXPORT-Kohärenzprüfung: ``deflation_sr0 is None`` bei
        # ``deflation_heterogeneous`` heisst "Policy hat die Kohorte verworfen/das Stratum war zu
        # klein" (SR₀/DSR NICHT exportiert). War die Politik ``per_stratum`` UND das Stratum gross
        # genug, ist ``deflation_sr0`` bereits die STRATIFIZIERTE (kommensurable) Grösse — dann ist
        # dies KEIN Skip, sondern ein regulärer (kleinerer) Kohorten-Erfolg.
        if deflation_heterogeneous and deflation_sr0 is None:
            deflation_skipped_reason = "N_PERIODS_HETEROGENEOUS"
            deflation_dsr = None
            deflation_dsr_z = None
        elif deflation_sr0 is None and deflation_dsr is None and deflation_dsr_z is None:
            deflation_skipped_reason = "SMALL_COHORT" if deflation_n < 2 else "NO_STATISTIC"
        elif deflation_sr0 is not None and deflation_dsr is None and deflation_dsr_z is None:
            deflation_skipped_reason = "NO_STATISTIC"
            deflation_sr0 = None  # Koharenz erzwingen: keine Telemetrie ohne Entscheidungsgroesse

    # Issue #611/#618/#636 — DSR-Telemetrie (Sortino-Skala) statt der alten Reward-Schwelle. Seit
    # #636 IMMER gefüllt, sobald SR₀ berechenbar war (unabhängig von holdout_passed) — deflated_dsr
    # ist damit nie mehr uninformativ-null, nur weil ein früheres Gate schon ablehnte.
    if deflation_sr0 is not None:
        best_result["metrics_symbol"]["deflated_sr0"] = deflation_sr0
        best_result["metrics_symbol"]["deflated_dsr"] = deflation_dsr
        best_result["metrics_symbol"]["deflation_dsr_z"] = deflation_dsr_z
        # Issue #758 — welche Inferenzmethode DIESE DSR tatsaechlich lieferte; im Regelfall
        # identisch zur Eligibility-Methode (backtest_runner.psr_inference_method).
        best_result["metrics_symbol"]["deflation_inference_method"] = deflation_inference_method
        best_result["metrics_symbol"]["deflation_n_eligible"] = deflation_n
        # Issue #670 — ``deflation_used_var_floor`` bleibt aus Rückwärtskompat-Gründen erhalten
        # (bedeutet NUR "λ ≥ 0.5"). Die PRÄZISEN Grössen für die Forensik: ``deflation_lambda`` (das
        # tatsächliche Shrinkage-Gewicht) und ``deflation_theoretical_var_source`` (seit #701 IMMER
        # ``'lo2002'`` — der var_floor-Fallback wurde nach Verifikation als tot entfernt).
        best_result["metrics_symbol"]["deflation_used_var_floor"] = deflation_used_var_floor
        best_result["metrics_symbol"]["deflation_lambda"] = deflation_lambda
        best_result["metrics_symbol"]["deflation_theoretical_var_source"] = deflation_theoretical_var_source
        # Issue #814 — der additive Suchraum-Kapazitäts-Term, falls konfiguriert (None/0 im Regelfall).
        best_result["metrics_symbol"]["deflation_search_space_penalty"] = deflation_search_space_penalty
        # Issue #652 — die familienweite Multiplizität, die tatsächlich in die SR₀-Berechnung
        # eingeflossen ist (N_effective = max(per-Study-N, N_family)), plus die rohe übergebene
        # Familien-Zahl — beide sichtbar im Proposal, damit die effektive SR₀-Hürde je promoteter
        # Kandidat nachvollziehbar ist (Akzeptanzkriterium #652). ``deflation_n_family`` (Legacy-Name)
        # bleibt bit-identisch der roh übergebene Skalar (Rückwärtskompat, siehe test_issue_652).
        best_result["metrics_symbol"]["deflation_n_family"] = int(deflation_n_family or 0)
        # Issue #822 Fix Punkt 3 — wie viele familienweite Kandidaten aus der Zaehlung
        # AUSGESCHLOSSEN wurden, weil sie trotz ``oos_evaluated=True`` KEINE verwertbare
        # Selektions-Teststatistik trugen (``oos_selection_statistic_available=False``),
        # aufgeschluesselt nach Grund (``sortino_guard``/``equity_ruined``/``other``) — macht den
        # Nicht-Ausgang der #822-Zaehlung genauso sichtbar wie das Ergebnis selbst (Lehre aus
        # #783/#786). ``None``/fehlend (Legacy-Aufrufer) ⇒ leeres Dict, bit-identisch.
        if deflation_n_family_excluded_no_statistic is not None:
            best_result["metrics_symbol"]["deflation_n_family_excluded_no_statistic"] = dict(
                deflation_n_family_excluded_no_statistic)
        # Issue #695/#696 — ``deflation_n_effective`` (Legacy-Name, #652) war bislang ein Fehlname:
        # sie trug die ROHE (un-declusterte) Familien-Multiplizität, obwohl der Name "effektiv"
        # suggeriert (dieselbe Fehldeutungsklasse wie #670 bei ``deflation_used_var_floor``). Seit
        # #695 ist der Wert, den ``deflation_n_effective`` trägt, TATSÄCHLICH effektiv (die
        # declusterte Familienzahl treibt jetzt E[max_N]) — die zwei PRÄZISEN, PBO-Pfad-parallelen
        # Grössen (``pbo_n_configs_raw``/``pbo_n_configs``-Analogon) sind zusätzlich EXPLIZIT
        # benannt, damit ein Operator roh vs. declustert auditieren kann, ohne den Legacy-Namen
        # (miss-)interpretieren zu müssen (Akzeptanzkriterium #696).
        best_result["metrics_symbol"]["deflation_n_family_raw"] = deflation_n_family_raw
        best_result["metrics_symbol"]["deflation_n_family_effective"] = deflation_n_family_effective
        best_result["metrics_symbol"]["deflation_n_effective"] = deflation_n_effective
        # Issue #813 — Anteil der gezaehlten (oos_evaluated) Kandidaten, fuer die ueberhaupt eine
        # Renditeserie zur Declusterung vorlag; < min_deflation_cluster_coverage ist ein Invarianten-
        # FAIL (invariants.check_deflation_cluster_coverage) — vor #813 lag dieser Wert bei ~13 %
        # (oos_period_returns wurde nur fuer eligible Trials gestempelt, deflation_n_family zaehlt
        # seit #784 aber ALLE oos_evaluated Trials).
        best_result["metrics_symbol"]["deflation_cluster_coverage"] = deflation_cluster_coverage
    if deflation_skipped_reason is not None:
        best_result["metrics_symbol"]["deflation_skipped_reason"] = deflation_skipped_reason
    # Issue #845 — Ratio-Telemetrie unabhängig vom Ausgang exportiert (auch wenn sie NICHT die
    # Ausloeserin des Skips war), damit ein Operator die Kohärenz-Kohorten-Heterogenität jeder
    # Study auditieren kann, ohne die Rohdaten (Trial-User-Attrs) erneut laden zu müssen.
    if deflation_n_periods_ratio is not None:
        best_result["metrics_symbol"]["deflation_n_periods_ratio"] = deflation_n_periods_ratio
    # Issue #865 — welche Politik GRIFF (unabhängig davon, ob die Kohorte tatsächlich heterogen war —
    # macht den Konfigurationszustand selbst auditierbar) + das gewählte Stratum, sobald
    # ``per_stratum`` tatsächlich stratifiziert hat (None/None im Regelfall einer homogenen Kohorte).
    best_result["metrics_symbol"]["deflation_heterogeneity_policy"] = deflation_heterogeneity_policy
    if deflation_stratum_id is not None:
        best_result["metrics_symbol"]["deflation_stratum_id"] = deflation_stratum_id
        best_result["metrics_symbol"]["deflation_stratum_n"] = deflation_stratum_n
        # Issue #1013 (Katalog #858, Fix Punkt 2) — ohne dieses Feld ist NICHT prüfbar, ob das
        # gewählte Stratum selbst kommensurabel ist (invariants.check_family_n_periods_homogeneity).
        if deflation_stratum_n_periods_ratio is not None:
            best_result["metrics_symbol"]["deflation_stratum_n_periods_ratio"] = (
                deflation_stratum_n_periods_ratio)

    return best_result


# Issue #671 — Confirm-/Holdout-Ursachen, die VORAUSSETZEN, dass die Selektion tatsächlich einen
# Holdout-Lauf erreichte (mind. 1 eligible Trial in der Top-k-Selektion) — im Unterschied zu
# ``HOLDOUT_NO_ELIGIBLE_TRIALS``/``REJECT_HOLDOUT_UNREACHABLE``, die VOR jedem Holdout-Backtest
# greifen. Für diese Ursachen ist der modale PER-TRIAL IS-Grund (``_dominant_rejection``) keine
# sinnvolle Erklärung der Promotion-Ablehnung — die Strategie hatte eligible IS-Trials, scheiterte
# aber am Confirm-/Holdout-Pfad selbst.
_CONFIRM_STAGE_REJECTIONS = frozenset({
    "REJECT_HOLDOUT_GATE", "REJECT_HOLDOUT_DSR_DROP", "REJECT_HOLDOUT_BOOTSTRAP_CI",
    # Issue #865 — REJECT_DEFLATION_HETEROGENEOUS ist ebenfalls eine Confirm-/Holdout-Pfad-Ursache
    # (der modale Per-Trial-IS-Grund erklärt nicht, warum die DSR-Korrektur selbst undefiniert war).
    "REJECT_DEFLATION_HETEROGENEOUS",
    "REJECT_SELECTION_PBO", "REJECT_BOUNDARY_SOLUTION", "REJECT_NO_EDGE_OVER_GLOBAL",
    # Issue #763 — HOLD_BOUNDARY_UNRESOLVED ist ebenfalls eine Confirm-/Holdout-Pfad-Ursache (der
    # modale Per-Trial-IS-Grund erklärt nicht, warum die Promotion pausiert wurde).
    "HOLD_BOUNDARY_UNRESOLVED",
    # Issue #773 — die Study wurde VOR jedem Holdout-Backtest wegen einer verletzten
    # Renditeserien-Kohaerenz abgelehnt; der modale IS-Per-Trial-Grund erklaert diese Ursache nicht.
    "REJECT_COHERENCE_VIOLATION",
})


def _dominant_rejection(study) -> str | None:
    """Issue #408 — modale Per-Trial-Rejection-Reason ueber alle Trials der Study (Counter).

    Macht im Proposal sichtbar, WARUM ein Symbol nicht promotet wurde. Klebt z. B. jeder Trial auf
    'oos_not_evaluated', ist das die Pitfall-#75-Signatur (das Symbol erzeugte nie evaluierbare
    OOS-Trades). Trials ohne `rejection_reason` (Legacy/gepruned) werden ignoriert; gibt es keine,
    ist die Reason `None`."""
    reasons = [t.user_attrs.get("rejection_reason") for t in study.trials
               if t.user_attrs.get("rejection_reason")]
    if not reasons:
        return None
    return Counter(reasons).most_common(1)[0][0]


def _dominant_is_rejection_detail(study) -> str | None:
    """Issue #453 — modale GRANULARE Ablehnungs-Kategorie (``is_rejection_detail``-User-Attr) über
    alle Trials. Wo ``_dominant_rejection`` nur grob 'oos_not_evaluated' liefert, macht dies die
    tatsächliche dominante Ursache sichtbar (z. B. ``REJECT_OOS_WINDOW_UNREACHABLE`` ⇒ Katalog-H2
    auffrischen statt Parameter tunen; ``REJECT_OOS_MAX_DRAWDOWN`` ⇒ Risiko-Constraint). Trials ohne
    das Attr (Legacy/gepruned) werden ignoriert; gibt es keine, ist die Kategorie ``None``.

    Issue #744 — WARNUNG: dies ist die SEKUNDÄRE IS-Study-Diagnose ("warum scheiterten die meisten
    IS-Trials"), NICHT die Promotion-Ablehnung. Die tatsächliche, Promotion-blockierende Ursache
    steht in ``holdout_reject_detail``/``is_rejection_detail_override`` (siehe
    ``export_symbol_proposal`` unten) — eine Verwechslung der beiden Felder war bereits die
    #654-Root-Cause. Das Ergebnis dieser Funktion landet im Proposal ausschliesslich unter
    ``dominant_is_rejection_detail`` (und, unmissverständlich alias-benannt,
    ``legacy_modal_is_rejection_detail``), NIE unter ``holdout_reject_detail``."""
    details = [t.user_attrs.get("is_rejection_detail") for t in study.trials
               if t.user_attrs.get("is_rejection_detail")]
    if not details:
        return None
    return Counter(details).most_common(1)[0][0]


def export_symbol_proposal(study, strategy: str, symbol: str, promotion: dict) -> Path:
    """Schreibt data/optimizer/proposal_{strategy}_{symbol}.json. Schreibt NIE in strategies.json —
    Promotion erfolgt ausschließlich per menschlich freigegebenem PR (HI-3)."""
    try:
        _reward = study.best_value if len(getattr(study, "directions", ["maximize"])) == 1 else promotion.get("R_symbol", 0.0)
    except ValueError:
        _reward = None

    # Issue #671 — die EINE gewinnende Ursache aus dem Confirm-Pfad (bereits seit #654 exakt
    # gesetzt für JEDEN Ausgang: REJECT_HOLDOUT_GATE/_DSR_DROP/_BOOTSTRAP_CI, REJECT_SELECTION_PBO,
    # REJECT_BOUNDARY_SOLUTION, REJECT_NO_EDGE_OVER_GLOBAL, HOLDOUT_NO_ELIGIBLE_TRIALS,
    # REJECT_HOLDOUT_UNREACHABLE, oder None bei READY_FOR_PR).
    holdout_reject_detail = promotion.get("is_rejection_detail_override")
    # Issue #671 — dominant_rejection (Top-Level) auf die CONFIRM-Ursache ausrichten, sobald die
    # Strategie tatsächlich einen Holdout-Lauf erreichte: der modale PER-TRIAL IS-Grund
    # (_dominant_rejection) erklärt in diesem Fall NICHT, warum die Promotion scheiterte — sie
    # scheiterte am Confirm-/Holdout-Pfad, nicht an einem IS-Gate. Nur wenn die Selektion NIE einen
    # Holdout-Lauf erreichte (HOLDOUT_NO_ELIGIBLE_TRIALS/REJECT_HOLDOUT_UNREACHABLE/READY_FOR_PR)
    # bleibt der modale IS-Grund die sinnvollste Top-Level-Erklärung.
    if holdout_reject_detail in _CONFIRM_STAGE_REJECTIONS:
        dominant_rejection = holdout_reject_detail
    else:
        dominant_rejection = _dominant_rejection(study)

    payload = {
        "strategy": strategy,
        "symbol": symbol,
        "status": promotion["status"],
        "reward": _reward,
        "proposed_instrument_override": promotion["symbol_params"],
        "R_symbol": promotion["R_symbol"],
        "R_global": promotion["R_global"],
        "promotion_margin": promotion["promotion_margin"],
        # Issue #682 — explizite Attribution, wenn der Kandidat NICHT aus einem symbol-eligiblen
        # Trial stammt, sondern der GLOBALE Default auf dem Symbol-Holdout selbst promotet wurde
        # (kein Micro-Tuning-Anspruch). ``None`` für den regulären Per-Symbol-Trial-Pfad.
        "promotion_route": promotion.get("promotion_route"),
        # Issue #408/#671 — modale Gate-Drop-Reason ueber alle Trials, ES SEI DENN die Promotion
        # scheiterte nachweislich am Confirm-/Holdout-Pfad (siehe _CONFIRM_STAGE_REJECTIONS oben) —
        # dann zeigt dieses Top-Level-Feld die ECHTE Ursache statt des irreführenden IS-Per-Trial-Grunds.
        "dominant_rejection": dominant_rejection,
        # Issue #453/#654 — die TATSÄCHLICHE Promotion-Ursache (REJECT_HOLDOUT_DSR_DROP,
        # REJECT_HOLDOUT_BOOTSTRAP_CI, REJECT_SELECTION_PBO, REJECT_BOUNDARY_SOLUTION,
        # REJECT_NO_EDGE_OVER_GLOBAL, REJECT_HOLDOUT_GATE, oder None bei READY_FOR_PR) —
        # ``confirm_per_symbol_promotion`` setzt ``is_rejection_detail_override`` jetzt für JEDEN
        # Ausgang explizit (#654; vorher fiel dies auf den modalen IS-Study-Trial-Grund zurück, der
        # mit der Holdout-/Promotion-Entscheidung nichts zu tun hat — kein OR-Fallback mehr).
        # Beibehalten für Rückwärtskompatibilität; ``holdout_reject_detail`` (unten) ist das
        # erstklassige, eindeutig benannte Äquivalent (#671).
        "is_rejection_detail": holdout_reject_detail,
        # Issue #671 — ERSTKLASSIGES Feld: dieselbe, EINE gewinnende Confirm-/Holdout-Ursache unter
        # einem eindeutigen Namen — vorher versteckte sich die tatsächlich blockierende Ursache
        # zwischen zwei ähnlich benannten *_rejection_detail-Feldern (is_rejection_detail vs.
        # dominant_is_rejection_detail), von denen nur eines (das hier) die Promotion-Entscheidung
        # tatsächlich erklärt.
        "holdout_reject_detail": holdout_reject_detail,
        # Issue #453/#654/#671 — der modale IS-Study-Trial-Grund: reine SEKUNDÄRE Study-Diagnose
        # ('warum scheiterten die meisten IS-Trials dieser Study'), NIEMALS die Promotion-
        # blockierende Ursache — unabhängig davon, ob ``holdout_reject_detail`` gesetzt ist.
        "dominant_is_rejection_detail": _dominant_is_rejection_detail(study),
        # Issue #744 — unmissverständlich benannter Alias VON dominant_is_rejection_detail (nicht
        # von is_rejection_detail!): macht auf den ersten Blick klar, dass dies die alte, modale
        # IS-Study-Diagnose ist ("legacy"), nicht die Promotion-Ursache — ohne Rückwärtskompat der
        # bestehenden Feldnamen zu brechen (beide bleiben erhalten, falls ein externer Konsument
        # sie liest). Ko-präsent und WERTGLEICH zu dominant_is_rejection_detail per Konstruktion.
        "legacy_modal_is_rejection_detail": _dominant_is_rejection_detail(study),
        # Issue #615 — der EINE Holdout-trial_dir, aus dem der promotete Vektor (Params/R_symbol/Gate)
        # stammt: macht die Kohärenz-Invariante im Proposal nachvollziehbar.
        "holdout_trial_dir": promotion.get("trial_dir"),
        # Issue #993 — durchgereicht fuer die Deployment-Grenze (deployment_gate.py); siehe
        # confirm_per_symbol_promotion's ``data_snapshot_sha256``.
        "data_snapshot_sha256": promotion.get("data_snapshot_sha256"),
        # Issue #1091 (Katalog #924) — die VOR dem ersten Trial dieses Symbols aus dem geplanten
        # Budget eingefrorene familienweite Multiplizitaet (siehe ``sweep._family_n_frozen_from_
        # studies``/``study.user_attrs['deflation_n_family_frozen']``). Anders als
        # ``deflation_n_eligible`` (unten, im ``holdout``-Block) haengt dieser Wert NICHT davon ab,
        # wie viele Studies dieses Symbols zum Lesezeitpunkt bereits ihr Proposal exportiert haben
        # — jede Study derselben Symbol-Familie traegt DENSELBEN, bereits symbolweit summierten Wert.
        "deflation_n_family_frozen": (getattr(study, "user_attrs", None) or {}).get(
            "deflation_n_family_frozen"),
        "holdout": {
            "symbol": promotion["metrics_symbol"],
            "global": promotion["metrics_global"],
        },
    }

    out_path = WORK / f"proposal_{strategy}_{symbol}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


def export_no_viable_proposal(study, strategy: str) -> Path:
    """
    Exportiert ein Proposal mit status="NO_VIABLE_TRIAL", falls alle Trials gepruned wurden.
    """
    payload = {
        "strategy": strategy,
        "status": "NO_VIABLE_TRIAL",
        "reward": None,
        "proposed_params_override": {},
        "note": "No valid trial found due to data coverage or OOS gating rejection.",
        "holdout": None
    }

    out_path = WORK / f"proposal_{strategy}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path


def export_proposal(study, strategy: str, holdout: dict) -> Path:
    """
    Schreibt data/optimizer/proposal_<strategy>.json mit proposed_params_override
    (best_trial.user_attrs['sampled_params']), Reward, Holdout-Metriken,
    status = 'READY_FOR_PR' wenn holdout['passed'] sonst 'REJECTED_ON_HOLDOUT'.
    """
    best_trial = study.best_trials[0] if len(getattr(study, "directions", ["maximize"])) > 1 else study.best_trial
    sampled = best_trial.user_attrs.get("sampled_params", best_trial.params)

    status = "READY_FOR_PR" if holdout.get("passed") else "REJECTED_ON_HOLDOUT"

    payload = {
        "strategy": strategy,
        "status": status,
        "reward": best_trial.value,
        "proposed_params_override": sampled,
        "holdout": holdout
    }

    out_path = WORK / f"proposal_{strategy}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return out_path
