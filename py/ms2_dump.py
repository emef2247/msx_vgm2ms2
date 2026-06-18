"""
ms2_dump.py  –  Debug utility: dump MS2 file chunks and pattern data.

Usage:
    python py/ms2_dump.py <file.MS2> [--patterns] [--compare <other.MS2>]
"""

import struct
import sys
import os
import csv
import io

# ---------------------------------------------------------------------------
# Chunk-type labels
# ---------------------------------------------------------------------------
CHUNK_TYPES = {
    0: 'Sentinel',
    1: 'Song Settings',
    2: 'Tunings',
    3: '2OP Instruments',
    4: '4OP Instruments',
    5: 'WAV Instruments',
    6: 'Patterns',
    7: 'Samples',
    8: 'Pitch Envelopes',
}

MS2_SIGNATURE = bytes([0x4D, 0x53, 0xC2, 0xB2, 0x0D, 0x0A, 0x00, 0x1A])


# ---------------------------------------------------------------------------
# Low-level chunk iterator
# ---------------------------------------------------------------------------

def iter_chunks(data: bytes):
    """Iterate over (offset, type, version, chunk_data) tuples in an MS2 file.

    Skips the 8-byte file signature.  Stops at the sentinel chunk (type 0)
    or end of file.
    """
    if len(data) < 8:
        raise ValueError("File too short to contain MS2 signature")
    if data[:8] != MS2_SIGNATURE:
        raise ValueError(f"Invalid MS2 signature: {data[:8].hex()}")

    pos = 8
    while pos + 4 <= len(data):
        hdr = data[pos:pos + 4]
        chunk_type, version, chunk_size = struct.unpack('<BBH', hdr)
        if chunk_type == 0:
            yield pos, 0, version, b''
            break
        if pos + 4 + chunk_size > len(data):
            print(f"  WARNING: chunk at {pos:#x} claims {chunk_size} bytes "
                  f"but only {len(data) - pos - 4} remain", file=sys.stderr)
            break
        chunk_data = data[pos + 4: pos + 4 + chunk_size]
        yield pos, chunk_type, version, chunk_data
        pos += 4 + chunk_size


# ---------------------------------------------------------------------------
# Chunk decoders for human-readable display
# ---------------------------------------------------------------------------

def _decode_song_settings(data: bytes) -> list[str]:
    if len(data) < 291:
        return [f"  (too short: {len(data)} bytes, expected 291)"]
    lines = []
    desc = data[0:65].rstrip(b'\x00 ').decode('ascii', errors='replace')
    lines.append(f"  Description:     '{desc}'")
    lines.append(f"  Song length:      {data[65]} patterns")
    loop = data[66]
    lines.append(f"  Loop pattern:     {loop if loop != 0xFF else 'none (-1)'}")
    timer = data[67]
    lines.append(f"  Timer:            {timer} ({'VDP' if timer == 0 else '50Hz'})")
    timer_div = struct.unpack_from('<H', data, 68)[0]
    lines.append(f"  Timer divider:    {timer_div / 256:.4f} (0x{timer_div:04X})")
    lines.append(f"  Modulation depth: 0x{data[70]:02X}")
    total_ch = data[71]
    lines.append(f"  Channels:         {total_ch} total  ({data[72]} 2OP + {data[73]} 4OP + {data[74]} WAV)")
    ch_types = list(data[75:117])
    enabled = sum(1 for t in ch_types if t and not (t & 0x80))
    lines.append(f"  Channel types:    {ch_types[:total_ch]}  ({enabled} enabled)")
    start_insts = list(data[117:159])
    lines.append(f"  Start instruments:{start_insts[:total_ch]}")
    n_tunings   = struct.unpack_from('<H', data, 243)[0]
    n_2op       = struct.unpack_from('<H', data, 248)[0]
    n_4op       = struct.unpack_from('<H', data, 253)[0]
    n_wav       = struct.unpack_from('<H', data, 258)[0]
    n_patterns  = struct.unpack_from('<H', data, 263)[0]
    n_pitchenv  = struct.unpack_from('<H', data, 268)[0]
    lines.append(f"  Tunings:          {n_tunings}")
    lines.append(f"  2OP instruments:  {n_2op}")
    lines.append(f"  4OP instruments:  {n_4op}")
    lines.append(f"  WAV instruments:  {n_wav}")
    lines.append(f"  Patterns:         {n_patterns}")
    lines.append(f"  Pitch envelopes:  {n_pitchenv}")
    return lines


