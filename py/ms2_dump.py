"""
ms2_dump.py  –  Debug utility: dump MS2 file chunks and pattern data.

Usage:
    python py/ms2_dump.py <file.MS2> [--patterns] [--compare <other.MS2>]
"""

import struct
import sys
import os

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


def _decode_2op_instruments(data: bytes) -> list[str]:
    if len(data) < 2:
        return ["  (too short)"]
    count = struct.unpack_from('<H', data, 0)[0]
    lines = [f"  Count: {count}"]
    pos = 2
    for i in range(count):
        if pos + 30 > len(data):
            lines.append(f"  Instrument {i}: (truncated)")
            break
        tuning = data[pos]
        regs = data[pos + 3: pos + 14]
        name = data[pos + 14: pos + 30].rstrip(b'\x00 ').decode('ascii', errors='replace')
        reg_str = ' '.join(f'{b:02X}' for b in regs)
        lines.append(f"  Inst {i:2d}: '{name:<16}' tuning={tuning}  regs={reg_str}")
        pos += 30
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

def dump_ms2(path: str, show_pattern_bytes: bool = True, max_patterns: int = 3) -> None:
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
            for line in _decode_2op_instruments(cdata):
                print(line)
        elif ctype == 6 and n_channels > 0:
            # Only dump up to max_patterns
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
    args = parser.parse_args()

    if args.compare:
        compare_signatures(args.file, args.compare)
    else:
        dump_ms2(args.file,
                 show_pattern_bytes=not args.no_bytes,
                 max_patterns=args.max_patterns)


if __name__ == '__main__':
    main()
