"""Champion-Store (Epic #702, Ebene 1 + Ebene 2 — Issues #703-#710).

Iterativer Warm-Start & symbol-skopierte Default-Nachführung, OHNE die Selektions-Integrität zu
brechen, die die Optimizer-Härtung seit #649 aufgebaut hat: ein Champion ist immer nur ein
zusätzlicher Enqueue-Kandidat, der beim nächsten Sweep-Lauf erneut VOLLSTÄNDIG durch alle
Eligibility-/Holdout-/DSR-Gates läuft (integritätsneutral) — er drückt nie unvalidierte,
symbol-übergefittete Parameter direkt in einen Live-Pfad.

Drei Ebenen, drei Gates (siehe Issue #705 §3):
    Ebene 1 (Such-Anker, jeder Lauf, niedriges Risiko):
        ``store_champion`` (#703) persistiert den besten *erreichten* Holdout-Kandidaten je
        (Strategie, Symbol) unter ``data/optimizer/champions/``; ``load_champion_seed``/
        ``load_champion_entry`` (#704) lesen ihn als Warm-Start-Seed (zwischen ``global_best``
        und ``strategy_defaults`` in der Tier-Reihenfolge von
        ``run_optimization.resolve_symbol_shrinkage_seed``).
    Ebene 2 (Default-Nachführung, korroboriert, mittleres Risiko):
        ``maybe_write_back`` (#706) schreibt einen Champion NUR nach ``strategy_symbol_seeds.json``
        (symbol-skopiert, NIE ``strategy_defaults.json``), wenn er entweder eine echte
        READY_FOR_PR-Promotion ist ODER über ``champion_promote_after_runs`` Läufe UND ein
        fortgeschrittenes Datenfenster (Snooping-Schutz) korroboriert wurde.
    Ebene 3 (Live-Deployment) bleibt UNVERÄNDERT menschlich (HI-3): dieses Modul schreibt
        NIEMALS ``strategies.json``.

``champion_is_admissible`` (#705) ist die EINE zentrale Guard-Funktion, die sowohl beim Schreiben
(``store_champion``) als auch beim Lesen (``load_champion_entry``) entscheidet, ob ein
Champion-Kandidat/-Eintrag zulässig ist — reward-version, override-keys, Rejection-Allowlist,
R_symbol-Floor, Demotion-Schwelle. Keine Guard-Logik ist im Sweep/Resolver dupliziert.
"""
import json
import logging
from pathlib import Path

from automation.optimizer.manifest import WORK, catalog_fingerprint
from automation.log_manager import emit_execution_event

# Issue #703 (Gate, Punkt 2) — Rejections, die bedeuten, dass der Kandidat den Holdout nie erreicht
# hat (kein Backtest-Ergebnis, aus dem ein Seed sinnvoll wäre).
_UNREACHED_HOLDOUT_REJECT_DETAILS = frozenset({
    "HOLDOUT_NO_ELIGIBLE_TRIALS",
    "REJECT_HOLDOUT_UNREACHABLE",
})
# Issue #703 (Gate, Punkt 2) — Status-Werte, die grundsätzlich keinen speicherwürdigen Kandidaten
# tragen (kein abgeschlossener Trial überhaupt).
_INADMISSIBLE_STATUS = frozenset({"NO_VIABLE_TRIAL"})
# Issue #703 (Gate, Punkt 2) — Rejection-Allowlist fürs Seeding: der Kandidat hat den Holdout
# ERREICHT und ist entweder eine echte Promotion (None ⇔ READY_FOR_PR) oder an einer der drei
# Confirm-Stufen gescheitert, die dennoch einen validen, evaluierten Parametervektor tragen.
# AUSGESCHLOSSEN: REJECT_SELECTION_PBO (Overfit-geflaggt) und REJECT_BOUNDARY_SOLUTION (Kandidat
# klebt an der Bounds-Kante) — beide würden die Folgesuche destabilisieren, statt sie zu verankern.
_ADMISSIBLE_HOLDOUT_REJECT_DETAILS = frozenset({
    None,
    "REJECT_HOLDOUT_DSR_DROP",
    "REJECT_HOLDOUT_BOOTSTRAP_CI",
    "REJECT_HOLDOUT_GATE",
})