def _decode_tunings(data: bytes) -> list[str]:
    if len(data) < 2:
        return ["  (too short)"]
    count = struct.unpack_from('<H', data, 0)[0]
    lines = [f"  Count: {count}"]
    pos = 2
    for i in range(count):
        if pos + 208 > len(data):
            lines.append(f"  Tuning {i}: (truncated)")
            break
        name = data[pos + 192: pos + 208].rstrip(b'\x00 ').decode('ascii', errors='replace')
        offsets = [struct.unpack_from('<h', data, pos + n * 2)[0] for n in range(96)]
        lines.append(f"  Tuning {i}: '{name}'  offsets[0..11]={offsets[:12]}")
        pos += 208
    return lines


# -------------------------- FM detail helpers -------------------------------

def _op20_fields(v: int) -> dict:
    return {
        'AM':    (v >> 7) & 0x01,
        'VIB':   (v >> 6) & 0x01,
        'EGT':   (v >> 5) & 0x01,   # EG type / sustain
        'KSR':   (v >> 4) & 0x01,
        'MULTI':  v       & 0x0F,
    }

def _op40_fields(v: int) -> dict:
    return {
        'KSL': (v >> 6) & 0x03,
        'TL':   v       & 0x3F,
    }

def _op60_fields(v: int) -> dict:
    return {
        'AR': (v >> 4) & 0x0F,
        'DR':  v       & 0x0F,
    }

def _op80_fields(v: int) -> dict:
    return {
        'SL': (v >> 4) & 0x0F,
        'RR':  v       & 0x0F,
    }

def _ope0_fields(v: int) -> dict:
    return {
        'WS': v & 0x07,  # waveform select
    }

def _c0_fields(v: int) -> dict:
    return {
        'FB':  (v >> 1) & 0x07,
        'ALG':  v       & 0x01,  # 2OP: 0/1, 4OPはC0/C3両方持つ
    }

def _inst2op_detail_rows(inst_index: int, name: str, tuning: int, regs: bytes) -> list[dict]:
    # regs layout from chunk type 3:
    # 20,21,40,41,60,61,80,81,E0,E1,C0
    r20_1, r20_2 = regs[0], regs[1]
    r40_1, r40_2 = regs[2], regs[3]
    r60_1, r60_2 = regs[4], regs[5]
    r80_1, r80_2 = regs[6], regs[7]
    re0_1, re0_2 = regs[8], regs[9]
    rc0 = regs[10]

    row_common = {
        'voice': inst_index,
        'name': name,
        'type': '2OP',
        'tuning': tuning,
        'FB': _c0_fields(rc0)['FB'],
        'ALG': _c0_fields(rc0)['ALG'],
    }

    op1 = {}
    op1.update(_op20_fields(r20_1))
    op1.update(_op40_fields(r40_1))
    op1.update(_op60_fields(r60_1))
    op1.update(_op80_fields(r80_1))
    op1.update(_ope0_fields(re0_1))

    op2 = {}
    op2.update(_op20_fields(r20_2))
    op2.update(_op40_fields(r40_2))
    op2.update(_op60_fields(r60_2))
    op2.update(_op80_fields(r80_2))
    op2.update(_ope0_fields(re0_2))

    row = dict(row_common)
    for k, v in op1.items():
        row[f'OP1_{k}'] = v
    for k, v in op2.items():
        row[f'OP2_{k}'] = v
    return [row]

