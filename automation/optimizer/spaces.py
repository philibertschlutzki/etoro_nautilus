import json as _json


_search_space_overrides_cache: dict | None = None
_auto_proposed_bounds_cache: dict | None = None


def _load_search_space_overrides() -> dict:
    """Issue #669 — symbol-spezifische Suchraum-Bounds-Überschreibungen aus
    ``search_space_overrides.json`` (Zero-Hardcoding, gecached). Fehlt die Datei/der Eintrag
    ⇒ ``{}`` (bit-identisch zum Pre-#669-Verhalten)."""
    global _search_space_overrides_cache
    if _search_space_overrides_cache is not None:
        return _search_space_overrides_cache
    try:
        from automation.optimizer.trial_config import config_dir
        path = config_dir() / "search_space_overrides.json"
        if path.exists():
            data = _json.loads(path.read_text("utf-8")) or {}
            _search_space_overrides_cache = data.get("overrides", {}) or {}
        else:
            _search_space_overrides_cache = {}
    except Exception:
        _search_space_overrides_cache = {}
    return _search_space_overrides_cache


def _load_auto_proposed_bounds() -> dict:
    """Issue #761 — AUTOMATISCH vorgeschlagene (NICHT kuratierte) Bounds aus dem #681-Diagnose-
    Cache (``sweep_diagnostics.propose_bounds_widening``, geschrieben von ``run_optimization.
    floor_plateau_callback`` bei einem ``'search_space_override'``-Befund). Gecached wie
    ``_load_search_space_overrides``. Fehlt der Cache/ein Eintrag ⇒ ``{}``."""
    global _auto_proposed_bounds_cache
    if _auto_proposed_bounds_cache is not None:
        return _auto_proposed_bounds_cache
    try:
        from automation.optimizer.sweep_diagnostics import load_diagnosed_pairs_cache
        out: dict = {}
        for (strategy, symbol), entry in load_diagnosed_pairs_cache().items():
            proposed = entry.get("proposed_bounds")
            if proposed:
                out.setdefault(strategy, {})[symbol] = proposed
        _auto_proposed_bounds_cache = out
    except Exception:
        _auto_proposed_bounds_cache = {}
    return _auto_proposed_bounds_cache


# Issue #1316 (GH #1193, P1) — bar-denominierte Parameter: ihre Suchraum-Bounds sind an die
# Bar-ACHSE gebunden (Kalender-24/7 vor #1275, RTH seit #1275, Faktor RTH_AXIS_FACTOR=0.24
# zwischen beiden) — ein Bound, der auf der ALTEN Achse kalibriert wurde, ist auf der neuen um
# denselben Faktor 4,17x zu weit. Reine Schwellwert-/Verhältnis-Parameter (z. B. ``rsi_oversold``,
# ``keltner_multiplier``, ``squeeze_ratio``) sind NICHT betroffen (keine Bar-Einheit) und bleiben
# ausserhalb dieser Menge. Konsumiert von ``_bounds_for`` (fail-loud) UND
# ``invariants.check_override_axis_coherence`` (report-seitig, dieselbe Menge).
_BAR_DENOMINATED_PARAMS: frozenset[str] = frozenset({
    "cooldown_bars", "max_bars_in_trade", "keltner_period", "ema_period", "adx_period", "or_bars",
})

# Issue #1316 — Metadaten-Schluessel je Override-Block in ``search_space_overrides.json``
# (Geschwister der eigentlichen Parameter-Bounds im selben Symbol-Dict, siehe Datei-Schema-
# Dokumentation). MUESSEN von jedem Code, der einen Override-Block als ``{param: [lo, hi]}``
# durchiteriert (hier UND ``bounds.active_bounds_overrides``), explizit uebersprungen werden.
_OVERRIDE_METADATA_KEYS: frozenset[str] = frozenset({
    "axis", "calibrated_in_run_id", "proposed_rth_bounds",
})


class StaleAxisOverrideError(ValueError):
    """Issue #1316 (GH #1193, P1) — ein kuratierter, bar-denominierter Suchraum-Override
    (``search_space_overrides.json``) traegt entweder KEINE ``axis``-Deklaration oder eine, die
    von der Achse DIESES Laufs (``optimizer.json['time_box_bars_axis']``) abweicht. FAIL-LOUD statt
    eines stillen Weiterverwendens der potenziell falsch skalierten Bounds (Faktor 4,17 zwischen
    Kalender-24/7- und RTH-Achse, ``RTH_AXIS_FACTOR=0.24`` — siehe #1275 fuer die volle
    Herleitung). Kein Default fuer eine fehlende ``axis``: die Migration (#1316) ist eine bewusste
    PR-Entscheidung, kein automatisch angenommener Zustand."""


