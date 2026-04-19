#!/usr/bin/env python3
"""
Apply rhythm-instrument plumbing to py/ms2_writer.py.

- Adds `rhythm_instruments` optional parameter to build_ms2_file signature.
- Includes logic to append rhythm instruments (11-byte OPL4 reg blobs) to
  the 2OP instruments chunk.
- Expands active channel selection when rhythm_instruments is provided.
- Assigns appended rhythm instruments as starting_instruments for logical
  rhythm channels NUM_CH..NUM_CH+4 when present.

This script is idempotent: if markers already present it will not reapply.
"""
from pathlib import Path
import sys
import shutil
from datetime import datetime
import re

TARGET = Path('py/ms2_writer.py')
if not TARGET.exists():
    print(f"Error: {TARGET} not found. Run from repository root.", file=sys.stderr)
    sys.exit(1)

src = TARGET.read_text(encoding='utf-8')

# quick idempotence check
if 'rhythm_instruments: dict | None = None' in src or 'Append rhythm instruments (MBMS' in src:
    print("Looks like the rhythm patch is already applied (no changes made).")
    sys.exit(0)

# Create timestamped backup
bak = TARGET.with_suffix(TARGET.suffix + f'.bak.{datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}')
shutil.copy2(TARGET, bak)
print(f"Backup created: {bak}")

# 1) Replace function signature for build_ms2_file to include rhythm_instruments param
# Match the def line including the closing "-> bytes:" and replace with new signature.
sig_re = re.compile(r'(def build_ms2_file\([^)]*\)\s*->\s*bytes:)', re.M)
new_sig = (
    "def build_ms2_file(\n"
    "    segments: dict,\n"
    "    voice_csv_path: str | None,\n"
    "    stem: str,\n"
    "    bpm: int = 120,\n"
    "    timer: int = 0,\n"
    "    base_ms2: str | None = None,\n"
    "    video: str = 'ntsc',\n"
    "    debug: bool = False,\n"
    "    rhythm_instruments: dict | None = None,\n"
    ") -> bytes:"
)
if not sig_re.search(src):
    print("Warning: could not find build_ms2_file signature to replace. Aborting.", file=sys.stderr)
    sys.exit(2)
src = sig_re.sub(new_sig, src, count=1)

# 2) Insert rhythm-append code after the user_patches block where n_2op_inst set.
# Find the line 'n_2op_inst = new_total' (which is inside the user_patches handling).
insert_after_re = re.compile(r'(\n\s*n_2op_inst\s*=\s*new_total\s*\n)', re.M)
rhythm_append_code = r"""
    # ── Append rhythm instruments (MBMS 11-byte OPL4 register rows) ───────
    # rhythm_instruments: dict name -> hexstring (11 bytes) using reg_order:
    #   [0x20,0x21,0x40,0x41,0x60,0x61,0x80,0x81,0xE0,0xE1,0xC0]
    rhythm_name_to_idx = {}
    if rhythm_instruments:
        extra_rhythm = b''
        # deterministic order: BD, SD, TOM, TCT, HH
        order = ['BD', 'SD', 'TOM', 'TCT', 'HH']
        appended = 0
        for name in order:
            hexstr = rhythm_instruments.get(name)
            if not hexstr:
                continue
            try:
                regs = bytes.fromhex(hexstr)
            except Exception:
                continue
            regs = (regs + b'\x00' * 11)[:11]
            reg_order = [0x20, 0x21, 0x40, 0x41, 0x60, 0x61, 0x80, 0x81, 0xE0, 0xE1, 0xC0]
            opl4 = {reg_order[i]: regs[i] for i in range(len(reg_order))}
            inst_name = f'R:{name}'
            extra_rhythm += _build_2op_instrument(opl4, tuning_idx=0, name=inst_name)
            appended += 1
        if appended:
            new_total2 = n_2op_inst + appended
            instruments_data = struct.pack('<H', new_total2) + instruments_data[2:] + extra_rhythm
            # Map rhythm names to indices (0-based). They appear immediately after existing instruments
            start_idx = n_2op_inst
            i = 0
            for name in order:
                if name in rhythm_instruments and rhythm_instruments[name]:
                    rhythm_name_to_idx[name] = start_idx + i
                    i += 1
            n_2op_inst = new_total2
"""
m = insert_after_re.search(src)
if not m:
    print("Warning: could not find insertion point for rhythm append (n_2op_inst). Trying fallback...", file=sys.stderr)
    # fallback: try to find the user_patches block end by locating 'n_2op_inst = new_total' without surrounding newlines
    idx = src.find('n_2op_inst = new_total')
    if idx == -1:
        print("Fatal: cannot locate user_patches end. Aborting.", file=sys.stderr)
        sys.exit(3)
    # insert right after that occurrence
    insert_pos = idx + len('n_2op_inst = new_total')
    src = src[:insert_pos] + '\n' + rhythm_append_code + src[insert_pos:]