def _inst4op_detail_rows(inst_index: int, name: str, tuning: int, regs: bytes) -> list[dict]:
    # regs layout from chunk type 4:
    # 20,21,40,41,60,61,80,81,E0,E1,23,24,43,44,63,64,83,84,E3,E4,C0,C3
    # OP1/2 from *0/*1 pair, OP3/4 from *3/*4 pair
    r20_1, r20_2 = regs[0], regs[1]
    r40_1, r40_2 = regs[2], regs[3]
    r60_1, r60_2 = regs[4], regs[5]
    r80_1, r80_2 = regs[6], regs[7]
    re0_1, re0_2 = regs[8], regs[9]

    r20_3, r20_4 = regs[10], regs[11]
    r40_3, r40_4 = regs[12], regs[13]
    r60_3, r60_4 = regs[14], regs[15]
    r80_3, r80_4 = regs[16], regs[17]
    re0_3, re0_4 = regs[18], regs[19]

    rc0, rc3 = regs[20], regs[21]

    row = {
        'voice': inst_index,
        'name': name,
        'type': '4OP',
        'tuning': tuning,
        'FB_C0': _c0_fields(rc0)['FB'],
        'ALG_C0': _c0_fields(rc0)['ALG'],
        'FB_C3': _c0_fields(rc3)['FB'],
        'ALG_C3': _c0_fields(rc3)['ALG'],
    }

    op1 = {}
    op1.update(_op20_fields(r20_1)); op1.update(_op40_fields(r40_1)); op1.update(_op60_fields(r60_1)); op1.update(_op80_fields(r80_1)); op1.update(_ope0_fields(re0_1))
    op2 = {}
    op2.update(_op20_fields(r20_2)); op2.update(_op40_fields(r40_2)); op2.update(_op60_fields(r60_2)); op2.update(_op80_fields(r80_2)); op2.update(_ope0_fields(re0_2))
    op3 = {}
    op3.update(_op20_fields(r20_3)); op3.update(_op40_fields(r40_3)); op3.update(_op60_fields(r60_3)); op3.update(_op80_fields(r80_3)); op3.update(_ope0_fields(re0_3))
    op4 = {}
    op4.update(_op20_fields(r20_4)); op4.update(_op40_fields(r40_4)); op4.update(_op60_fields(r60_4)); op4.update(_op80_fields(r80_4)); op4.update(_ope0_fields(re0_4))

    for i, op in enumerate([op1, op2, op3, op4], start=1):
        for k, v in op.items():
            row[f'OP{i}_{k}'] = v

    return [row]

def _csv_from_rows(rows: list[dict], field_order: list[str] | None = None) -> str:
    if not rows:
        return ""
    if field_order is None:
        # gather stable order
        keys = set()
        for r in rows:
            keys.update(r.keys())
        # first fixed head
        head = ['voice', 'name', 'type', 'tuning']
        rest = sorted(k for k in keys if k not in head)
        field_order = head + rest

    sio = io.StringIO()
    w = csv.DictWriter(sio, fieldnames=field_order, extrasaction='ignore')
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return sio.getvalue().rstrip('\n')


def _decode_2op_instruments(data: bytes, fm_detail: bool = False, csv_output: bool = False) -> list[str]:
    if len(data) < 2:
        return ["  (too short)"]
    count = struct.unpack_from('<H', data, 0)[0]
    lines = [f"  Count: {count}"]
    pos = 2
    detail_rows = []
    for i in range(count):
        if pos + 30 > len(data):
            lines.append(f"  Instrument {i}: (truncated)")
            break
        tuning = data[pos]
        regs = data[pos + 3: pos + 14]
        name = data[pos + 14: pos + 30].rstrip(b'\x00 ').decode('ascii', errors='replace')
        reg_str = ' '.join(f'{b:02X}' for b in regs)
        lines.append(f"  Inst {i:2d}: '{name:<16}' tuning={tuning}  regs={reg_str}")

        if fm_detail:
            rows = _inst2op_detail_rows(i, name, tuning, regs)
            detail_rows.extend(rows)
            r = rows[0]
            lines.append(
                f"       CH: FB={r['FB']} ALG={r['ALG']} | "
                f"OP1(AM={r['OP1_AM']} VIB={r['OP1_VIB']} EGT={r['OP1_EGT']} KSR={r['OP1_KSR']} MUL={r['OP1_MULTI']} "
                f"KSL={r['OP1_KSL']} TL={r['OP1_TL']} AR={r['OP1_AR']} DR={r['OP1_DR']} SL={r['OP1_SL']} RR={r['OP1_RR']} WS={r['OP1_WS']}) | "
                f"OP2(AM={r['OP2_AM']} VIB={r['OP2_VIB']} EGT={r['OP2_EGT']} KSR={r['OP2_KSR']} MUL={r['OP2_MULTI']} "
                f"KSL={r['OP2_KSL']} TL={r['OP2_TL']} AR={r['OP2_AR']} DR={r['OP2_DR']} SL={r['OP2_SL']} RR={r['OP2_RR']} WS={r['OP2_WS']})"
            )
        pos += 30

    if fm_detail and csv_output and detail_rows:
        lines.append("  --- 2OP FM DETAIL CSV ---")
        order = [
            'voice','name','type','tuning','FB','ALG',
            'OP1_AM','OP1_VIB','OP1_EGT','OP1_KSR','OP1_MULTI','OP1_KSL','OP1_TL','OP1_AR','OP1_DR','OP1_SL','OP1_RR','OP1_WS',
            'OP2_AM','OP2_VIB','OP2_EGT','OP2_KSR','OP2_MULTI','OP2_KSL','OP2_TL','OP2_AR','OP2_DR','OP2_SL','OP2_RR','OP2_WS',
        ]
        csv_text = _csv_from_rows(detail_rows, order)
        for ln in csv_text.splitlines():
            lines.append("  " + ln)

    return lines


