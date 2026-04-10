"""
ms2_validate.py  –  Structural validator for the MS2 Patterns chunk (type 6).

Parses each channel-pattern stream and checks:
  1. Every event has the correct number of argument bytes (no truncation).
  2. No more than 5 event items appear between two consecutive time-length codes.
  3. Every time-length code has a value in 1..16 (0x81..0x90).
  4. The cumulative time sum for each channel-pattern equals exactly 16.
  5. No channel-pattern reads past the end of the chunk data.

Usage as library:
    from ms2_validate import validate_ms2_bytes
    errors = validate_ms2_bytes(ms2_bytes, n_channels)
    if errors:
        for e in errors:
            print(e)

Usage as CLI:
    python py/ms2_validate.py <file.MS2>
"""

import struct
import sys
import os

MS2_SIGNATURE = bytes([0x4D, 0x53, 0xC2, 0xB2, 0x0D, 0x0A, 0x00, 0x1A])

# Number of argument bytes for each event opcode (not counting the opcode byte).
# Unknown / unsupported opcodes are treated as 0-argument and flagged as warnings.
_EVENT_ARG_LEN: dict[int, int] = {
    0x00: 1,   # Note on  (note byte)
    0x01: 0,   # Note off (no arg)
    0x02: 1,   # Legato note (note byte)
    0x03: 1,   # MoonBlaster CMD (event byte E)
    0x04: 1,   # Instrument change (index byte)
    0x05: 1,   # Volume change (volume byte)
    0x06: 1,   # Stereo change (signed 4-bit byte)
    0x07: 1,   # Pitch envelope (index byte)
    0x08: 1,   # Reserved / extension
    0x09: 2,   # OPL Timer (16-bit word)
    0x0A: 1,   # MoonBlaster event (byte)
    0x0B: 1,   # Reserved
    0x0C: 1,   # Tuning change (byte)
    0x18: 0,   # End of pattern
}

STEPS_PER_PATTERN = 16
MAX_EVENTS_PER_SLICE = 5


# ---------------------------------------------------------------------------
# Core: decode one channel-pattern stream from raw bytes
# ---------------------------------------------------------------------------

def _validate_channel_stream(
    data: bytes,
    pos: int,
    end: int,
    pat: int,
    ch: int,
) -> tuple[int, list[str]]:
    """Parse and validate one channel's pattern data starting at *pos*.

    Returns (new_pos, errors).  new_pos is the byte position immediately
    after the last byte consumed.  errors is a list of human-readable strings.
    """
    errors: list[str] = []
    cum_time = 0
    events_in_slice = 0
    start_pos = pos
    label = f"[pat={pat} ch={ch} @{pos:#x}]"

    while pos < end and cum_time < STEPS_PER_PATTERN:
        b = data[pos]
        pos += 1

        if b & 0x80:
            # Time-length code
            length = b & 0x7F
            if length < 1 or length > 16:
                errors.append(
                    f"{label} bad time-length code 0x{b:02X} "
                    f"(length={length}, valid range 1..16) at {pos-1:#x}"
                )
            cum_time += length
            events_in_slice = 0   # reset counter for next slice
        else:
            # Event byte
            events_in_slice += 1
            if events_in_slice > MAX_EVENTS_PER_SLICE:
                errors.append(
                    f"{label} more than {MAX_EVENTS_PER_SLICE} events "
                    f"before time-length code at {pos-1:#x} "
                    f"(slice event count={events_in_slice})"
                )

            n_args = _EVENT_ARG_LEN.get(b, None)
            if n_args is None:
                errors.append(
                    f"{label} unknown event opcode 0x{b:02X} at {pos-1:#x}; "
                    f"assuming 0 argument bytes"
                )
                n_args = 0

            # Consume argument bytes
            for _ in range(n_args):
                if pos >= end:
                    errors.append(
                        f"{label} unexpected end of data reading "
                        f"arg for event 0x{b:02X} at {pos:#x}"
                    )
                    break
                arg_byte = data[pos]
                pos += 1
                # Sanity check: argument bytes must NOT have bit 7 set
                # (they could be confused with time-length codes).
                # Exception: the signed stereo byte (0x06) may be negative.
                if b != 0x06 and (arg_byte & 0x80):
                    errors.append(
                        f"{label} event 0x{b:02X} argument byte 0x{arg_byte:02X} "
                        f"has bit 7 set at {pos-1:#x}; "
                        f"may indicate stream misalignment"
                    )

    # After loop: cumulative time must equal exactly STEPS_PER_PATTERN
    if cum_time != STEPS_PER_PATTERN:
        errors.append(
            f"{label} cumulative time = {cum_time} "
            f"(expected {STEPS_PER_PATTERN}); "
            f"consumed bytes {start_pos:#x}..{pos-1:#x}"
        )

    return pos, errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_patterns_chunk(
    chunk_data: bytes,
    n_channels: int,
) -> list[str]:
    """Validate the raw Patterns chunk data (chunk type 6).

    chunk_data must be the chunk payload (after the 4-byte chunk header).
    Returns a list of error strings; empty list means the chunk is valid.
    """
    if len(chunk_data) < 2:
        return ["Patterns chunk too short (< 2 bytes)"]

    n_patterns = struct.unpack_from('<H', chunk_data, 0)[0]
    errors: list[str] = []
    pos = 2
    end = len(chunk_data)

    for pat in range(n_patterns):
        for ch in range(n_channels):
            if pos >= end:
                errors.append(
                    f"[pat={pat} ch={ch}] data exhausted before reading "
                    f"channel-pattern stream"
                )
                return errors  # Can't continue; positions are hopelessly off
            pos, ch_errors = _validate_channel_stream(
                chunk_data, pos, end, pat, ch
            )
            errors.extend(ch_errors)

    return errors


def validate_ms2_bytes(ms2_bytes: bytes) -> list[str]:
    """Validate the Patterns chunk of a complete MS2 file.

    Returns a list of error strings.  An empty list means no issues were
    detected.  The function automatically discovers n_channels from the
    Song Settings chunk (type 1) in the file.
    """
    from ms2_dump import iter_chunks   # local import to avoid circular deps

    errors: list[str] = []

    if len(ms2_bytes) < 8:
        return ["File too short to contain MS2 signature"]
    if ms2_bytes[:8] != MS2_SIGNATURE:
        return [f"Invalid MS2 signature: {ms2_bytes[:8].hex()}"]

    # Extract n_channels from Song Settings chunk (type 1)
    n_channels = 0
    patterns_chunk_data: bytes | None = None

    try:
        for _offset, ctype, _version, cdata in iter_chunks(ms2_bytes):
            if ctype == 1 and len(cdata) >= 72:
                n_channels = cdata[71]
            elif ctype == 6:
                patterns_chunk_data = cdata
            elif ctype == 0:
                break
    except Exception as exc:
        return [f"Failed to parse MS2 chunks: {exc}"]

    if n_channels == 0:
        return ["Could not determine channel count from Song Settings chunk"]
    if patterns_chunk_data is None:
        return ["No Patterns chunk (type 6) found in file"]

    return validate_patterns_chunk(patterns_chunk_data, n_channels)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description='Validate MS2 Patterns chunk structural correctness'
    )
    parser.add_argument('file', help='MS2 file to validate')
    args = parser.parse_args()

    with open(args.file, 'rb') as f:
        ms2_bytes = f.read()

    sys.path.insert(0, os.path.dirname(__file__))
    errors = validate_ms2_bytes(ms2_bytes)

    if not errors:
        print(f"OK: {args.file} – Patterns chunk is structurally valid.")
        return 0
    else:
        print(f"ERRORS in {args.file} ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