else:
    insert_pos = m.end(1)
    src = src[:insert_pos] + rhythm_append_code + src[insert_pos:]

# 3) Replace active channel selection block with expanded logic
# Find the section that computes active_channels and n_channels: we detect by the comment heading.
active_block_re = re.compile(
    r"(\n\s*# ── Determine active channel count[\s\S]*?n_channels\s*=\s*max\(1,\s*len\(active_channels\)\)\s*)",
    re.M
)
new_active_block = r"""
    # ── Determine active channel count ───────────────────────────────────
    # Count only OPLL channels that actually have note data (keyon=1 segments).
    # Unused channels are excluded to keep the MS2 file small and correct.
    # When rhythm_instruments are provided, include logical rhythm channels
    # NUM_CH .. NUM_CH+4 so they can be wired to appended instruments.
    active_channels = []
    # Determine maximum referenced channel in segments (safety)
    max_seg_ch = max(list(segments.keys())) if segments else (NUM_CH - 1)
    desired_max = max(max_seg_ch, NUM_CH - 1)
    if rhythm_instruments:
        desired_max = max(desired_max, NUM_CH + 4)
    # Cap to supported zero-based MS2 channel max (0..16 => 1..17 supported)
    desired_max = min(desired_max, 16)
    for ch in range(desired_max + 1):
        if rhythm_instruments:
            # include all channels up to desired_max when rhythm requested
            active_channels.append(ch)
        else:
            if any(seg.keyon for seg in segments.get(ch, [])):
                active_channels.append(ch)
    n_channels = max(1, len(active_channels))
"""
m2 = active_block_re.search(src)
if not m2:
    print("Warning: active channel selection block not found; attempting heuristic replacement...", file=sys.stderr)
    # Try to find 'active_channels = []' and 'n_channels = max(1, len(active_channels))' and replace between them
    start_idx = src.find('active_channels = []')
    end_idx = src.find('n_channels = max(1, len(active_channels))')
    if start_idx == -1 or end_idx == -1:
        print("Fatal: cannot locate active_channels block. Aborting.", file=sys.stderr)
        sys.exit(4)
    src = src[:start_idx] + new_active_block + src[end_idx + len('n_channels = max(1, len(active_channels))'):]
else:
    src = src[:m2.start(1)] + new_active_block + src[m2.end(1):]

# 4) Insert starting_instruments rhythm wiring after the call that builds starting_instruments
si_re = re.compile(r'(\n\s*starting_instruments\s*=\s*_get_starting_instruments\([^)]*\)\s*)', re.M)
si_code = r"""
    # If rhythm instruments were appended, ensure rhythm logical channels
    # (original indices NUM_CH .. NUM_CH+4) receive those instruments as
    # their starting instruments (when the channel is present in active_channels).
    if rhythm_name_to_idx:
        for idx, name in enumerate(['BD', 'SD', 'TOM', 'TCT', 'HH']):
            orig_ch = NUM_CH + idx
            if orig_ch in active_channels:
                out_idx = active_channels.index(orig_ch)
                inst_idx = rhythm_name_to_idx.get(name)
                if inst_idx is not None and out_idx < len(starting_instruments):
                    starting_instruments[out_idx] = inst_idx
"""
m3 = si_re.search(src)
if not m3:
    print("Warning: could not find starting_instruments call to append wiring; aborting.", file=sys.stderr)
    sys.exit(5)
src = src[:m3.end(1)] + si_code + src[m3.end(1):]

# Write back
TARGET.write_text(src, encoding='utf-8', newline='\n')
print(f"Patched {TARGET} (backup: {bak})")
