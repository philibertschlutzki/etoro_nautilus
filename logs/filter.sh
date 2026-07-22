#!/usr/bin/env bash

# Strictly typed Bash script to extract WARNING logs via grep.
# Fulfills CH environment compliance: uses standard GNU/BSD tools.

set -euo pipefail

# Config variables
readonly INPUT_LOG="optimizer_ec87551e_20260722T160352478562.log"
readonly OUTPUT_LOG="optimizer_warnings.log"

# Main processing block
if [[ -f "${INPUT_LOG}" ]]; then
    # Matches lines containing 'WARNING' or the specific emoji format
    grep -E "WARNING|⚠️" "${INPUT_LOG}" > "${OUTPUT_LOG}" || true
else
    echo "Error: Input file ${INPUT_LOG} not found." >&2
    exit 1
fi