def _current_time_box_bars_axis() -> str | None:
    """Issue #1316 — liest ``optimizer.json['time_box_bars_axis']`` UNGECACHED (anders als
    ``_load_search_space_overrides``/``_load_auto_proposed_bounds``): dieser Pfad wird nur bei
    einem TATSAECHLICHEN kuratierten Override-Treffer erreicht (eine Handvoll (strategy, symbol,
    param)-Kombinationen, kein Hot-Path pro Trial/Parameter) — ein Modul-Cache wuerde hier nur ein
    Test-Isolationsrisiko einfuehren (``config_dir()``-Monkeypatches zwischen Tests), ohne einen
    messbaren Performance-Vorteil. Fehlt die Datei/der Schluessel ⇒ ``None`` (die Divergenzpruefung
    in ``_bounds_for`` behandelt das als "nicht bestimmbar", nicht als Verstoss)."""
    try:
        from automation.optimizer.trial_config import config_dir
        path = config_dir() / "optimizer.json"
        if not path.exists():
            return None
        data = _json.loads(path.read_text("utf-8")) or {}
        return data.get("time_box_bars_axis")
    except Exception:
        return None


def _bounds_for(strategy: str, symbol: str | None, param: str, low, high):
    """Issue #669/#761 — löst die effektiven ``(low, high)``-Suchraumgrenzen für ``param`` auf, in
    Prioritätsreihenfolge: (1) eine kuratierte symbol-spezifische Überschreibung
    (``search_space_overrides.json``, menschliche PR-Entscheidung), (2) ein AUTOMATISCH
    vorgeschlagener Bounds-Wert aus dem #681-Diagnose-Cache (``SEARCH_SPACE_AUTO_OVERRIDE`` —
    die Brücke, damit der NÄCHSTE Lauf nicht identisch an denselben zu engen Bounds scheitert,
    bis ein Operator die permanente Kalibrierung per PR einträgt), (3) die universellen Default-
    Bounds. Fehlt ``symbol`` ODER ist weder ein kuratierter noch ein automatischer Override für
    (``strategy``, ``symbol``, ``param``) vorhanden ⇒ ``(low, high)`` UNVERÄNDERT (bit-identisch,
    Zero-Hardcoding).

    Issue #1316 (GH #1193) — trägt der kuratierte Treffer einen bar-denominierten Parameter
    (``_BAR_DENOMINATED_PARAMS``), MUSS der Override-Block eine ``axis`` tragen, die mit
    ``optimizer.json['time_box_bars_axis']`` übereinstimmt — sonst ``StaleAxisOverrideError``
    (siehe dortigen Docstring). Der AUTOMATISCHE #761-Diagnose-Cache-Pfad bleibt unberührt (kein
    ``axis``-Feld in diesem Format, ausserhalb des #1316-Scopes)."""
    if not symbol:
        return low, high
    entry = (_load_search_space_overrides().get(strategy) or {}).get(symbol) or {}
    bound = entry.get(param)
    if bound and len(bound) == 2:
        if param in _BAR_DENOMINATED_PARAMS:
            override_axis = entry.get("axis")
            run_axis = _current_time_box_bars_axis()
            if override_axis is None or (run_axis is not None and override_axis != run_axis):
                raise StaleAxisOverrideError(
                    f"STALE_AXIS_OVERRIDE: kuratierter Override {strategy}/{symbol}.{param}="
                    f"{bound!r} traegt axis={override_axis!r}, Lauf-Achse ist {run_axis!r} "
                    "(optimizer.json['time_box_bars_axis']) — search_space_overrides.json "
                    "aktualisieren (axis-Feld + Bounds, siehe proposed_rth_bounds) (#1316)."
                )
        # Issue #1066 — eine kuratierte Überschreibung ist eine menschliche PR-Entscheidung
        # (``search_space_overrides.json``); ein Wert ausserhalb des Domänenregisters ist ein
        # Config-Fehler und wird FAIL-LOUD abgelehnt, statt negativ/degeneriert zu sampeln
        # (Akzeptanzkriterium #1066/4).
        if not is_bounds_admissible(param, bound[0], bound[1]):
            raise ValueError(
                f"SEARCH_SPACE_OVERRIDE_INADMISSIBLE: kuratierter Override "
                f"{strategy}/{symbol}.{param}={bound!r} liegt ausserhalb des Domänenregisters "
                f"{_PARAM_DOMAIN_REGISTRY.get(param)!r} — search_space_overrides.json korrigieren."
            )
        return bound[0], bound[1]
    auto_entry = (_load_auto_proposed_bounds().get(strategy) or {}).get(symbol) or {}
    auto_bound = auto_entry.get(param)
    if auto_bound and len(auto_bound) == 2:
        import logging
        # Issue #1066 — ein AUTOMATISCH vorgeschlagener Override (#761-Diagnose-Cache) kann von
        # VOR diesem Fix geschriebenen, nicht geklammerten Einträgen stammen (Beweis B-5: negative
        # ``ema_period``-Untergrenzen). Anders als bei der kuratierten Überschreibung ist das kein
        # Grund, den Sweep abzubrechen — der Cache ist ein Selbstheilungsmechanismus, kein
        # menschlich geprüfter Vertrag: der Eintrag wird verworfen (fällt auf die universellen
        # Default-Bounds zurück) und laut protokolliert, statt negativ zu sampeln.
        if not is_bounds_admissible(param, auto_bound[0], auto_bound[1]):
            _dom = _PARAM_DOMAIN_REGISTRY.get(param)
            logging.getLogger("optimizer").warning(
                "[JSON_EVENT] " + _json.dumps({
                    "event_type": "SEARCH_SPACE_OVERRIDE_INADMISSIBLE",
                    "strategy": strategy, "symbol": symbol, "param": param,
                    "rejected_bounds": [auto_bound[0], auto_bound[1]],
                    "domain": [_dom[0], _dom[1]] if _dom else None,
                    "default_bounds": [low, high],
                }))
            return low, high
        logging.getLogger("optimizer").info(
            "[JSON_EVENT] " + _json.dumps({
                "event_type": "SEARCH_SPACE_AUTO_OVERRIDE",
                "strategy": strategy, "symbol": symbol, "param": param,
                "bounds": [auto_bound[0], auto_bound[1]], "default_bounds": [low, high],
            }))
        return auto_bound[0], auto_bound[1]
    return low, high


