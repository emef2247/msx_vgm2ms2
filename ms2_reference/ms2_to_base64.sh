#!/usr/bin/env bash
set -euo pipefail

# Create .base64 files alongside .MS2 (or under OUT_DIR if given)
#
# Usage:
#   ./tools/ms2_to_base64.sh [SEARCH_DIR] [OUT_DIR] [WRAP]
#
# SEARCH_DIR: directory to scan for *.MS2 (default: .)
# OUT_DIR:    output directory for *.MS2.base64 (default: same dir as input file)
# WRAP:       "0" = no wrap (one long line), "76" = MIME style wrap (default: 0)
#
# Examples:
#   ./tools/ms2_to_base64.sh ms2_reference "" 0
#   ./tools/ms2_to_base64.sh ms2_reference ms2_reference 76

SEARCH_DIR="${1:-.}"
OUT_DIR="${2:-}"
WRAP="${3:-0}"

mapfile -t files < <(find "$SEARCH_DIR" -type f \( -iname "*.ms2" \) | LC_ALL=C sort)

if [[ ${#files[@]} -eq 0 ]]; then
  echo "No .MS2 files found under: $SEARCH_DIR" >&2
  exit 1
fi

for f in "${files[@]}"; do
  in_dir="$(dirname "$f")"
  base="$(basename "$f")"           # e.g. BLOCKB.MS2
  out_dir="$in_dir"
  if [[ -n "$OUT_DIR" ]]; then
    out_dir="$OUT_DIR"
    mkdir -p "$out_dir"
  fi
  out_path="$out_dir/${base}.base64"

  echo "Base64: $f -> $out_path (wrap=$WRAP)"
  if [[ "$WRAP" == "0" ]]; then
    # GNU coreutils base64: -w 0 = no wrap
    base64 -w 0 "$f" > "$out_path"
    echo >> "$out_path"  # keep file ending newline for diffs/tools
  else
    base64 -w "$WRAP" "$f" > "$out_path"
  fi
done

echo "Done."

