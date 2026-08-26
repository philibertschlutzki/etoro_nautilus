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
    local symbol="$1"
    local work_dir
    work_dir="data/optimizer/runs/${symbol%%.*}_$(date -u +%Y%m%dT%H%M%S%N)"
    mkdir -p "$work_dir"
    echo "==> Sweep ${symbol} -> OPTIMIZER_WORK_DIR=${work_dir}"
    OPTIMIZER_WORK_DIR="$work_dir" python -m automation.optimizer.sweep --strategies all --tier all --symbols "$symbol"
}

# Sequential Sweep Execution
# Issue #1142 (Stufe 0, Sperrvermerk §5.2 / Empfehlung E-1) — zwei der drei Läufe eines
# Batches waren bit-identische Kopien (208/218 Study-Felder identisch): ohne --seed-salt
# (#1123/#1253) ist ein zweiter/dritter Lauf auf derselben Eingangsmenge keine Stichprobe,
# sondern eine Wiederholung, die nur Wallclock kostet (Ersparnis 0,92 h/Batch). Genau EIN
# TSLA-Lauf, bis Wiederholungsläufe wieder mit --seed-salt gefahren werden.
#run_sweep GOOGL.ETORO
run_sweep TSLA.ETORO
#run_sweep PLTR.ETORO
run_sweep NVDA.ETORO
#run_sweep ASML.ETORO
#run_sweep KRYS.ETORO
#run_sweep LULU.ETORO
#run_sweep GOOGL.ETORO
#run_sweep TSLA.ETORO
run_sweep NVDA.ETORO
#run_sweep NATGAS.ETORO