# Issue #714 (GR-01) — 24-Bar-Zeitbox (1h-Bars). Harte Obergrenze für JEDE ``max_bars_in_trade``-
# Suchraum-Bound über alle 15 Strategien (Untergrenzen bleiben unverändert). Issue #858 — Single
# Source of Truth über einen Import statt einer eigenen Kopie des Literals, konsistent mit
# ``hourly_strategy_base.MAX_BARS_IN_TRADE_HARD_CAP``/``invariants._MAX_BARS_IN_TRADE_CAP``.
#
# Issue #1030/#1179 (Katalog #866-2) — ACHSEN-HINWEIS fuer JEDES ``max_bars_in_trade``-Band in
# diesem Modul: alle Bands waren bis #1275 in Kalender-Bars gesampelt (die synthetische 1h-Bar-
# Achse lief fuer EQUITY/COMMODITY ueber einen 24/7-Kalender statt einer Handelszeiten-Maske,
# ``invariants.check_session_calendar_coherence``, #1011/#1163/#1027/#1176) — die vormaligen
# Default-Untergrenzen 6/8/12 waren KEINE Handelsstunden-Naeherung, sondern buchstaeblich
# Kalenderstunden.
#
# Issue #1275 (GH #1148, Katalog #1272-1297, P0) Fix Punkt 3 — GESCHLOSSEN: seit
# ``backtest_runner._filter_ticks_to_session_hours`` (#1260/#1130-Nachfolger) die Bar-Erzeugung
# tatsaechlich auf RTH-Ticks umgestellt hat, sind JEDES ``max_bars_in_trade``-Band in diesem Modul
# (der Cap UND jede Strategie-spezifische ``_bounds_for(..., "max_bars_in_trade", lo,
# _MAX_BARS_IN_TRADE_CAP)``-Zeile) UND ``optimizer.json['time_box_bars']`` auf die neue Achse
# umgerechnet (Faktor 0.24 — das GEMESSENE ``session_coverage_fraction`` des #1275-Referenzlaufs,
# NICHT die fruehere ~0,583-Faustregel dieses Kommentars: jene schaetzte die reale Haltedauer UEBER
# Session-Luecken hinweg, dieser Faktor rebasiert die Bar-ACHSE SELBST). Alte Kalender-Bar-Werte
# (zur Referenz): Cap 24, Floor 4, Strategie-Untergrenzen 6/8/12. Neu (RTH-Bars): Cap 6, Floor 1,
# Strategie-Untergrenzen 1/2/3 (siehe ``_contracts.MAX_BARS_IN_TRADE_HARD_CAP``-Docstring fuer die
# volle Herleitung). ``optimizer.json['time_box_bars_axis']`` steht seither auf ``'rth'`` (war
# ``'calendar_24_7'``); ``invariants.check_timebox_unit_coherence`` haelt diese Deklaration
# weiterhin gegen die tatsaechlich gemessene ``bars_per_calendar_day``-Achse konsistent.
from automation.optimizer._contracts import MAX_BARS_IN_TRADE_HARD_CAP as _MAX_BARS_IN_TRADE_CAP
# Issue #1067 — die symmetrische Untergrenze zu ``_MAX_BARS_IN_TRADE_CAP`` (Single Source of Truth,
# siehe _contracts.py-Docstring).
from automation.optimizer._contracts import MIN_BARS_IN_TRADE_FLOOR as _MIN_BARS_IN_TRADE_FLOOR


