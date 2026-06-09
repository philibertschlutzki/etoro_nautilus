# Feature-Roadmap & Optimierungs-Handbuch

> **Dateiname:** `manuals/feature_roadmap.md`
> **System-Version:** v2.0 (Standalone `automation/`, Shift-Left Data Quality, Walk-Forward + OOS-Gate)
> **Zielgruppe:** Operatoren, Quant-Entwickler und AI-Coding-Agenten (Jules)
> **Zweck:** (1) Mathematisch und logisch fundierte Optimierungen zur Steigerung der risiko-adjustierten Netto-Rendite, **streng aufbauend** auf der bestehenden Architektur. (2) Verbindliche Klärung der Stellen, die das Master-`README.md` und die Handbücher als „noch nicht final / in Optimierung" markieren.

---

## 0. Risiko- und Methodik-Hinweis

Dieses Dokument beschreibt **methodische und statistische Verbesserungen am Trading-System**, keine Anlageberatung und keine Garantie auf Profit. Backtest-Ergebnisse sind **in-sample-überoptimiert anfällig**; vergangene Performance ist kein Indikator für zukünftige Resultate. Jede hier vorgeschlagene Änderung muss vor Live-Schaltung im Dry-Run + OOS-Gate validiert werden. Der Sinn der meisten Vorschläge ist **Reduktion von Overfitting und Drawdown** — robustere statt scheinbar höhere Renditen sind das tragfähige Ziel (vgl. `README.md`, Echtgeld-Warnung).

**Notation (durchgängig):**

- $E$ = aktuelles Equity (Kontowert), $r_t$ = Trade-/Perioden-Rendite.
- $\mu, \sigma$ = Mittelwert/Standardabweichung der Renditen; $\sigma^-$ = Downside-Deviation.
- $p$ = empirische Trefferquote (Win-Rate), $W$ = mittlerer Gewinn, $L$ = mittlerer Verlust (Betrag, $>0$).
- $b = W/L$ = Payoff-Ratio; Erwartungswert pro Trade $\text{Exp} = pW - (1-p)L$.
- $N$ = Anzahl getesteter Strategie-Symbol-Kombinationen (Turnier-Trials).
- ATR = Average True Range (bereits im `HourlyStrategyBase`-Trailing-Stop, Multiplikator 1.5).

**Priorisierungs-Konvention:** P0 = höchster Hebel/Sicherheitsrelevanz, P3 = optional/experimentell. Die Reihung am Ende (Abschnitt H) ist verbindlich für die Umsetzungsplanung.

---

## A. Selektions-Integrität & Overfitting-Schutz

> **Problem:** Das Turnier evaluiert pro Tag $N = (\text{aktive Strategien}) \times (\text{Symbole}) \approx 8 \times N_{sym}$ Kombinationen. Die *maximale* beobachtete Sortino-/Sharpe-Ratio ist schon unter der Nullhypothese (kein echter Edge) systematisch nach oben verzerrt (Selection-Bias / Multiple-Testing). Ein hoher Turnier-Sieger-Sortino bedeutet daher **nicht zwingend** echten Edge.

### A1 — Deflated Sharpe Ratio (DSR) als zusätzliches Gate · **P0**

**Idee:** Die beobachtete Ratio gegen die *erwartete maximale Ratio unter Zufall* über $N$ Trials abwerten (López de Prado / Bailey).

**Mathematik (Skizze):** Die erwartete maximale Sharpe-Ratio über $N$ unabhängige, edge-lose Trials ist näherungsweise

$$\widehat{SR}_0 \approx \sqrt{V}\left[(1-\gamma)\,Z^{-1}\!\left(1-\tfrac{1}{N}\right) + \gamma\,Z^{-1}\!\left(1-\tfrac{1}{N e}\right)\right],$$

mit $V = \operatorname{Var}\{\widehat{SR}_n\}$ (Varianz der Trial-Ratios), $\gamma \approx 0.5772$ (Euler-Mascheroni), $Z^{-1}$ = inverse Standardnormal-CDF. Der DSR ist die Wahrscheinlichkeit, dass die wahre Ratio die Benchmark $\widehat{SR}_0$ übersteigt, korrigiert um Stichprobenlänge $T$, Schiefe $\hat\gamma_3$ und Wölbung $\hat\gamma_4$:

