# Architektur-Konzept: Instrumenten-spezifisches Strategie-Tuning

> **Dateiname:** manuals/instrument_specific_tuning_konzept.md  
> **System-Version:** Konzept-Entwurf (v2.1)  
> **Zielgruppe:** Architekten, Quant-Entwickler, Operatoren  
> **Status:** Proposal / Draft  

## Kapitel 1: Analyse der aktuellen Architektur (v2.0)

Deine Beobachtung ist absolut korrekt und trifft den Kern eines klassischen Quant-Dilemmas: das **"One-Size-Fits-All" vs. "Overfitting" Problem**.

Aktuell läuft der Optimierungsprozess (Phase 3) so ab:
1. Optuna generiert einen Parametersatz (z. B. für die `ComboTrendVwapStrategy`).
2. Dieser Parametersatz wird im Matrix-Backtest gegen das **gesamte Universum** (z. B. 70 Instrumente) simuliert.
3. Die Reward-Funktion (`automation/optimizer/reward.py`) berechnet einen globalen Score, basierend auf der Anzahl der gewonnenen Symbole (`win_count`) und der aggregierten OOS-Performance.

**Der aktuelle Schutzmechanismus (Phase 4):** Das System federt dieses Problem teilweise durch das **Tournament** ab. Wenn eine Strategie auf Aktien gut funktioniert, aber auf Kryptos wie ETC versagt, gewinnt sie im Tournament eben nur die Aktien-Symbole. Das System zwingt die Strategie also nicht in den Live-Handel für Instrumente, auf denen sie schlecht ist.

**Das bestehende Architektur-Problem:** Obwohl ungeeignete Instrumente im Tournament ignoriert werden, wurden die *Parameter* der Strategie zuvor so getunt, dass sie "im Durchschnitt" über das gesamte Universum den höchsten Reward erzielen. Die Strategie holt also auf ihrem Spezialgebiet (z. B. ETC) nicht das absolute Maximum heraus, weil der Optimizer Kompromisse eingehen musste, um globale Strafen (z. B. `penalty_dd_weight` aus Ausreißern bei Forex) gering zu halten.

---

## Kapitel 2: Lösungsansätze für die Architektur v2.1

Um maximale Gewinne mit minimalem Risiko pro Instrumenten-Typ zu erwirtschaften, stehen vier architektonische Lösungsansätze zur Verfügung.

### Ansatz 1: Asset-Class Cluster-Tuning (Der Sweet-Spot)

Anstatt das gesamte Universum auf einmal zu testen, wird das Universum im Vorfeld in Cluster unterteilt (z. B. `crypto_universe.json`, `tech_stocks_universe.json`). 

* **Wie es funktioniert:** Der Optimizer läuft mehrfach für dieselbe Strategie, aber jeweils isoliert gegen ein spezifisches Cluster.
* **Ergebnis:** Wir instanziieren im System mehrere Varianten: `ComboTrendVwapStrategy_Crypto`, `ComboTrendVwapStrategy_Equities`. Jede hat ihre eigenen optimalen Parameter in der `strategies.json`.
* **Vorteile:** Sehr einfach in der aktuellen Architektur abzubilden. Erfordert nur Anpassungen in `run_optimization.py` (Universum-Auswahl per CLI) und die Aufteilung der Daten.
* **Nachteile:** Erhöht die Compute-Zeit, da Optuna pro Cluster gestartet werden muss.

### Ansatz 2: "Winner-Takes-All" Reward-Shaping (Algorithmus-Fokus)

Wir belassen den Backtest gegen das gesamte Universum, ändern aber die Art, wie der Optimizer den "Erfolg" eines Parametersatzes bewertet.

* **Wie es funktioniert:** Die `compute_reward()` Funktion (in `reward.py`) wird dahingehend modifiziert, dass sie nur die "Top N" Instrumente bewertet, auf denen die Strategie am besten performt. Instrumente, bei denen die Strategie durchfällt (z. B. Krypto-Strategie auf Forex-Assets), fließen mit einer **Gewichtung von 0** in die globale Penalty-Berechnung ein, anstatt den Optuna-Trial hart abzustrafen.
* **Vorteile:** Der Optimizer sucht automatisch nach extremen "Edges" für bestimmte Instrumentengruppen, ohne künstlich eingebremst zu werden.
* **Nachteile:** Hohes Risiko, dass Optuna "Glückstreffer" (zufällige Gewinne bei extrem volatilen Meme-Coins) überbewertet.

### Ansatz 3: Dynamisch adaptive Parameter (Code-Level)

Wir lösen das Problem nicht im Optimizer, sondern in der Mathematik der Strategien selbst.

* **Wie es funktioniert:** Starre Konstanten werden aus den Strategie-Dateien verbannt. Ein Parameter heißt nicht mehr `stop_loss_pct = 2.0`, sondern wird dynamisch an die Baseline-Volatilität (z. B. ATR der letzten 14 Tage) gekoppelt: `stop_loss = 1.5 * ATR`. Optuna optimiert dann nur noch den **Multiplikator**, nicht den absoluten Wert.
* **Vorteile:** Eine einzige Konfiguration skaliert nahtlos von einem lethargischen ETF bis zur volatilsten Kryptowährung.
* **Nachteile:** Erfordert ein aufwendiges Refactoring der Python-Dateien unter `automation/strategies/`.

### Ansatz 4: Per-Symbol Micro-Tuning (Maximale Granularität)

Die radikalste Architektur: Die Optimierungsschleife wird für **jedes Symbol einzeln** durchlaufen.

* **Wie es funktioniert:** Optuna sucht die perfekten Parameter separat für `ETC.ETORO`, danach für `TSLA.ETORO`, usw. Die `strategies.json` erhält eine neue Hierarchie (`"instrument_overrides"`).
* **Vorteile:** Der absolute theoretische Maximal-Edge für jedes einzelne Asset.
* **Nachteile:** Katastrophales **Overfitting-Risiko**. Wenn ein Instrument nur eine kurze Historie hat, lernt die KI das Chartbild einfach auswendig. Zudem explodieren die Rechenkosten (70 Instrumente * 10 Strategien = 700 Optuna-Studien).

---

## Kapitel 3: Implementierungs-Roadmap

Für ein stabiles v2.1 Upgrade wird eine Kombination aus **Ansatz 1** und **Ansatz 2** empfohlen:

1. **Kurzfristige Quick-Wins:** Anpassung der Datei `run_optimization.py` -> Zeile 50. Aktuell ist dort das Universum hartcodiert (`universe_path = ... / "momentum_ls.json"`). Dieses sollte als Parameter (`--universe`) an die CLI übergeben werden können, um Cluster-Tuning (Ansatz 1) zu ermöglichen.
2. **Reward-Funktion lockern:** In `config/optimizer.json` den Parameter `penalty_unevaluable_oos` überdenken oder die `compute_reward` so anpassen, dass Assets, die ohnehin vom Tournament gefiltert werden, den Optimizer-Score der funktionierenden Assets nicht "herunterziehen".