def _decode_4op_instruments(data: bytes, fm_detail: bool = False, csv_output: bool = False) -> list[str]:
    if len(data) < 2:
        return ["  (too short)"]
    count = struct.unpack_from('<H', data, 0)[0]
    lines = [f"  Count: {count}"]
    pos = 2
    detail_rows = []

    for i in range(count):
        if pos + 41 > len(data):
            lines.append(f"  Instrument {i}: (truncated)")
            break
        tuning = data[pos]
        regs = data[pos + 3: pos + 25]  # 22 bytes
        name = data[pos + 25: pos + 41].rstrip(b'\x00 ').decode('ascii', errors='replace')
        reg_str = ' '.join(f'{b:02X}' for b in regs)
        lines.append(f"  Inst {i:2d}: '{name:<16}' tuning={tuning}  regs={reg_str}")

        if fm_detail:
            rows = _inst4op_detail_rows(i, name, tuning, regs)
            detail_rows.extend(rows)
            r = rows[0]
            lines.append(
                f"       CH: C0(FB={r['FB_C0']} ALG={r['ALG_C0']}), C3(FB={r['FB_C3']} ALG={r['ALG_C3']})"
            )
        pos += 41

    if fm_detail and csv_output and detail_rows:
        lines.append("  --- 4OP FM DETAIL CSV ---")
        order = ['voice','name','type','tuning','FB_C0','ALG_C0','FB_C3','ALG_C3']
        for op in (1,2,3,4):
            order += [
                f'OP{op}_AM', f'OP{op}_VIB', f'OP{op}_EGT', f'OP{op}_KSR', f'OP{op}_MULTI',
                f'OP{op}_KSL', f'OP{op}_TL', f'OP{op}_AR', f'OP{op}_DR', f'OP{op}_SL', f'OP{op}_RR', f'OP{op}_WS'
            ]
        csv_text = _csv_from_rows(detail_rows, order)
        for ln in csv_text.splitlines():
            lines.append("  " + ln)

    return lines


def _decode_patterns(data: bytes, n_channels: int, show_bytes: bool = True) -> list[str]:
    if len(data) < 2:
        return ["  (too short)"]
    count = struct.unpack_from('<H', data, 0)[0]
    lines = [f"  Count: {count} patterns, {n_channels} channels each"]
    pos = 2

    for pat in range(count):
        lines.append(f"  --- Pattern {pat} ---")
        for ch in range(n_channels):
            if pos >= len(data):
                lines.append(f"    CH{ch}: (data exhausted at offset {pos})")
                break
            ch_bytes = []
            cum_time = 0
            start_pos = pos
            events_decoded = []
            while pos < len(data) and cum_time < 16:
                b = data[pos]
                pos += 1
                if b & 0x80:
                    # Time length code
                    length = b & 0x7F
                    cum_time += length
                    events_decoded.append(f"[t{length}]")
                else:
                    # Event byte
                    if b == 0x00 or b == 0x02:      # note on / legato
                        name = 'NOTE_ON' if b == 0x00 else 'LEGATO'
                        if pos < len(data):
                            note = data[pos]
                            pos += 1
                            events_decoded.append(f"{name}({note})")
                        else:
                            events_decoded.append(f"{name}(?)")
                    elif b == 0x01:
                        events_decoded.append("NOTE_OFF")
                    elif b == 0x04:
                        if pos < len(data):
                            inst = data[pos]
                            pos += 1
                            events_decoded.append(f"INST({inst})")
                        else:
                            events_decoded.append("INST(?)")
                    elif b == 0x05:
                        if pos < len(data):
                            vol = data[pos]
                            pos += 1
                            events_decoded.append(f"VOL({vol})")
                        else:
                            events_decoded.append("VOL(?)")
                    elif b == 0x06:
                        if pos < len(data):
                            stereo = data[pos]
                            pos += 1
                            events_decoded.append(f"STEREO({stereo})")
                        else:
                            events_decoded.append("STEREO(?)")
                    elif b == 0x09:
                        if pos + 1 < len(data):
                            tempo = struct.unpack_from('<H', data, pos)[0]
                            pos += 2
                            events_decoded.append(f"TMP({tempo / 256:.2f})")
                        else:
                            events_decoded.append("TMP(?)")
                    else:
                        events_decoded.append(f"EVT(0x{b:02X})")
                ch_bytes.append(b)
            raw_hex = data[start_pos:pos].hex()
            lines.append(f"    CH{ch:2d}: cum={cum_time:2d}  {' '.join(events_decoded)}")
            if show_bytes:
                lines.append(f"         raw: {raw_hex}")
    return lines