# Issue #1066 (Pitfall #371) — Domänenregister für JEDEN Parameter, den ein automatischer
# Suchraum-Rückschrieb (``sweep_diagnostics._widen_bounds_toward``, Issue #761/#763) oder eine
# kuratierte Überschreibung (``search_space_overrides.json``) erreichen kann. Jeder Eintrag
# begrenzt SYMMETRISCH (Unter- UND Obergrenze) — vor diesem Fix klammerte nur
# ``max_bars_in_trade`` nach oben (``_MAX_BARS_IN_TRADE_CAP``), jeder andere Parameter und jede
# Untergrenze blieb frei. Ein akkumulierender Rückschrieb, der bei jedem Lauf um denselben Betrag
# weitet, erreicht sonst nach k Läufen ``lo₀ − k·Δ`` — negative Perioden, negative Bar-Anzahlen,
# ein RSI-Schwellwert unterhalb des Wertebereichs von RSI (Beweis B-5 im #866-Katalog).
# ``(min_admissible, max_admissible, dtype)``. Grosszügig gegenüber den kuratierten Default-
# Suchräumen (kein bestehender ``sample_params``-Bound wird durch dieses Register selbst
# eingeschränkt) — es klammert nur, wohin eine AUTOMATISCHE Weitung/Überschreibung noch gehen darf.
_PARAM_DOMAIN_REGISTRY: dict[str, tuple[float, float, type]] = {
    "ema_period": (2, 400, int),
    "sma_period": (2, 400, int),
    "donchian_period": (2, 150, int),
    "atr_period": (2, 60, int),
    "rsi_period": (2, 60, int),
    "rsi_oversold": (1.0, 49.0, float),
    "rsi_overbought": (51.0, 99.0, float),
    "cooldown_bars": (1, 96, int),
    "keltner_period": (2, 100, int),
    "keltner_atr_period": (2, 100, int),
    "keltner_multiplier": (0.1, 10.0, float),
    "adx_period": (2, 60, int),
    "bb_period": (2, 100, int),
    "vwap_period": (2, 150, int),
    "or_bars": (1, 24, int),
    "min_holding_time": (0, _MAX_BARS_IN_TRADE_CAP, int),
    "min_squeeze_bars": (1, 96, int),
    "price_breakout_period": (2, 150, int),
    "squeeze_ratio": (0.1, 3.0, float),
    "gap_threshold_pct": (0.0001, 0.5, float),
    "deviation_threshold": (0.0001, 0.5, float),
    "max_bars_in_trade": (_MIN_BARS_IN_TRADE_FLOOR, _MAX_BARS_IN_TRADE_CAP, int),
}


def clamp_param_bounds(param: str, lo: float, hi: float) -> tuple[float, float]:
    """Issue #1066 — klammert ``(lo, hi)`` symmetrisch gegen ``_PARAM_DOMAIN_REGISTRY[param]``.
    Parameter ohne Registereintrag sind unverändert (kein Zero-Hardcoding-Bruch für Parameter, die
    heute keinen automatischen Rückschrieb erreichen können). Ein Wert, der bereits INNERHALB der
    Domäne liegt, bleibt UNVERÄNDERT (kein Rundungs-/Typ-Zwang über ``dtype`` — das Register
    dokumentiert nur die zulässige Spannweite, nicht die Präzision der Weitungs-Arithmetik) — nur
    eine tatsächliche Grenzverletzung wird auf den jeweiligen Domänen-Randwert zurückgesetzt.
    Kollabiert ``lo`` nach dem Klammern über ``hi``, wird auf den zulässigen Einzelpunkt
    ``(min_admissible, min_admissible)`` zurückgesetzt (defensiv — sollte bei ``lo <= hi`` in der
    Eingabe nie eintreten)."""
    dom = _PARAM_DOMAIN_REGISTRY.get(param)
    if dom is None:
        return lo, hi
    dom_lo, dom_hi, _dtype = dom
    clamped_lo = max(lo, dom_lo)
    clamped_hi = min(hi, dom_hi)
    if clamped_lo > clamped_hi:
        clamped_lo = clamped_hi = dom_lo
    return clamped_lo, clamped_hi


def is_bounds_admissible(param: str, lo: float, hi: float) -> bool:
    """Issue #1066 — ``True``, wenn ``(lo, hi)`` innerhalb ``_PARAM_DOMAIN_REGISTRY[param]`` liegt
    (oder der Parameter kein Registereintrag hat — dann ist jeder Wert per Definition zulässig).
    Konsumiert von ``_bounds_for`` (Ablehnung eines Cache-/Config-Werts) UND
    ``invariants.check_search_space_override_admissible`` (Report-Wächter über den gesamten
    #761-Cache)."""
    dom = _PARAM_DOMAIN_REGISTRY.get(param)
    if dom is None:
        return True
    dom_lo, dom_hi, _dtype = dom
    try:
        lo_f, hi_f = float(lo), float(hi)
    except (TypeError, ValueError):
        return False
    return lo_f <= hi_f and lo_f >= dom_lo and hi_f <= dom_hi


