#!/usr/bin/env bash
set -euo pipefail

# scripts/df-events-rotate.sh — Rotate events.jsonl by size (Phase 5)
#
# Rotates the events file when it exceeds a size threshold.
# Keeps the current file plus N rotated copies.
#
# Usage: bash scripts/df-events-rotate.sh [--max-size SIZE_MB] [--keep N] [--events FILE]

MAX_SIZE_MB=10
KEEP=3
EVENTS_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-size) MAX_SIZE_MB="$2"; shift 2 ;;
        --keep)     KEEP="$2"; shift 2 ;;
        --events)   EVENTS_FILE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: df-events-rotate.sh [--max-size SIZE_MB] [--keep N] [--events FILE]"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$EVENTS_FILE" ]]; then
    EVENTS_FILE="${_DF_EVENTS_FILE:-.dark-factory/events.jsonl}"
fi

if [[ ! -f "$EVENTS_FILE" ]]; then
    echo "Events file not found: $EVENTS_FILE" >&2
    exit 0
fi

# Get file size in bytes (portable: try GNU stat, BSD stat, then wc -c)
if stat --version >/dev/null 2>&1; then
    file_size=$(stat -c%s "$EVENTS_FILE" 2>/dev/null) || file_size=""
else
    file_size=$(stat -f%z "$EVENTS_FILE" 2>/dev/null) || file_size=""
fi
if [[ -z "$file_size" ]]; then
    file_size=$(wc -c < "$EVENTS_FILE" 2>/dev/null | tr -d ' ') || file_size=0
fi

max_size_bytes=$(( MAX_SIZE_MB * 1024 * 1024 ))

if (( file_size < max_size_bytes )); then
    echo "Events file is ${file_size} bytes (under ${MAX_SIZE_MB}MB threshold). No rotation needed."
    exit 0
fi

echo "Rotating events file (${file_size} bytes > ${max_size_bytes} bytes threshold)..."

# Remove oldest rotation if at limit
for i in $(seq "$KEEP" -1 1); do
    src="${EVENTS_FILE}.$((i - 1))"
    dst="${EVENTS_FILE}.${i}"
    if [[ "$i" -eq "$KEEP" ]] && [[ -f "$dst" ]]; then
        rm -f "$dst"
    fi
    [[ -f "$src" ]] && mv "$src" "$dst"
done

# Rotate current file
mv "$EVENTS_FILE" "${EVENTS_FILE}.0"
touch "$EVENTS_FILE"

echo "Rotated: ${EVENTS_FILE} → ${EVENTS_FILE}.0"
echo "Kept $KEEP rotated copies."
