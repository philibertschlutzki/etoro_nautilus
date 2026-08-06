#!/usr/bin/env bash

# Strictly typed Bash script to extract WARNING and ERROR logs via grep.
# Fulfills CH environment compliance: uses standard GNU/BSD tools.
#
# Issue #934 — INPUT_LOG war eine hart kodierte Konstante, die bei jedem Lauf von Hand
# nachgezogen werden musste; der Vorlauf zeigte nachweislich die falsche (aeltere) Datei, ohne
# dass es auffiel. Fix: optionales Argument $1 (der Dateiname); ohne Argument wird die NEUESTE
# optimizer_*.log per `ls -t` gewaehlt und nach stderr gemeldet. Der Ausgabename wird aus dem
# Eingabenamen abgeleitet, damit zwei Laeufe sich nicht gegenseitig ueberschreiben.

set -euo pipefail

if [[ $# -ge 1 ]]; then
    readonly INPUT_LOG="$1"
else
    # Neueste optimizer_*.log im aktuellen Verzeichnis waehlen (mtime, wie `ls -t`).
    shopt -s nullglob
    candidates=(optimizer_*.log)
    shopt -u nullglob
    if [[ ${#candidates[@]} -eq 0 ]]; then
        echo "Error: kein Argument uebergeben und keine optimizer_*.log im aktuellen Verzeichnis gefunden." >&2
        echo "Usage: filter.sh [<log-file>]" >&2
        exit 1
    fi
    readonly INPUT_LOG="$(ls -t optimizer_*.log | head -1)"
    echo "Kein Argument uebergeben — neueste Log-Datei gewaehlt: ${INPUT_LOG}" >&2
fi

# Issue #934 — Ausgabename aus dem Eingabenamen abgeleitet (statt der festen Konstante
# optimizer_filtered.log), damit zwei Laeufe zwei getrennte Filter-Ausgaben erzeugen.
readonly INPUT_BASENAME="$(basename "${INPUT_LOG}")"
readonly INPUT_STEM="${INPUT_BASENAME%.log}"
readonly OUTPUT_LOG="optimizer_filtered_${INPUT_STEM#optimizer_}.log"

# Main processing block
if [[ -f "${INPUT_LOG}" ]]; then
    # Matches lines containing WARNING, ERROR, or the specific emojis (⚠️, ❌)
    grep -E "WARNING|ERROR|⚠️|❌" "${INPUT_LOG}" > "${OUTPUT_LOG}" || true
    echo "Gefiltert: ${INPUT_LOG} -> ${OUTPUT_LOG}" >&2
else
    echo "Error: Input file ${INPUT_LOG} not found." >&2
    exit 1
fi
