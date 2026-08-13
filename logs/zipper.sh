#!/usr/bin/env bash
set -euo pipefail

TARGET_FILE="${1:-optimizer_filtered_6dbc228c_20260807T205802986791.log}"
CHUNK_SIZE="20M"
BASENAME=$(basename "${TARGET_FILE}" .log)
OUT_DIR="${BASENAME}_llm_processing"

mkdir -p "${OUT_DIR}"

# Chunking: Line-Safe Byte-Split
split -C "${CHUNK_SIZE}" -d --additional-suffix=".log" "${TARGET_FILE}" "${OUT_DIR}/chunk_"

for chunk in "${OUT_DIR}"/chunk_*.log; do
    jsonl_file="${chunk%.log}.jsonl"
    
    # Payload-Isolierung
    sed -n 's/.*\[JSON_EVENT\] \(.*\)/ /p' "${chunk}" > "${jsonl_file}"
    
    # LLM Context Window Optimierung via jq
    if command -v jq &> /dev/null; then
        # Reduktion auf LLM-relevante Variablen
        jq -c '{check, scope, actual, expected, detail}' "${jsonl_file}" > "${chunk%.log}_llm_optimized.jsonl"
        
        # Aggregation der Fehlerverteilung
        jq -r '.check' "${jsonl_file}" | sort | uniq -c | sort -nr > "${chunk%.log}_check_distribution.txt"
    fi
done