def _sanitize(symbol: str) -> str:
    """'TSLA.ETORO' -> 'TSLA_ETORO'. Bewusst hier dupliziert (nicht aus run_optimization
    importiert) — eine triviale, reine String-Transformation; ein Modul-Import würde einen
    Zirkelimport riskieren, da run_optimization.py umgekehrt dieses Modul importiert (#704)."""
    return symbol.replace(".", "_")


def _champions_dir() -> Path:
    d = WORK / "champions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _champion_path(strategy: str, symbol: str) -> Path:
    return _champions_dir() / f"champion_{strategy}_{_sanitize(symbol)}.json"


def _read_entry(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_entry(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)


def _run_id(now=None) -> str:
    import datetime as dt
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _same_region(strategy: str, params_a: dict, params_b: dict, opt_data: dict) -> bool:
    """Issue #707 — Regionsgleichheit: dieselbe normierte, ``[0,1]``-skalierte Parameter-Distanz
    wie die A4.3-Shrinkage (``bounds.normalized_param_distance`` gegen die ``spaces.py``-Bandbreite
    jedes Parameters) — bewusst dieselbe, bereits etablierte Metrik statt einer zweiten,
    abweichenden Distanzdefinition (DRY, konsistente Skala über den gesamten Optimizer). Innerhalb
    ``champion_region_eps`` (Default 0.10) ⇒ derselbe Champion (Korroboration); sonst ein neuer."""
    if not params_a or not params_b:
        return False
    from automation.optimizer import bounds
    try:
        b = bounds.extract_numeric_bounds(strategy)
    except ValueError:
        return False
    eps = float(opt_data.get("champion_region_eps", 0.10))
    return bounds.normalized_param_distance(params_a, params_b, b) <= eps


def champion_is_admissible(entry: dict, opt_data: dict) -> tuple[bool, str | None]:
    """Issue #705 (P0) — die EINE zentrale Guard-Einheit für Champion-Zulässigkeit. Wird sowohl
    beim Schreiben (``store_champion``, gegen einen frisch gebauten Kandidaten) als auch beim
    Lesen (``load_champion_entry``, gegen den gespeicherten Eintrag) aufgerufen — identische
    Prüfkette, keine duplizierte Guard-Logik in sweep.py/run_optimization.py.

    Reines Prädikat: keine I/O, keine Mutation. Rückgabe ``(ok, reason)`` — ``reason`` ist ``None``
    bei ``ok=True``, sonst ein stabiler Kurzcode (analog ``gate.is_symbol_tunable``) für Tests/Logs.

    Geprüfte Bedingungen (Issue #703 Punkt 2 + #705 Zentralisierung):
      1. ``champion_enabled`` (globaler Kill-Switch, Default ``true``).
      2. ``params`` nicht-leer (override-keys > 0).
      3. ``status_at_store`` nicht in {NO_VIABLE_TRIAL}.
      4. ``holdout_reject_detail`` hat den Holdout erreicht (nicht HOLDOUT_NO_ELIGIBLE_TRIALS/
         REJECT_HOLDOUT_UNREACHABLE) UND liegt in der Rejection-Allowlist.
      5. ``R_symbol >= champion_min_R_symbol`` (Qualitäts-Floor, Default 0.0).
      6. ``reward_semantics_version`` des Eintrags == der aktuellen Config (sonst nicht mehr
         vergleichbar, siehe ``run_optimization._check_reward_semantics_version``).
      7. ``degrade_streak < champion_demote_after_runs`` (#708 — nicht demoted).
    """
    if not opt_data.get("champion_enabled", True):
        return False, "CHAMPION_DISABLED"

    params = entry.get("params") or {}
    if not params:
        return False, "EMPTY_PARAMS"

    provenance = entry.get("provenance") or {}
    if provenance.get("status_at_store") in _INADMISSIBLE_STATUS:
        return False, "INADMISSIBLE_STATUS"
    reject_detail = provenance.get("holdout_reject_detail")
    if reject_detail in _UNREACHED_HOLDOUT_REJECT_DETAILS:
        return False, "HOLDOUT_UNREACHED"
    if reject_detail not in _ADMISSIBLE_HOLDOUT_REJECT_DETAILS:
        return False, "REJECTION_NOT_ALLOWLISTED"

    quality = entry.get("quality") or {}
    r_symbol = quality.get("R_symbol")
    min_r = float(opt_data.get("champion_min_R_symbol", 0.0))
    if r_symbol is None or r_symbol < min_r:
        return False, "BELOW_QUALITY_FLOOR"

    integrity = entry.get("integrity") or {}
    reward_version = opt_data.get("reward_semantics_version")
    if reward_version is None or integrity.get("reward_semantics_version") != reward_version:
        return False, "REWARD_SEMANTICS_MISMATCH"

    lifecycle = entry.get("lifecycle") or {}
    degrade_streak = int(lifecycle.get("degrade_streak", 0) or 0)
    demote_after = int(opt_data.get("champion_demote_after_runs", 2))
    if degrade_streak >= demote_after:
        return False, "DEMOTED"

    return True, None


def _build_entry_from_promotion(study, strategy: str, symbol: str, promotion: dict, *,
                                catalog_newest_ns: int | None, opt_data: dict,
                                tier: str = "all") -> dict:
    """Issue #703 (Datenmodell §4) — baut die Champion-Store-Repräsentation AUSSCHLIESSLICH aus
    bereits im Proposal/Study vorhandenen Feldern (keine neue Berechnung): ``params`` ==
    ``promotion['symbol_params']`` (== das spätere ``proposed_instrument_override``),
    ``R_symbol``/``R_global``/``holdout_reject_detail`` (== ``is_rejection_detail_override``)/
    ``status`` == dieselben Felder, die ``confirm.export_symbol_proposal`` in
    ``proposal_{strategy}_{symbol}.json`` schreibt; ``reward_semantics_version`` aus
    ``optimizer.json``; ``data_snapshot_sha256`` aus ``manifest.catalog_fingerprint()``."""
    try:
        reward = (study.best_value
                  if len(getattr(study, "directions", ["maximize"])) == 1
                  else promotion.get("R_symbol", 0.0))
    except ValueError:
        reward = None
    r_symbol = promotion.get("R_symbol")
    run_id = _run_id()
    return {
        "strategy": strategy,
        "symbol": symbol,
        "tier": tier,
        "params": dict(promotion.get("symbol_params") or {}),
        "quality": {
            "R_symbol": r_symbol,
            "R_global": promotion.get("R_global"),
            "reward": reward,
        },
        "provenance": {
            "run_id": run_id,
            "source_trial_dir": promotion.get("trial_dir"),
            "holdout_reject_detail": promotion.get("is_rejection_detail_override"),
            "status_at_store": promotion.get("status"),
        },
        "integrity": {
            "reward_semantics_version": opt_data.get("reward_semantics_version"),
            "data_snapshot_sha256": catalog_fingerprint(),
            "catalog_newest_ns": catalog_newest_ns,
        },
        "lifecycle": {
            "first_seen_run": run_id,
            "first_seen_catalog_newest_ns": catalog_newest_ns,
            "corroboration_count": 1,
            "last_R_symbol": r_symbol,
            "degrade_streak": 0,
            "writeback_applied": False,
        },
    }


def store_champion(study, strategy: str, symbol: str, promotion: dict, *,
                   catalog_newest_ns: int | None, opt_data: dict, tier: str = "all") -> Path | None:
    """Issue #703 (P0) — persistiert den Ebene-1-Suchanker für (strategy, symbol).

    Aufgerufen von ``sweep.py`` UNMITTELBAR NACH ``export_symbol_proposal`` — jeder Sweep-Lauf,
    unabhängig vom Promotion-Status (Ebene 1 ist integritätsneutral: der Seed durchläuft beim
    nächsten Enqueue erneut ALLE Gates auf frischen Daten). Ändert NIEMALS ``strategies.json``
    (HI-3) — ausschliesslich ``data/optimizer/champions/*.json``. Ändert nie die aktuelle
    Promotion-Entscheidung selbst (rein additiv, kein Rückkanal in ``promotion``).

    Merge-Logik gegen einen bestehenden Eintrag (Issue #703 Punkt 3 + #707/#708):
      - ``reward_semantics_version``-Mismatch ⇒ alten Eintrag verwerfen & ersetzen.
      - Region-gleich (#707) zum gespeicherten Champion, aber unter dem Qualitäts-Floor ⇒
        Regime-Degradation (#708): ``degrade_streak += 1``; erreicht sie
        ``champion_demote_after_runs`` ⇒ Demotion (Store- + Seed-Eintrag entfernt).
      - Region-gleich UND admissible ⇒ ``corroboration_count += 1``; Übernahme des besseren
        ``R_symbol``-Vektors (#707 „Erhalt vs. Ersetzung").
      - Region-ungleich UND besser (höheres ``R_symbol``) ⇒ neuer Champion (``count = 1``).
      - Region-ungleich UND nicht besser ⇒ gespeicherter Champion bleibt unangetastet.

    Returns den Pfad der (ggf. aktualisierten) Store-Datei, oder ``None``, wenn der Kandidat
    dieses Laufs selbst nicht speicherwürdig ist (ein zuvor auf demselben Weg erkannter
    Degrade-/Demotion-Effekt auf einen BESTEHENDEN Eintrag bleibt davon unberührt persistiert)."""
    if not opt_data.get("champion_enabled", True):
        return None

    path = _champion_path(strategy, symbol)
    existing = _read_entry(path)
    reward_version = opt_data.get("reward_semantics_version")
    if existing is not None and (existing.get("integrity") or {}).get(
            "reward_semantics_version") != reward_version:
        existing = None  # Semantik nicht vergleichbar (parallel zu _check_reward_semantics_version)

    candidate_params = dict(promotion.get("symbol_params") or {})
    candidate_r_symbol = promotion.get("R_symbol")

    # #708 — Degrade-Signal: eine ERNEUTE Evaluierung DERSELBEN Region unter dem Qualitäts-Floor
    # zählt als Regime-Degradation, UNABHÄNGIG davon, ob der neue Kandidat selbst admissible ist
    # (ein unter-dem-Floor-liegender Kandidat ist gerade das Degrade-Signal, das erkannt werden soll).
    if existing is not None and candidate_params and candidate_r_symbol is not None:
        if _same_region(strategy, candidate_params, existing.get("params") or {}, opt_data):
            min_r = float(opt_data.get("champion_min_R_symbol", 0.0))
            lifecycle = existing.setdefault("lifecycle", {})
            if candidate_r_symbol < min_r:
                lifecycle["degrade_streak"] = int(lifecycle.get("degrade_streak", 0) or 0) + 1
            else:
                lifecycle["degrade_streak"] = 0
            demote_after = int(opt_data.get("champion_demote_after_runs", 2))
            if lifecycle["degrade_streak"] >= demote_after:
                _demote_champion(strategy, symbol, path, reason="degrade_streak")
                existing = None
            else:
                _write_entry(path, existing)

    candidate_entry = _build_entry_from_promotion(
        study, strategy, symbol, promotion,
        catalog_newest_ns=catalog_newest_ns, opt_data=opt_data, tier=tier,
    )
    ok, _reason = champion_is_admissible(candidate_entry, opt_data)
    if not ok:
        return None  # Kandidat selbst nicht speicherwürdig; ein Degrade-Update oben bleibt bestehen.

    if existing is None:
        merged = candidate_entry
    else:
        region_equal = _same_region(strategy, candidate_entry["params"],
                                    existing.get("params") or {}, opt_data)
        existing_r = (existing.get("quality") or {}).get("R_symbol")
        better = existing_r is None or candidate_r_symbol > existing_r
        if region_equal:
            merged = candidate_entry if better else existing
            if better:
                merged["lifecycle"]["writeback_applied"] = False  # Params geändert -> re-validieren
            prior_lifecycle = existing.get("lifecycle") or {}
            merged.setdefault("lifecycle", {})
            merged["lifecycle"]["corroboration_count"] = int(
                prior_lifecycle.get("corroboration_count", 1) or 1) + 1
            merged["lifecycle"]["first_seen_run"] = prior_lifecycle.get(
                "first_seen_run", merged["lifecycle"].get("first_seen_run"))
            merged["lifecycle"]["first_seen_catalog_newest_ns"] = prior_lifecycle.get(
                "first_seen_catalog_newest_ns", merged["lifecycle"].get("first_seen_catalog_newest_ns"))
            merged["lifecycle"]["degrade_streak"] = prior_lifecycle.get("degrade_streak", 0)
            merged["lifecycle"]["last_R_symbol"] = candidate_r_symbol
            merged.setdefault("integrity", {})
            merged["integrity"]["catalog_newest_ns"] = catalog_newest_ns
            merged["integrity"]["data_snapshot_sha256"] = candidate_entry["integrity"]["data_snapshot_sha256"]
        elif better:
            merged = candidate_entry  # neue, bessere Region -- frischer Lifecycle
        else:
            merged = existing  # schlechter & andere Region -- gespeicherten Champion unangetastet lassen

    _write_entry(path, merged)
    return path


def _demote_champion(strategy: str, symbol: str, path: Path, *, reason: str) -> None:
    """Issue #708 (P2) — Regime-Degradation: Store-Eintrag UND ein etwaiger
    ``strategy_symbol_seeds.json``-Eintrag werden entfernt; die Seed-Auflösung fällt auf
    ``strategy_defaults`` zurück (kein stale Optimum ankert die Suche dauerhaft am falschen Punkt)."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    _remove_symbol_seed(strategy, symbol)
    emit_execution_event(logging.getLogger("optimizer"), "CHAMPION_DEMOTED", {
        "strategy": strategy, "symbol": symbol, "champion_demoted_reason": reason,
    })
    logging.getLogger("optimizer").warning(
        "[#708] %s/%s: Champion demoted (%s) — Store- + Seed-Eintrag entfernt, Fallback auf "
        "strategy_defaults.", strategy, symbol, reason,
    )


def load_champion_entry(strategy: str, symbol: str, *, opt_data: dict) -> dict | None:
    """Issue #704 (P0) — liest + validiert (``champion_is_admissible``) den gespeicherten
    Champion. Gibt den VOLLEN Eintrag zurück (für #709-Study-Attr-Telemetrie);
    ``load_champion_seed`` extrahiert daraus nur ``params`` (Issue-Signatur, s. u.)."""
    entry = _read_entry(_champion_path(strategy, symbol))
    if entry is None:
        return None
    ok, _reason = champion_is_admissible(entry, opt_data)
    return entry if ok else None


def load_champion_seed(strategy: str, symbol: str, base_cfg: Path | None = None, *,
                       opt_data: dict, catalog_newest_ns: int | None = None) -> dict:
    """Issue #704 (P0) — Champion-Seed Reader für ``run_optimization.resolve_symbol_shrinkage_seed``.

    ``base_cfg``/``catalog_newest_ns`` werden für Signatur-Parität zu ``load_global_best``/
    ``load_strategy_defaults_params`` akzeptiert, aber intern nicht gebraucht: der Champion-Store
    liegt unter ``WORK`` (nicht config-relativ), und der ENQUEUE-Pfad braucht KEIN Fenster-Gate —
    der Seed wird auf den AKTUELLEN Daten voll neu durch alle Gates re-evaluiert. Das Fenster-Gate
    (``catalog_newest_ns`` vs. Erst-Sichtung) betrifft ausschliesslich den Writeback (#706,
    ``maybe_write_back``), nicht das Enqueue."""
    entry = load_champion_entry(strategy, symbol, opt_data=opt_data)
    return dict(entry.get("params") or {}) if entry else {}


def _seeds_path(base_cfg: Path | None = None) -> Path:
    from automation.optimizer.trial_config import config_dir
    return (base_cfg or config_dir()) / "strategy_symbol_seeds.json"


def _read_seeds_file(base_cfg: Path | None = None) -> dict:
    path = _seeds_path(base_cfg)
    if not path.exists():
        return {"seeds": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("seeds", {})
    return data


def _write_symbol_seed(strategy: str, symbol: str, params: dict, *,
                       base_cfg: Path | None = None) -> None:
    """Issue #706 (P1) — schreibt ``params`` nach ``strategy_symbol_seeds.json[seeds][strategy]
    [symbol]``. NIE ``strategy_defaults.json`` (der globale Cross-Symbol-Prior) — Symbol-Seeds
    sind strikt symbol-skopiert (siehe Docstring ``maybe_write_back``)."""
    path = _seeds_path(base_cfg)
    data = _read_seeds_file(base_cfg)
    data["seeds"].setdefault(strategy, {})[symbol] = dict(params)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _remove_symbol_seed(strategy: str, symbol: str, *, base_cfg: Path | None = None) -> None:
    path = _seeds_path(base_cfg)
    if not path.exists():
        return
    data = _read_seeds_file(base_cfg)
    strat_seeds = data.get("seeds", {}).get(strategy)
    if strat_seeds and symbol in strat_seeds:
        del strat_seeds[symbol]
        if not strat_seeds:
            del data["seeds"][strategy]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def _default_min_advance_days(base_cfg: Path | None) -> float:
    """Issue #706 — Default für ``champion_min_advance_days``, wenn der Key in ``optimizer.json``
    fehlt: ``backtest.json``'s ``walk_forward.oos_window_days`` (Zero-Hardcoding, dieselbe
    Fallback-Konvention wie überall im Optimizer — fehlt der Key, ein dokumentiertes,
    config-abgeleitetes Verhalten statt einer erfundenen Konstante)."""
    from automation.optimizer.trial_config import config_dir
    cfg_dir = base_cfg or config_dir()
    bt_path = cfg_dir / "backtest.json"
    if bt_path.exists():
        try:
            with open(bt_path, "r", encoding="utf-8") as f:
                wf = (json.load(f) or {}).get("walk_forward", {})
            return float(wf.get("oos_window_days", 0) or 0)
        except (OSError, ValueError):
            pass
    return 0.0


def maybe_write_back(entry: dict, opt_data: dict, *, base_cfg: Path | None = None) -> bool:
    """Issue #706 (P1) — Default-Nachführung: schreibt ``entry['params']`` nach
    ``strategy_symbol_seeds.json``, wenn ALLE Bedingungen erfüllt sind:
      1. ``reward_semantics_version`` des Eintrags stimmt mit der aktuellen Config überein.
      2. Fensterfortschritt: ``catalog_newest_ns`` liegt um >= ``champion_min_advance_days``
         (Default: ``backtest.json.walk_forward.oos_window_days``) über
         ``first_seen_catalog_newest_ns`` — Snooping-Schutz (sonst würden Parameter deployt, die
         nur auf identischen Daten „bestätigt" wurden). ENTFÄLLT bei einer echten
         READY_FOR_PR-Promotion (Punkt 3b) — die ist bereits vollständig auf dem Holdout validiert.
      3. Korroboration: ``corroboration_count >= champion_promote_after_runs`` (Default 2)
         ODER ``status_at_store == 'READY_FOR_PR'`` (sofortiger Writeback bei echter Promotion).

    Setzt ``lifecycle.writeback_applied = True`` und persistiert den Champion-Store-Eintrag
    zurück, wenn geschrieben wurde. HI-3 bleibt gewahrt: ``strategy_symbol_seeds.json`` ist ein
    Seed/Prior für den Resolver (``resolve.resolve_params``), KEIN Live-Deployment —
    ``strategies.json`` ändert weiterhin ausschliesslich ein menschlich freigegebener PR.

    **Warum niemals nach ``strategy_defaults.json``:** das ist der globale Cross-Symbol-Prior; ein
    symbol-getunter Vektor kann global toxisch sein (R_symbol positiv ↔ R_global negativ ist ein
    realer Fall, siehe Issue #705 §2), Symbol-Seeds gehören strikt symbol-skopiert."""
    if not opt_data.get("champion_enabled", True):
        return False
    reward_version = opt_data.get("reward_semantics_version")
    integrity = entry.get("integrity") or {}
    if reward_version is None or integrity.get("reward_semantics_version") != reward_version:
        return False
    params = entry.get("params") or {}
    if not params:
        return False

    provenance = entry.get("provenance") or {}
    is_ready_for_pr = provenance.get("status_at_store") == "READY_FOR_PR"

    lifecycle = entry.get("lifecycle") or {}
    corroboration_count = int(lifecycle.get("corroboration_count", 0) or 0)
    promote_after = int(opt_data.get("champion_promote_after_runs", 2))
    corroborated = corroboration_count >= promote_after

    if not (is_ready_for_pr or corroborated):
        return False

    if not is_ready_for_pr:
        # Snooping-Schutz gilt NUR für die korroborations-basierte Route — READY_FOR_PR ist
        # bereits ein vollständig auf dem Holdout validierter Promotion-Befund.
        current_ns = integrity.get("catalog_newest_ns")
        first_ns = lifecycle.get("first_seen_catalog_newest_ns")
        if current_ns is None or first_ns is None:
            return False
        min_advance_days = opt_data.get("champion_min_advance_days")
        if min_advance_days is None:
            min_advance_days = _default_min_advance_days(base_cfg)
        advance_days = (current_ns - first_ns) / 1_000_000_000.0 / 86400.0
        if advance_days < float(min_advance_days or 0):
            return False

    strategy = entry.get("strategy")
    symbol = entry.get("symbol")
    _write_symbol_seed(strategy, symbol, params, base_cfg=base_cfg)
    entry.setdefault("lifecycle", {})["writeback_applied"] = True
    _write_entry(_champion_path(strategy, symbol), entry)
    return True
