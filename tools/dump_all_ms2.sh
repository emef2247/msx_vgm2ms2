#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./tools/dump_all_ms2.sh [SEARCH_DIR] [OUT_DIR] [MAX_PATTERNS]
#
# Examples:
#   ./tools/dump_all_ms2.sh ms2_reference dumps 20
#   ./tools/dump_all_ms2.sh outputs_ms2 dumps_outputs 10

SEARCH_DIR="${1:-.}"
OUT_DIR="${2:-ms2_dumps}"
MAX_PATTERNS="${3:-10}"

mkdir -p "$OUT_DIR"

# Find .MS2 files, sort stable
mapfile -t files < <(find "$SEARCH_DIR" -type f \( -iname "*.ms2" \) | LC_ALL=C sort)

if [[ ${#files[@]} -eq 0 ]]; then
  echo "No .MS2 files found under: $SEARCH_DIR" >&2
  exit 1
fi

# Write file list
list_path="$OUT_DIR/ms2_files.txt"
printf "%s\n" "${files[@]}" > "$list_path"
echo "Wrote list: $list_path (${#files[@]} files)"

# Dump each
for f in "${files[@]}"; do
  rel="${f#./}"
  # sanitize filename for log output
  safe="$(echo "$rel" | sed 's|/|__|g')"
  out="$OUT_DIR/${safe}.dump.txt"
  echo "Dumping: $f -> $out"
  python py/ms2_dump.py "$f" --max-patterns "$MAX_PATTERNS" >"$out"
done

echo "Done. Dumps in: $OUT_DIR"

