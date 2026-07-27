#!/usr/bin/env bash

# Strictly typed Bash script to extract WARNING and ERROR logs via grep.
# Fulfills CH environment compliance: uses standard GNU/BSD tools.

set -euo pipefail

# Config variables
readonly INPUT_LOG="optimizer_15773afa_20260726T160845482651.log"
readonly OUTPUT_LOG="optimizer_filtered.log"

# Main processing block
if [[ -f "${INPUT_LOG}" ]]; then
    # Matches lines containing WARNING, ERROR, or the specific emojis (⚠️, ❌)
    grep -E "WARNING|ERROR|⚠️|❌" "${INPUT_LOG}" > "${OUTPUT_LOG}" || true
else
    echo "Error: Input file ${INPUT_LOG} not found." >&2
    exit 1
fi