$$\text{DSR} = Z\!\left(\frac{(\widehat{SR}-\widehat{SR}_0)\sqrt{T-1}}{\sqrt{1-\hat\gamma_3\widehat{SR}+\frac{\hat\gamma_4-1}{4}\widehat{SR}^2}}\right).$$

**Erwarteter Effekt:** Filtert Glücks-Gewinner aus → weniger False-Positive-Deployments → höhere Live-Trefferquote des Turniers.

**Umsetzung:** Neues weiches Gate `min_dsr` (z. B. 0.90) in `tournament.json`; Berechnung in `backtest_runner.py` aus der bereits vorhandenen Verteilung aller Trial-Ratios (die Population existiert wegen „Rank first, Gate second" bereits vollständig). Startup-Logging der neuen Schwelle gemäß Observability-Regel (`AGENTS.md`, Pitfall #46).

### A2 — Probability of Backtest Overfitting (PBO via CSCV) · **P1**

**Idee:** Combinatorially Symmetric Cross-Validation auf der Turnier-Matrix. Die Performance-Matrix (Strategien × Zeit-Slices) in $S$ Blöcke teilen, über alle $\binom{S}{S/2}$ IS/OOS-Aufteilungen den IS-Besten bestimmen und dessen OOS-Rang prüfen.

**Mathematik:** $\text{PBO} = \Pr[\,\text{IS-Bester liegt OOS unter dem Median}\,] = \frac{1}{|C|}\sum_{c}\mathbf{1}\{\,\omega_c \le 0.5\,\}$, mit relativem OOS-Rang $\omega_c$.

**Erwarteter Effekt:** Liefert eine *systemweite* Overfitting-Kennzahl pro Lauf. Steigt PBO über z. B. 0.5, ist die gesamte Parametrisierung zu komplex → Warnsignal im Orchestrator-Log.

**Umsetzung:** Eigenes Diagnose-Modul (`automation/diagnostics/pbo.py`), aufgerufen in Phase 4; Ergebnis als JSON-Event und Feld `pbo` im Turnier-JSON. Kein Gate, sondern Observability + optionaler Abbruch bei `pbo > pbo_abort`.

### A3 — Stabilitäts-Selektion über mehrere Rolling-Fenster · **P1**

**Idee:** Statt nur den höchsten OOS-Score zu wählen, $K$ rollierende Walk-Forward-Fenster auswerten und Kandidaten mit **niedriger OOS-Varianz** bevorzugen (Konsistenz schlägt Spitzenwert).

**Mathematik:** Stabilitäts-bereinigter Score

$$\text{Score}^{\star} = \overline{\text{Score}}_{OOS} - \lambda_{stab}\cdot \operatorname{std}\big(\text{Score}_{OOS}^{(1..K)}\big).$$

`splits` in `backtest.json` steuert $K$ bereits; aktuell wird die Streuung jedoch nicht in den Score eingerechnet.

**Erwarteter Effekt:** Wählt robuste statt fragiler Strategien → glattere Live-Equity-Kurve, geringerer Realized-Drawdown.

**Umsetzung:** `lambda_stab` in `tournament.json`; Aggregation der Per-Fenster-Scores in `select_winners`.

### A4 — Echter Hard-Reset + Embargo statt „State Bleed" · **P2**

**Idee:** Den dokumentierten Kompromiss (Abschnitt G3) optional aufheben: an der IS/OOS-Grenze Engine-State zurücksetzen und ein **Embargo-Gap** (z. B. = max. Indikator-Warmup-Periode) einschieben, damit aufgewärmte Indikatoren/offene Positionen nicht ins OOS leaken (López de Prado: Purging & Embargo).

**Erwarteter Effekt:** OOS-Metriken werden methodisch „rein" → verlässlichere Live-Prognose. **Trade-off:** deutlich höhere Backtest-Laufzeit (mehrere Engine-Runs).

**Umsetzung:** Flag `walk_forward.hard_reset: true` + `embargo_bars` in `backtest.json`; bei aktivem Flag pro Fenster ein separater Engine-Run statt eines durchgehenden. Architektur-Constraint beachten: Worker-Signatur **nicht** ändern (`AGENTS.md`, Pitfall #30) — Parameter über das `strat`-Dict injizieren.

---

## B. Position-Sizing & Kapitalallokation

> **Problem:** Der `MomentumLSAllocator` verteilt freies Kapital **gleichmäßig** (`free_balance / pending_signals`) auf Symbole ohne offene Position. Das ignoriert die **Volatilität** der Instrumente vollständig: ein hochvolatiles Krypto erhält denselben USD-Betrag wie eine ruhige Aktie und dominiert dadurch den Portfolio-Drawdown.

### B1 — Fixed-Fractional-Risk-Sizing über ATR · **P0 (höchster Einzel-Hebel)**

**Idee:** Jeder Trade riskiert einen **konstanten Bruchteil des Equity** $r$ (z. B. 0,5–1 %), kalibriert am vorhandenen ATR-Stop (Multiplikator $k = 1.5$). Das koppelt Positionsgröße direkt an das Risiko statt an den Nominalbetrag.

**Mathematik:**

$$\text{Units} = \frac{r \cdot E}{k \cdot \text{ATR}}, \qquad \text{Notional} = \text{Units}\cdot \text{Price}.$$

**Erwarteter Effekt:** Gleichmäßige Risikobeiträge pro Position → niedrigerer und stabilerer Portfolio-Drawdown bei vergleichbarer Bruttorendite ⇒ höhere Sortino/Calmar. Direkt anschlussfähig, da der 1.5×-ATR-Stop bereits in `HourlyStrategyBase` existiert.

**Umsetzung:** Neuer Sizing-Modus `risk_pct` in der Hierarchie `allocator > trade_amount_usd > trade_amount_pct > risk_pct > Default` (`AGENTS.md`, Issue #182). Quantisierung strikt auf `size_increment`/tick-precision (Pitfall #194), Fail-Closed via `return None` unter Increment (Pitfall #45). $11-Floor unverändert.

### B2 — Inverse-Volatility-Gewichtung im Allocator · **P1**

**Idee:** Slicing nicht gleichmäßig, sondern invers zur realisierten Volatilität.

**Mathematik:** Gewicht je Symbol $i$ ohne offene Position:

$$w_i = \frac{1/\sigma_i}{\sum_{j} 1/\sigma_j}, \qquad \text{Allokation}_i = w_i \cdot \text{free\_balance}.$$

(Diagonal-Approximation der Risk-Parity; $\sigma_i$ aus rollierender Stunden-Rendite, z. B. 168 Bars.)

**Erwarteter Effekt:** Angleichung der Risikobeiträge auf Portfolio-Ebene; reduziert die Dominanz volatiler Krypto-Positionen. **No-Interference-Regel und $11-Floor bleiben unangetastet.**

**Umsetzung:** `momentum_ls_allocator.py` — `get_allocation()` erhält Zugriff auf eine rollierende Vol-Schätzung (aus dem Parquet-Katalog vorgewärmt, analog `_warmup_trend_filter`, Issue #213).

### B3 — Fraktionales Kelly als Obergrenze · **P2**

**Idee:** Aus den Backtest-Statistiken die Kelly-Fraktion ableiten und **gedeckelt** (fractional Kelly) als Sizing-Cap nutzen — maximiert langfristiges geometrisches Wachstum, ohne die Ruin-Gefahr des vollen Kelly.

**Mathematik:**

$$f^{\star} = p - \frac{1-p}{b} = \frac{pW-(1-p)L}{W} = \frac{\text{Exp}}{W}, \qquad f_{\text{used}} = \alpha\,f^{\star},\ \alpha\in[0.25,\,0.5].$$

**Erwarteter Effekt:** Theoretisch wachstumsoptimale Allokation. **Warnung:** Kelly ist extrem schätzfehler-sensitiv; nur mit $\alpha \le 0.5$, nur als **Cap** (nie als Hebel nach oben), und nur für Paare mit ausreichender Stichprobe ($n \ge$ `min_trades`).

**Umsetzung:** `f_used` als oberer Deckel auf das B1-Sizing in `HourlyStrategyBase`; $p, W, L$ aus den im Turnier-JSON bereits vorliegenden Per-Paar-Metriken.

---

## C. Regime-Erkennung & Long-only-Mitigation

> **Problem:** eToro lehnt REAL-Shorts still ab (`IsBuy:False` → verworfen), das System ist faktisch **Long-only**. Krypto-Strategien produzieren in Bärenmärkten mathematisch korrekt stark negative Sortino-Werte (`AGENTS.md`, Issue #232) und werden zwar organisch im OOS-Gate gefiltert — aber erst *nachdem* Kapital potenziell in fallenden Märkten alloziert wurde.

### C1 — Portfolio-Regime-Gate mit Cash-Overlay · **P1**

**Idee:** Statt synthetischer Shorts (durch das fixe Smart-Portfolio-Universe ohnehin nicht frei wählbar) ein **Market-Timing-Overlay**: in klar bärischen Regimes auf **Cash gehen** (keine neue Long-Position), statt long zu halten. Long-only-kompatibel.

**Mathematik (Regime-Klassifikator):** Kombiniere Trendstärke und Mean-Reversion-Neigung:
- Trend: Vorzeichen/Steigung eines langen SMA bzw. ADX-Schwelle.
- Persistenz: rollierender **Hurst-Exponent** $H$ ($H>0.5$ trendend, $H<0.5$ mean-revertierend) über R/S- oder DFA-Schätzung.

Routing: Momentum/Breakout-Strategien nur bei $H>0.5 \wedge \text{Trend}>0$; Mean-Reversion nur bei $H<0.5$; sonst **Cash**.

**Erwarteter Effekt:** Vermeidet das Deployen der falschen Strategie-Archetypen in der falschen Marktphase und reduziert Long-Verluste in Abwärtsmärkten — adressiert die dokumentierte Krypto-Degradation an der Wurzel.

**Umsetzung:** Erweiterung des bestehenden `trend_filter` (Issue #213) zu einem Regime-Klassifikator in `HourlyStrategyBase` (`can_go_long` bleibt das Fail-Closed-Gate); Parameter in `strategy_defaults.json` (`regime_lookback`, `hurst_window`, `adx_threshold`).

### C2 — Regime-konditionale Krypto-Teilnahme · **P2**

**Idee:** Krypto-Symbole nur dann ins Turnier/Live aufnehmen, wenn das Krypto-Regime nicht bärisch ist (Spezialfall von C1 für die Asset-Klasse mit den weitesten Spreads, 15 bps).

**Erwarteter Effekt:** Senkt die strukturell negativen Krypto-Beiträge, ohne die Asset-Klasse generell zu verbieten.

**Umsetzung:** Asset-Class-Filter in Phase 1/3 des `daily_orchestrator.py`, gesteuert über `backtest.json` (`crypto_requires_bull_regime: true`).

---

## D. Kosten- & Ausführungs-Optimierung

> **Problem:** Der Composite-Score belohnt Sortino/PF/WinRate, **penalisiert aber Turnover nicht**. Bei eToro-Spreads (EQUITY 8 bps, CRYPTO 15 bps) frisst hohe Handelsfrequenz die Bruttorendite. Mehrere Pitfalls (#47, #50, #88) drehen sich genau um Overtrading-Kaskaden.

### D1 — Kostengewichteter Net-Score · **P1**

**Idee:** Turnover explizit im Score bestrafen, sodass churn-intensive Strategien nur bei entsprechend höherem Brutto-Edge gewinnen.

**Mathematik:**

$$\text{Score}_{\text{net}} = \underbrace{(\text{Sortino}\cdot 0.4 + \text{PF}\cdot 0.3 + \text{WinRate}\cdot 0.2 - \text{MaxDD}\cdot 0.1)}_{\text{bestehender Composite}} - \lambda_{\text{cost}}\cdot \text{Turnover}\cdot \text{spread\_bps}.$$

Turnover = Trades pro Zeiteinheit; `spread_bps` aus `spread_bps_by_asset_class`.

**Erwarteter Effekt:** Bevorzugt Strategien mit hoher **Netto**-Effizienz; dämpft die dokumentierten Overtrading-Muster zusätzlich zu den bestehenden Cooldown-Guards.

**Umsetzung:** `lambda_cost` in `tournament.json`; `compute_tournament_score` (Pitfall #35) um den Turnover-Term erweitern; Startup-Logging der Gewichtung.

### D2 — Statistisch signifikanter Erwartungswert (Expectancy-t-Gate) · **P2**

**Idee:** Das bestehende `min_expectancy`-Gate (0.00005) um eine **Signifikanzbedingung** ergänzen: der Per-Trade-Edge muss messbar über dem Rauschen liegen.

**Mathematik:** $t = \dfrac{\bar\mu_{\text{trade}}}{s_{\text{trade}}/\sqrt{n}}$; Forderung $t > t_{\text{crit}}$ (z. B. 1.65 für einseitig 95 %).

**Erwarteter Effekt:** Schließt Strategien aus, deren positiver Erwartungswert statistisch nicht von 0 unterscheidbar ist → weniger Glücks-Deployments.

**Umsetzung:** Zusatz in `_is_eligible`; nutzt die bereits gesammelten Per-Trade-PnLs.

---

## E. Portfolio-Konstruktion & Risiko

### E1 — Korrelations-bewusste Exposure-Caps · **P1**

> **Problem:** Werden mehrere stark korrelierte Symbole gleichzeitig long gehalten (z. B. mehrere US-Tech-Aktien), ist das Portfolio-Risiko viel höher als die Summe der Einzel-Drawdowns suggeriert.

**Mathematik:** Portfolio-Varianz $\sigma_p^2 = \mathbf{w}^{\top}\Sigma\,\mathbf{w}$. Constraint: Summe der Gewichte je Korrelations-Cluster (Schwelle z. B. $\rho > 0.7$) gedeckelt; alternativ direkte Begrenzung von $\sigma_p$.

**Erwarteter Effekt:** Verhindert Klumpenrisiken, glättet die aggregierte OOS-Equity-Kurve (die seit Issue #286/#303 ohnehin chronologisch gemergt vorliegt — die Datengrundlage ist bereits vorhanden).

**Umsetzung:** Korrelationsmatrix aus dem Parquet-Katalog in `momentum_ls_allocator.py`; Cluster-Cap als Allocator-Constraint **nach** der No-Interference-Regel.

### E2 — Drawdown-adaptives De-Risking · **P2**

**Idee:** Bei Überschreiten eines rollierenden Equity-Drawdowns das aggregierte Risikobudget temporär reduzieren (z. B. $r$ aus B1 halbieren), bis sich die Kurve erholt — ein einfacher „Equity-Curve-Trading"-Schutz.

**Mathematik:** Risiko-Skalar $s = 1$ falls $\text{DD}_t < \text{DD}_{\text{soft}}$, sonst linear bis 0 zwischen `DD_soft` und `DD_hard`.

**Erwarteter Effekt:** Begrenzt Verlustserien; schützt Kapital in adversen Phasen.

**Umsetzung:** Globaler Risiko-Skalar im Allocator, gespeist aus der Live-Equity (über `account.balance_total()`, typsicher gemäß Pitfall #39).

---

## F. Metrik-Verbesserungen (Präzision der Bewertung)

### F1 — Bayesianische Profit-Factor-Schätzung statt harter Caps · **P2**

> **Problem:** Bei wenigen Verlusttrades explodiert der PF; die aktuellen harten Caps (50.0) sind grob und verzerren Mediane (deshalb das Sentinel-Filtering, Issue #263).

**Idee:** PF aus **shrinkage-/Bayes-geschätzten** Gewinn-/Verlustsummen bilden (Prior auf Verlustseite verhindert Division-nahe-Null), statt nackt zu kappen.

**Mathematik (Skizze):** $\widehat{PF} = \dfrac{\sum W_i + \alpha_0}{\sum L_i + \beta_0}$ mit schwachen Priors $\alpha_0,\beta_0$. Liefert für Low-Sample-Fälle stetige, endliche Werte statt Sentinels.

**Erwarteter Effekt:** Stabilere Aggregat-Mediane; macht das Sentinel-Filtering größtenteils überflüssig. **Constraint:** Die bestehenden Capping-/Shrinkage-Gatekeeper (Issue #288) dürfen nicht entfernt, nur ergänzt werden.

### F2 — Konsistente Sortino-MAR & Drawdown-Basis dokumentieren · **P3**

**Idee:** Minimum Acceptable Return (MAR) der Downside-Deviation explizit fixieren (MAR = 0 oder risikofrei) und die **Realized-FIFO-PnL-Basis** des Drawdowns (vs. Mark-to-Market) als bewusste Designentscheidung verankern (bereits in Issue #276 begonnen).

**Erwarteter Effekt:** Reproduzierbarkeit/Vergleichbarkeit der Metriken; verhindert künftige Fehlinterpretationen.

---

## G. Klärung „unklarer" Architekturpunkte (Referenzziel des README)

Diese Abschnitte beantworten verbindlich die Stellen, an denen `README.md` und Handbücher auf dieses Dokument verweisen. Sie beschreiben **gewollte** Kompromisse — kein Bug, keine „Reparatur".

### G1 — Warum die hybride Aggregation gewollt ist

Eine reine Mittelung über Paare würde die mathematische Identität der Einzel-Backtests zerstören („Frankenstein-Metriken", Issue #255). Daher: **Volumen summieren** (Trades, Wins → Count-Ratio-WinRate), **Rendite trade-gewichten**, **Risiko-Ratios medianisieren** (Nenner-Abweichungen verbieten Summation), **`max_drawdown` aus der chronologisch gemergten OOS-Equity** (Issue #286/#303). Das OOS-Gate normalisiert intern `total_trades / n_res`, damit die Portfolio-Trade-Summe die Per-Symbol-Schwelle `oos_min_trades` nicht trivial überschreitet (Trade-Sum-Trap).

### G2 — Warum negative Krypto-Sortinos kein Precision-Bug sind

Das Sizing für Krypto-Bruchstücke (`size_precision=8`) funktioniert; die negativen Werte resultieren aus **Long-only + weite Spreads + Abwärtsvolatilität** (Issue #232). Die Mitigation ist **kein** Precision-Fix, sondern das Regime-Gate aus Abschnitt C.

### G3 — Warum „State Bleed" derzeit akzeptiert wird

Ein einziger durchgehender Engine-Run minimiert Laufzeit; der retrospektive Timestamp-Split spart $K$ Engine-Starts. Preis: OOS ist nicht vollständig unabhängig. Die saubere Alternative ist A4 (Hard-Reset + Embargo), bewusst als optionales, laufzeitintensiveres Feature.

### G4 — Warum der $11-Floor und No-Interference hart bleiben

$11 ist das eToro-Mindest-Margin; darunter werden Orders API-seitig abgelehnt. No-Interference verhindert, dass der Allocator in laufende Execution-Zyklen eingreift. **Alle** Sizing-Optimierungen (B1–B3, E1–E2) müssen diese beiden Invarianten respektieren.

---

## H. Verbindliche Priorisierung & Umsetzungsreihenfolge

| Rang | Feature | Abschnitt | Prio | Primärer Effekt | Hauptdateien |
|------|---------|-----------|------|-----------------|--------------|
| 1 | Fixed-Fractional-Risk-Sizing (ATR) | B1 | **P0** | ↓ Drawdown, ↑ Sortino/Calmar | `strategies/hourly_strategy_base.py`, `config/strategy_defaults.json` |
| 2 | Deflated Sharpe Ratio Gate | A1 | **P0** | ↓ False-Positives | `backtest_runner.py`, `config/tournament.json` |
| 3 | Kostengewichteter Net-Score | D1 | P1 | ↑ Netto-Edge, ↓ Overtrading | `backtest_runner.py`, `config/tournament.json` |
| 4 | Inverse-Vol-Allokation | B2 | P1 | Risiko-Angleichung | `momentum_ls_allocator.py` |
| 5 | Portfolio-Regime-Gate / Cash-Overlay | C1 | P1 | ↓ Long-Verluste in Bärenmärkten | `strategies/hourly_strategy_base.py` |
| 6 | Stabilitäts-Selektion (Multi-Fenster) | A3 | P1 | Robustheit | `backtest_runner.py` |
| 7 | Korrelations-bewusste Caps | E1 | P1 | ↓ Klumpenrisiko | `momentum_ls_allocator.py` |
| 8 | PBO-Diagnose | A2 | P1 | Overfitting-Observability | `automation/diagnostics/pbo.py` |
| 9 | Fraktionales Kelly (Cap) | B3 | P2 | Geometr. Wachstum | `strategies/hourly_strategy_base.py` |
| 10 | Expectancy-t-Gate | D2 | P2 | Signifikanz-Filter | `backtest_runner.py` |
| 11 | Krypto-Regime-Teilnahme | C2 | P2 | ↓ Krypto-Verluste | `daily_orchestrator.py` |
| 12 | Drawdown-adaptives De-Risking | E2 | P2 | Kapitalschutz | `momentum_ls_allocator.py` |
| 13 | Hard-Reset + Embargo | A4 | P2 | OOS-Reinheit | `backtest_runner.py`, `config/backtest.json` |
| 14 | Bayes-PF | F1 | P2 | Metrik-Stabilität | `backtest_runner.py` |
| 15 | Sortino-MAR/Drawdown-Doku | F2 | P3 | Reproduzierbarkeit | `AGENTS.md`, `backtest_runner.py` |

**Verbindliche Umsetzungs-Constraints für Jules:**
- Jeder neue Gating-/Score-Parameter MUSS im Startup-Header von `backtest_runner.py` geloggt werden (Observability-Regel, Pitfall #46). „Hidden Gates" sind untersagt.
- Die Worker-Signatur in `backtest_runner.py` darf NICHT geändert werden — neue Parameter über das `strat`-Dict injizieren (Pitfall #30).
- Tupel-Arity-Koppelung (Erzeugung ↔ Entpackung) und `total_trades > 0`-Assertions bleiben unangetastet (Pitfalls #33, #45).
- Bestehende Capping-/Shrinkage-Gatekeeper (Issue #288) dürfen nur ergänzt, nie entfernt werden.
- Chirurgische, einzeln testbare Commits; `pytest`-Gate grün; deutsche Log-Sprache; Changelog-Eintrag in `AGENTS.md`.
- $11-Floor, No-Interference und der dreistufige Echtgeld-Interlock sind harte Invarianten.

---

## I. Validierungs-Workflow für jede Optimierung

1. Konfig-Änderung in `automation/config/*.json` (deklarativ, wo möglich).
2. `pytest -v` lokal grün (insb. `automation/tests/test_backtest_runner.py`, `test_oos_aggregation.py`).
3. Dry-Run: `python3 automation/daily_orchestrator.py --dry-run --skip-api-fetch`.
4. Auswertung gegen den Vortag (vgl. `manuals/strategie_optimierung.md`, Kapitel 6):
   - **OOS-Gate passiert == True** (harte Bedingung),
   - Median-In-Sample-Sortino stabil/gestiegen,
   - `win_count` (abgedeckte Symbole) stabil/gestiegen,
   - **zusätzlich (neu):** DSR/PBO im akzeptablen Bereich, Netto-Score nicht durch Turnover dominiert.
5. Erst nach mehreren stabilen Dry-Runs Commit + Transfer auf das produktive System.

---

*Erstellt am 2026-06-09. Dieses Dokument baut strikt auf `automation/AGENTS.md` (autoritativ) auf. Bei Umsetzung eines Features: Eintrag im AGENTS.md-Changelog und Querverweis hierher.*