def _dyn_tp_params(trial) -> dict:
    """Issue #713 (Req-01-Realgehalt, gated) — dyn_tp_enabled als (searchbare) TPE-Dimension,
    einheitlich für ALLE 15 Strategien (der dyn-TP-Pfad ist via #712 in der Basisklasse verfügbar).
    λ/γ werden KONDITIONAL nur im ``True``-Ast gesampelt (TPE-nativ; Optuna behandelt die inaktiven
    Trials korrekt als fehlende Dimensionen). Alle drei Keys sind echte ``HourlyStrategyConfig``-
    Felder (#712) — kein stilles Verwerfen (Pitfall #446)."""
    dyn_tp_on = trial.suggest_categorical("dyn_tp_enabled", [False, True])
    params: dict = {"dyn_tp_enabled": dyn_tp_on}
    if dyn_tp_on:
        # λ∈[0.1, 3.0] (Halbwertszeit der Target-Kontraktion), log=True (multiplikative Wirkung);
        # γ∈[0.5, 4.0] (ATR-Vielfaches, konsistent mit den bestehenden atr_trailing_multiplier-Bounds).
        params["dyn_tp_lambda"] = trial.suggest_float("dyn_tp_lambda", 0.1, 3.0, log=True)
        params["dyn_tp_gamma"] = trial.suggest_float("dyn_tp_gamma", 0.5, 4.0)
    return params


def _sample_risk_layer(
    trial, *, strategy: str, symbol: str | None,
    atr_bounds: tuple[float, float] = (0.5, 3.0),
    max_bars_bounds: tuple[int, int] = (3, _MAX_BARS_IN_TRADE_CAP),
) -> dict:
    """Issue #1043/#1192 (Katalog #1192) — gemeinsamer Sampling-Block für die beiden Risiko-Layer-
    Parameter, die ``strategies.HourlyStrategyBase`` für JEDE Strategie bereitstellt
    (ATR-Trailing-Stop, Zeitbox): ``atr_trailing_multiplier``/``max_bars_in_trade``.

    Root-Cause #1043: ``SmaCrossoverStrategy`` (dieser Datei) sampelte bislang KEINEN einzigen
    Risikoparameter — ``atr_trailing_multiplier``/``max_bars_in_trade`` blieben auf dem statischen
    ``strategy_default`` fixiert (kein Tuning-Effekt), obwohl die Basisklasse sie fuer jede
    Strategie bereitstellt. Die Study war mit −0,918 % Holdout-Return und −1,450 % α·n die
    zweitschlechteste des Referenzlaufs, bei einem TRAILING_STOP-Exit-Anteil von 58,18 % — ein
    ungetunter Stop dominierte das Exit-Verhalten, ohne je optimiert worden zu sein.

    Bewusste Scope-Entscheidung: NUR die ``SmaCrossoverStrategy``-Luecke wird ueber diesen
    gemeinsamen Block geschlossen (der einzige nachgewiesene Fall). Die uebrigen 13 Strategien-
    Zweige sampeln BEIDE Parameter bereits selbst, mit strategiespezifisch kalibrierten, historisch
    begruendeten Bandbreiten (z. B. FlashCrashReversal/VwapExhaustion: ``max_bars_in_trade``-
    Untergrenze 6 statt 12) — sie auf diesen gemeinsamen Block umzustellen wäre eine reine
    Refactoring-Uebung ohne Verhaltensaenderung, aber mit echtem Transkriptionsrisiko fuer 13
    bereits korrekt funktionierende, kalibrierte Suchraeume. ``invariants.
    check_risk_layer_parameter_parity`` verifiziert das Ergebnis (beide Parameter im ``params``-
    Dict jeder Strategie) unabhaengig davon, WELCHER Codepfad (dieser gemeinsame Block oder ein
    eigenstaendiger Zweig) sie liefert — die Beobachtung zaehlt, nicht die Implementierung.

    ``atr_bounds``/``max_bars_bounds`` bleiben je Aufrufer ueberschreibbar (Fix-Vorgabe: "Bänder je
    Strategie überschreibbar halten"); Default ``(0.5, 3.0)``/``(3, _MAX_BARS_IN_TRADE_CAP)``
    entspricht der bereits an der Mehrzahl der bestehenden Strategien-Zweige (Dynamic Breakout,
    FlashCrashReversal ausgenommen der 1er-Untergrenze, TrendPullback, VwapExhaustion ausgenommen)
    verwendeten Bandbreite (Issue #1275, GH #1148, Katalog #1272-1297, P0 Fix Punkt 3 — auf die
    RTH-Bar-Achse umkalibriert, Faktor 0.24, siehe ``_contracts.MAX_BARS_IN_TRADE_HARD_CAP``-
    Docstring). Symbol-Override-faehig ueber ``_bounds_for`` (dieselbe #669/#761-Prioritaetskette
    wie jeder andere Suchraum-Parameter)."""
    atr_lo, atr_hi = atr_bounds
    mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", *max_bars_bounds)
    return {
        "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", atr_lo, atr_hi),
        "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
    }


