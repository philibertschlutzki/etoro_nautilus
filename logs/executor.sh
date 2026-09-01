#!/bin/bash

# Directory Change
cd ~/etoro_nautilus || exit 1

# Environment Activation
source venv/bin/activate

# Issue #1099 (Stufe 0, Sperrvermerk §5.9 / Empfehlung E-1) — jeder Sweep-Lauf bekommt ein
# frisches OPTIMIZER_WORK_DIR. Ein zweiter Sweep auf demselben Arbeitsverzeichnis reicht den
# Optuna-Store an nachfolgende Läufe weiter (Warm-Start/Store-Reuse) und wurde mit signifikantem
# Ertragsnachteil belegt (p = 0,046; Holdout-Median +5,46 bps bei frischem Arbeitsverzeichnis).
# OPTIMIZER_WORK_DIR bindet beim Modul-Import in manifest.py (WORK = Path(os.environ.get(...))),
# muss also VOR dem Prozessstart gesetzt sein — deshalb je Zeile per env-Prefix statt per
# CLI-Flag (sweep.py hat kein --work-dir).
run_sweep() {
    # Issue #1344 (GH #1238) Fix Punkt 1/3 — nimmt jetzt eine KOMMA-Liste von Symbolen entgegen
    # (sweep.py --symbols akzeptiert das nativ, "'all' (Universum) oder Komma-Liste") statt eines
    # einzelnen Symbols. Root-Cause #1344: ein Sweep mit GENAU EINEM Symbol macht jede
    # Preflight-Ablehnung (REJECT_DATA_DEGENERATE/REJECT_DATA_UNAVAILABLE) zum Totalausfall des
    # gesamten Laufs (symbols_planned=0, 0 Studies) — mit mehreren Symbolen je Invokation
    # arbeitet der Preflight-Loop in run_per_symbol_sweep die UEBRIGEN Symbole weiter ab und der
    # Lauf bleibt gueltig (run_status='complete'), solange k > 0 von n Symbolen bestehen.
    local symbols_csv="$1"
    local work_dir seed_salt label
    label="$(echo "$symbols_csv" | tr ',' '_' | tr -d '.' | cut -c1-40)"
    work_dir="data/optimizer/runs/${label}_$(date -u +%Y%m%dT%H%M%S%N)"
    mkdir -p "$work_dir"
    # Issue #1285 (GH #1158, Katalog #1272-1297) Fix Punkt 3 — JEDE Invokation bekommt einen
    # eigenen, zeitstempelbasierten --seed-salt: der neue sweep.py-Preflight
    # (assert_run_is_not_duplicate_preflight) bricht einen Lauf mit identischer Eingangsmenge zu
    # einem bereits im Index stehenden Lauf jetzt VOR Phase 1 fail-loud ab (statt wie bisher erst
    # nach vollem Durchlauf einen duplicate_of-Befund zu melden) — zwei Aufrufe DERSELBEN
    # run_sweep-Zeile sind sonst ab sofort ein Exit-Code-2-Abbruch statt der bisherigen stillen
    # Wallclock-Verschwendung (a9d80fba/f13f29db).
    seed_salt="$(date -u +%Y%m%dT%H%M%S%N)"
    echo "==> Sweep ${symbols_csv} -> OPTIMIZER_WORK_DIR=${work_dir} --seed-salt=${seed_salt}"
    OPTIMIZER_WORK_DIR="$work_dir" python -m automation.optimizer.sweep --strategies all --tier all --symbols "$symbols_csv" --seed-salt "$seed_salt"
}

# Sequential Sweep Execution
# Issue #1142 (Stufe 0, Sperrvermerk §5.2 / Empfehlung E-1) — zwei der drei Läufe eines
# Batches waren bit-identische Kopien (208/218 Study-Felder identisch): ohne --seed-salt
# (#1123/#1253) ist ein zweiter/dritter Lauf auf derselben Eingangsmenge keine Stichprobe,
# sondern eine Wiederholung, die nur Wallclock kostet (Ersparnis 0,92 h/Batch). Issue #1285 —
# jede run_sweep-Invokation traegt seither IMMER einen frischen --seed-salt (siehe run_sweep
# oben). Issue #1344 (GH #1238) — die frühere Ein-Symbol-je-Zeile-Form ist durch
# Komma-Batches ersetzt: PLTR/ASML/KRYS/LULU/NATGAS bleiben als bewusste
# Wallclock-Entscheidung deaktiviert (unverändert).
#run_sweep PLTR.ETORO,ASML.ETORO,KRYS.ETORO,LULU.ETORO,NATGAS.ETORO
run_sweep "TSLA.ETORO,NVDA.ETORO,GOOGL.ETORO"