# ---------------------------------------------------------------------------
# Main dump function
# ---------------------------------------------------------------------------

def dump_ms2(path: str,
             show_pattern_bytes: bool = True,
             max_patterns: int = 3,
             fm_detail: bool = False,
             fm_csv: bool = False) -> None:
    """Parse and print the structure of an MS2 file."""
    with open(path, 'rb') as f:
        data = f.read()

    print(f"File: {path}  ({len(data)} bytes)")
    print(f"Signature: {data[:8].hex()}  ({'OK' if data[:8] == MS2_SIGNATURE else 'BAD'})")
    print()

    n_channels = 0
    n_patterns_total = 0

    for offset, ctype, version, cdata in iter_chunks(data):
        label = CHUNK_TYPES.get(ctype, f'Unknown({ctype})')
        print(f"[{offset:#06x}] Chunk type {ctype} ({label})  version={version}  size={len(cdata)}")

        if ctype == 1:
            for line in _decode_song_settings(cdata):
                print(line)
            if len(cdata) >= 291:
                n_channels      = cdata[71]
                n_patterns_total = struct.unpack_from('<H', cdata, 263)[0]
        elif ctype == 2:
            for line in _decode_tunings(cdata):
                print(line)
        elif ctype == 3:
            for line in _decode_2op_instruments(cdata, fm_detail=fm_detail, csv_output=fm_csv):
                print(line)
        elif ctype == 4:
            for line in _decode_4op_instruments(cdata, fm_detail=fm_detail, csv_output=fm_csv):
                print(line)
        elif ctype == 6 and n_channels > 0:
            # NOTE: existing behavior kept (currently not trimming by max_patterns)
            trimmed = cdata
            for line in _decode_patterns(trimmed, n_channels,
                                         show_bytes=show_pattern_bytes):
                print(line)
        elif ctype == 0:
            print("  (end of file)")
        print()


# ---------------------------------------------------------------------------
# Comparison: check signature + chunk header validity
# ---------------------------------------------------------------------------

def compare_signatures(file1: str, file2: str) -> bool:
    """Return True if both files have valid MS2 signatures and parseable chunks."""
    ok = True
    for path in (file1, file2):
        with open(path, 'rb') as f:
            data = f.read()
        if data[:8] != MS2_SIGNATURE:
            print(f"FAIL: {path} – bad signature ({data[:8].hex()})")
            ok = False
            continue
        try:
            chunks = list(iter_chunks(data))
            print(f"OK:   {path} – {len(chunks)} chunks, types: "
                  f"{[c[1] for c in chunks]}")
        except Exception as exc:
            print(f"FAIL: {path} – parse error: {exc}")
            ok = False
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Dump MS2 file structure')
    parser.add_argument('file', help='MS2 file to dump')
    parser.add_argument('--no-bytes', action='store_true',
                        help='Hide raw pattern bytes in pattern dump')
    parser.add_argument('--compare', metavar='OTHER',
                        help='Compare signature+structure with another MS2 file')
    parser.add_argument('--max-patterns', type=int, default=3,
                        help='Maximum patterns to dump (default: 3)')
    parser.add_argument('--fm-detail', action='store_true',
                        help='Decode FM(2OP/4OP) register bytes into per-parameter fields')
    parser.add_argument('--fm-csv', action='store_true',
                        help='With --fm-detail, also print CSV tables (voice x parameters)')
    args = parser.parse_args()

    if args.compare:
        compare_signatures(args.file, args.compare)
    else:
        dump_ms2(args.file,
                 show_pattern_bytes=not args.no_bytes,
                 max_patterns=args.max_patterns,
                 fm_detail=args.fm_detail,
                 fm_csv=args.fm_csv)


if __name__ == '__main__':
    main()
