#!/usr/bin/env python3
"""
Insert rhythm-parsing helpers into py/opll_mml.py.

Idempotent: does nothing if helpers already present.
Creates a timestamped backup of the original file before editing.
"""
from pathlib import Path
import sys
import shutil
from datetime import datetime

TARGET = Path('py/opll_mml.py')
if not TARGET.exists():
    print(f"Error: {TARGET} not found. Run from repository root.", file=sys.stderr)
    sys.exit(1)

src = TARGET.read_text(encoding='utf-8')

# If helpers look already present, exit safely
if '_ms2_note_to_fnum_block(' in src or 'parse_opll_regs_for_rhythm(' in src:
    print(f"Helpers already present in {TARGET}; no changes made.")
    sys.exit(0)

# Create a timestamped backup
bak = TARGET.with_suffix(TARGET.suffix + f'.bak.{datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}')
shutil.copy2(TARGET, bak)
print(f"Backup created: {bak}")

# Ensure import json exists
if 'import json' not in src:
    src = src.replace('import math\n', 'import math\nimport json\n', 1)

# Insertion block — uses triple-single quotes around the Python code to avoid nested triple-double conflicts
insert_code = '''
def _ms2_note_to_fnum_block(ms2_note: int) -> tuple[int, int]:
    """Approximate inverse of fnum_block_to_ms2_note.

    Given an MS2 note index (0 = C0), return a plausible (fnum, block)
    pair for YM2413 such that the resulting frequency maps back close to
    the requested note.  Returns (0, 0) if conversion fails.
    """
    if ms2_note is None:
        return 0, 0
    # MS2 note 0 = C0 = MIDI 12
    midi = ms2_note + 12
    freq = 440.0 * 2 ** ((midi - 69) / 12.0)
    # Try blocks 0..7 and pick a fnum that fits typical YM2413 range
    for block in range(0, 8):
        fnum = int(round(freq * (1 << 19) / (49716.0 * (1 << block))))
        if 1 <= fnum <= 0x1FF:
            return fnum, block
    return 0, 0


def parse_opll_regs_for_rhythm(regs_csv_path: str, default_len_s: float = 0.06) -> dict:
    """Parse the *_trace.opll_regs.csv for YM2413 rhythm control events.

    Returns a dict containing:
      - 'channels': mapping internal_ch_index -> list of {tick_start, tick_end, ms2_note, fnum, block, name}
      - 'instruments': mapping name -> hex-string register blobs

    Rhythm bit order (YM2413 manual): D4..D0 = BD, SD, TOM, T-CT, HH
    We map these to MS2 note indices (simple defaults) and emit short fixed-length
    pseudo-notes on separate logical channels.
    """
    result = {'channels': {}, 'instruments': {}}
    from pathlib import Path
    if not regs_csv_path or not Path(regs_csv_path).exists():
        return result

    # Bit order D4..D0 -> BD, SD, TOM, TCT, HH
    bit_positions = [4, 3, 2, 1, 0]
    names = ['BD', 'SD', 'TOM', 'TCT', 'HH']
    # Default MS2 note mapping (0=C0)
    ms2_notes = {
        'BD': 24,   # C2
        'SD': 36,   # C3
        'TOM': 30,  # D2
        'TCT': 48,  # C4
        'HH': 60,   # C5
    }

    # Read rhythm register timeline (ticks, val)
    events: list[tuple[int, int]] = []
    with open(regs_csv_path, 'r', newline='') as fh:
        for line in fh:
            line = line.rstrip('\\r\\n')
            if not line or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) < 6:
                continue
            typ = parts[0].strip()
            # regs CSV columns: type,time,ticks,addr,val,ch
            addr = parts[3].strip()
            try:
                ticks = int(parts[2])
                val = int(parts[4])
            except (ValueError, IndexError):
                continue
            # Accept either explicit 'rhythm' rows or addr==0x0E writes
            if typ == 'rhythm' or addr.lower() == '0x0e':
                events.append((ticks, val & 0xFF))

    if not events:
        return result

    # default length in ticks (assume 60 Hz tick base for trace CSV)
    ticks_per_sec = 60.0
    len_ticks = max(1, int(round(ticks_per_sec * default_len_s)))

    prev = 0
    for ticks, cur in events:
        # rising bits in lower 5 bits
        rising = (~prev & cur) & 0x1F
        for idx, bitpos in enumerate(bit_positions):
            mask = 1 << bitpos
            if rising & mask:
                name = names[idx]
                ms2_note = ms2_notes.get(name, 36)
                # compute approximate fnum/block for the ms2_note
                midi = ms2_note + 12
                freq = 440.0 * 2 ** ((midi - 69) / 12.0)
                fnum = 0
                block = 0
                for b in range(0, 8):
                    candidate = int(round(freq * (1 << 19) / (49716.0 * (1 << b))))
                    if 1 <= candidate <= 0x1FF:
                        fnum = candidate
                        block = b
                        break
                ch_index = NUM_CH + idx
                seg = {
                    'tick_start': ticks,
                    'tick_end': ticks + len_ticks,
                    'ms2_note': ms2_note,
                    'fnum': fnum,
                    'block': block,
                    'name': name,
                }
                result['channels'].setdefault(ch_index, []).append(seg)
        prev = cur

    # Rhythm instrument register blobs (from MBMS.MS2 supplied list)
    # Order: BD, SD, TOM, TCT, HH
    mbms_regs = {
        'BD':   [0x20,0x20,0x09,0x00,0xFA,0xF6,0xFF,0xFF,0x00,0x00,0x00],
        'SD':  [0x20,0x00,0x00,0x00,0xF1,0xF7,0x00,0xB8,0x00,0x00,0x0E],
        'TOM': [0x02,0x02,0x00,0x00,0x33,0x33,0x73,0x73,0x00,0x00,0x03],
        'TCT': [0x43,0x61,0x41,0x00,0xF9,0xF6,0xFC,0xFC,0x00,0x00,0x00],
        'HH':  [0x2F,0x2F,0x00,0x00,0xF1,0xA9,0x00,0x48,0x00,0x00,0x0E],
    }
    for name, regs in mbms_regs.items():
        result['instruments'][name] = ''.join(f'{b:02X}' for b in regs)

    return result
'''
# Find insertion point: after the "return segments" line of _build_segments
anchor = '    return segments\n'
idx = src.rfind(anchor)
if idx == -1:
    print("Error: couldn't find insertion anchor in py/opll_mml.py", file=sys.stderr)
    sys.exit(2)

new_src = src[:idx + len(anchor)] + '\n' + insert_code + '\n' + src[idx + len(anchor):]
TARGET.write_text(new_src, encoding='utf-8', newline='\n')
print(f"Patched {TARGET} (backup: {bak})")