def sample_params(strategy: str, trial, *, symbol: str | None = None) -> dict:
    """Issue #669 — ``symbol`` (optional, Default ``None``) aktiviert symbol-spezifische
    Suchraum-Bounds-Überschreibungen für die trade-armen Strategien (siehe ``_bounds_for``). Fehlt
    ``symbol`` (z. B. der globale Multi-Symbol-Pfad, ``bounds.extract_numeric_bounds``) ⇒
    bit-identisch zu den universellen Default-Bounds.

    Issue #713 — jede Strategie erhält zusätzlich die konditionalen ``dyn_tp_*``-Suchdimensionen
    (siehe ``_dyn_tp_params``), einheitlich am Ende dieser Funktion angehängt statt pro Strategie-
    Zweig dupliziert."""
    if strategy == "HourlyMeanReversionStrategy":
        kp_lo, kp_hi = _bounds_for(strategy, symbol, "keltner_period", 6, 40)
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "keltner_period": trial.suggest_int("keltner_period", kp_lo, kp_hi),
            "keltner_atr_period": trial.suggest_int("keltner_atr_period", 6, 40),
            "keltner_multiplier": trial.suggest_float("keltner_multiplier", 1.0, 3.5),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.3, 2.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "SmaCrossoverStrategy":
        # Issue #1043/#1192 — vor diesem Fix sampelte SmaCrossover KEINEN Risikoparameter
        # (siehe ``_sample_risk_layer``-Docstring fuer die volle Root-Cause/den Referenzbefund).
        params = {
            "sma_period": trial.suggest_int("sma_period", 5, 60),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            **_sample_risk_layer(trial, strategy=strategy, symbol=symbol),
        }
    elif strategy == "ComboTrendVwapStrategy":
        # Konzept §4 alignment (ISSUE-OPT-377): macd_fast 3–14, macd_gap 4–26
        # ⇒ macd_slow = fast + gap (Gap garantiert fast < slow für den MACD-Indikator).
        fast = trial.suggest_int("macd_fast", 3, 14)
        gap = trial.suggest_int("macd_gap", 4, 26)
        params = {
            # Korrigierter Name für den Config-Empfänger
            "macd_signal_period": trial.suggest_int("macd_signal_period", 5, 15),
            "macd_fast": fast,
            "macd_slow": fast + gap,

            # WICHTIG: Die primären Entry-Konditionen für Optuna freigeben
            "sma_period": trial.suggest_int("sma_period", 20, 100),
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.0, 2.5),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_multiplier": trial.suggest_float("atr_multiplier", 0.1, 1.5),
            "vwap_period": trial.suggest_int("vwap_period", 10, 60),

            "trend_tolerance_pct": trial.suggest_float("trend_tolerance_pct", 0.0, 0.10),
            "bb_touch_window": trial.suggest_int("bb_touch_window", 6, 96),

            # Konjunktions-Schalter: erlauben dem Optimizer, einzelne Entry-Bedingungen abzuwählen
            "require_vwap_confirmation": trial.suggest_categorical("require_vwap_confirmation", [True, False]),
            "require_bb_touch": trial.suggest_categorical("require_bb_touch", [True, False]),

            # Trade-Management
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            # Issue #714 (GR-01) — Obergrenze 120 → 24 (24-Bar-Zeitbox über alle 15 Strategien).
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "FlashCrashReversalStrategy":
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 1, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.5, 3.0),
            "rsi_period": trial.suggest_int("rsi_period", 2, 14),
            "rsi_oversold": trial.suggest_int("rsi_oversold", 10, 30),
            "atr_period": trial.suggest_int("atr_period", 5, 20),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "VolatilityBreakoutPumpStrategy":
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": trial.suggest_float("bb_std_dev", 1.5, 3.0),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "VwapExhaustionStrategy":
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 1, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "vwap_period": trial.suggest_int("vwap_period", 10, 50),
            "deviation_threshold": trial.suggest_float("deviation_threshold", 0.005, 0.03),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "DynamicBreakoutStrategy":
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "price_breakout_period": trial.suggest_int("price_breakout_period", 5, 60),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "TrendPullbackStrategy":
        # Issue #669 — TrendPullback erzeugte auf TSLA-1h STRUCTURAL_ALL_UNEVALUABLE (0/16 Trials
        # >= oos_min_trades): ema_period bis 300 Bars (~12.5 Tage bei 1h) verlangt ein sehr langes
        # Trend-Fenster, cooldown_bars/max_bars_in_trade begrenzen zusätzlich die Signal-/Realisierungs-
        # frequenz. Symbol-Override-Punkte (leer per Default, Zero-Hardcoding — Aktivierung erst nach
        # einem dokumentierten Kalibrierlauf, siehe search_space_overrides.json).
        ema_lo, ema_hi = _bounds_for(strategy, symbol, "ema_period", 50, 300)
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "ema_period": trial.suggest_int("ema_period", ema_lo, ema_hi),
            "rsi_period": trial.suggest_int("rsi_period", 5, 21),
            "rsi_oversold": trial.suggest_float("rsi_oversold", 15.0, 45.0),
            "rsi_overbought": trial.suggest_float("rsi_overbought", 55.0, 85.0),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "AdxAtrMomentumStrategy":
        # Issue #669 — Symbol-Override-Punkte für cooldown_bars/max_bars_in_trade (leer per
        # Default). Issue #699 — `adx_period` wird NICHT MEHR gesampelt: der #691-Trockenlauf
        # verifizierte, dass `DirectionalMovement.value` in NautilusTrader 1.230.0 konstant 0.0
        # bleibt — das ADX-Gate wurde durch eine EMA-Steigungs-Bestätigung ersetzt (Option B,
        # siehe adx_atr_momentum.py-Docstring, analog DonchianRegimeBreakout/#691). `adx_period`
        # ist seither funktional TOT (kein Effekt auf das Entry-Signal) und würde sonst
        # Phantom-Tuning betreiben (Pitfall #4) — bleibt in der Config als Re-Aktivierungspunkt.
        # Issue #908 — die #870-Bounds-Öffnung hat das Frequenzproblem (0 eligible Trials, gesperrt
        # durch einen zu engen Suchraum) gelöst und dabei ein Regime freigegeben, in dem die
        # Strategie alle ~5,7 Bars handelt (754 OOS-Trades / 180 d bei einer 24-Bar-Zeitbox — kein
        # Momentum-Handel mehr, sondern hochfrequentes Rauschen-Traden ohne Informationsgewinn, nur
        # Durchsatzkosten). cooldown_bars-Untergrenze 2 → 6 UND min_holding_time NEU im Suchraum
        # (vorher an KEINER Strategie gesampelt, immer Default 0) begrenzen das Handelsregime.
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 6, 36)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP)
        mh_lo, mh_hi = _bounds_for(strategy, symbol, "min_holding_time", 0, 2)
        params = {
            "ema_period": trial.suggest_int("ema_period", 20, 100),
            "atr_multiplier": trial.suggest_float("atr_multiplier", 1.0, 4.0),
            "atr_period": trial.suggest_int("atr_period", 5, 21),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
            "min_holding_time": trial.suggest_int("min_holding_time", mh_lo, mh_hi),
        }
    elif strategy == "MeanReversionStrategy":
        params = {
            "keltner_period": trial.suggest_int("keltner_period", 6, 40),
            "keltner_atr_period": trial.suggest_int("keltner_atr_period", 6, 40),
            "keltner_multiplier": trial.suggest_float("keltner_multiplier", 1.0, 3.5),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 36),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.3, 2.5),
            # Issue #714 (GR-01) — Obergrenze 96 → 24.
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "SqueezeBreakoutStrategy":
        # Issue #689 — Bollinger-innerhalb-Keltner-Squeeze-Release.
        # Issue #921 — `bb_std_dev`/`keltner_multiplier` UNABHÄNGIG sampeln (Pre-#921-Verhalten,
        # siehe Kommentar-Historie) erzeugte bei 178 Trials nur 19 auswertbare (Median 1
        # OOS-Trade): `squeeze_on` (squeeze_breakout.py:70) verlangt, dass die Bollinger-Bänder
        # VOLLSTÄNDIG innerhalb des Keltner-Kanals liegen — strukturell nur erreichbar, wenn
        # `bb_std_dev / keltner_multiplier` unterhalb eines datengetriebenen Schwellwerts nahe 1
        # bleibt. Zwei unabhängige Sampler treffen dieses enge Verhältnis selten. Fix: dasselbe
        # fast+gap-Muster wie ComboTrendVwaps `macd_slow` (ISSUE-OPT-377) — das VERHÄLTNIS
        # (`squeeze_ratio`, Band [0.70; 1.05]) wird gesampelt, `keltner_multiplier` bleibt der
        # absolute Faktor, `bb_std_dev` wird daraus abgeleitet. Garantiert die enge Kopplung, die
        # die Squeeze-Bedingung tatsächlich braucht, statt sie zwei unabhängigen Samplern zu
        # überlassen. `squeeze_ratio` selbst ist KEIN Config-Feld der Strategie (existiert nur als
        # Optuna-Suchraum-Achse) — nur das abgeleitete `bb_std_dev` erreicht die Strategie-Config.
        sr_lo, sr_hi = _bounds_for(strategy, symbol, "squeeze_ratio", 0.70, 1.05)
        msb_lo, msb_hi = _bounds_for(strategy, symbol, "min_squeeze_bars", 3, 18)
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 2, 24)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP)
        squeeze_ratio = trial.suggest_float("squeeze_ratio", sr_lo, sr_hi)
        keltner_multiplier = trial.suggest_float("keltner_multiplier", 1.0, 2.5)
        params = {
            "bb_period": trial.suggest_int("bb_period", 10, 40),
            "bb_std_dev": squeeze_ratio * keltner_multiplier,
            "keltner_period": trial.suggest_int("keltner_period", 10, 40),
            "keltner_multiplier": keltner_multiplier,
            "min_squeeze_bars": trial.suggest_int("min_squeeze_bars", msb_lo, msb_hi),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 3.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "OpeningRangeBreakoutStrategy":
        # Issue #690 — Opening-Range-Breakout (Momentum-Ignition am Tagesbeginn).
        # Issue #922 Fix 3 — untere or_bars/cooldown_bars-Bounds geöffnet (2→1 bzw. 2→1): das
        # XOM-Referenzsymptom (median oos_total_trades=15 gegen oos_min_trades=20, median
        # n_periods=72) ist ZWEI unabhängige Frequenz-Defizite, der Session-Anker (Fix 2) allein
        # behebt nur eines. Zielgrösse ≥ 40 OOS-Round-Trips (das Doppelte von oos_min_trades,
        # damit das Gate nicht selbst bindend wird) — ein engerer or_bars/cooldown_bars-Suchraum
        # lässt mehr Signale zu, ohne die obere Bound (weniger Trades, längere Range) zu verlieren.
        # Jetzt auch über search_space_overrides.json symbol-spezifisch überschreibbar (Fix 3,
        # vorher nur TrendPullback/AdxAtr/HourlyMeanReversion/SqueezeBreakout verdrahtet).
        ob_lo, ob_hi = _bounds_for(strategy, symbol, "or_bars", 1, 8)
        cd_lo, cd_hi = _bounds_for(strategy, symbol, "cooldown_bars", 1, 24)
        mb_lo, mb_hi = _bounds_for(strategy, symbol, "max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP)
        params = {
            "or_bars": trial.suggest_int("or_bars", ob_lo, ob_hi),
            "or_atr_buffer": trial.suggest_float("or_atr_buffer", 0.0, 1.0),
            "cooldown_bars": trial.suggest_int("cooldown_bars", cd_lo, cd_hi),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 3.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", mb_lo, mb_hi),
        }
    elif strategy == "DonchianRegimeBreakoutStrategy":
        # Issue #691 — Donchian-Ausbruch, EMA-Steigungs-gegatet (Regime-Filter Option B). Der
        # Trockenlauf (echter NautilusTrader-Engine-Lauf) verifizierte, dass `DirectionalMovement
        # .value` in der installierten NautilusTrader-Version (1.230.0) konstant 0.0 bleibt — Option
        # A (ADX) wurde daher deaktiviert (siehe donchian_regime_breakout.py-Docstring, Pitfall #9
        # des Implementierungs-Leitfadens #688). `adx_period`/`adx_threshold` sind seither
        # funktional TOT (kein Effekt auf das Entry-Signal mehr) und werden daher NICHT gesampelt
        # (sonst Phantom-Tuning, Pitfall #4) — sie bleiben in der Config als Re-Aktivierungs-Punkt
        # für eine künftige NautilusTrader-Version.
        params = {
            "donchian_period": trial.suggest_int("donchian_period", 8, 60),
            "ema_period": trial.suggest_int("ema_period", 20, 100),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 2, 24),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 4.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "Rsi2ReversionStrategy":
        # Issue #692 — Connors-RSI(2)-Pullback-/Bounce-Reversion. Untere `rsi_oversold`-Bound
        # (5.0) hält das Signal selektiv (RSI(2) ist stark verrauscht, Spec-Warnung: nicht auf 30
        # anheben, sonst degeneriert es zu einem Dauer-Signal).
        params = {
            "rsi_period": trial.suggest_int("rsi_period", 2, 6),
            "rsi_oversold": trial.suggest_float("rsi_oversold", 5.0, 25.0),
            "rsi_overbought": trial.suggest_float("rsi_overbought", 75.0, 95.0),
            "ema_period": trial.suggest_int("ema_period", 50, 200),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 1, 18),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 0.5, 3.0),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 2, _MAX_BARS_IN_TRADE_CAP),
        }
    elif strategy == "GapContinuationStrategy":
        # Issue #693 — Overnight-/Event-Gap-Continuation. Untere `gap_threshold_pct`-Bound
        # (0.5 %) hält genug Setups offen (Gap-Tage sind ohnehin selten, tournament_overrides
        # senkt min_trades zusätzlich, siehe strategies.json).
        params = {
            "gap_threshold_pct": trial.suggest_float("gap_threshold_pct", 0.005, 0.04),
            "atr_period": trial.suggest_int("atr_period", 7, 21),
            "atr_trailing_multiplier": trial.suggest_float("atr_trailing_multiplier", 1.0, 3.5),
            "max_bars_in_trade": trial.suggest_int("max_bars_in_trade", 3, _MAX_BARS_IN_TRADE_CAP),
        }
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    # Issue #713 — dyn_tp_enabled/lambda/gamma einheitlich für ALLE 15 Strategien angehängt (nicht
    # per-Strategie-Zweig dupliziert).
    params.update(_dyn_tp_params(trial))
    return params
