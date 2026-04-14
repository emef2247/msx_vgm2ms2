"""
ms2_writer.py  –  MS2 (MoonSound Music Studio) binary file builder.

Supports OPLL (YM2413) → OPL4 2OP FM export only.

Stage B:  audibly similar melody via 16-step quantisation.
Stage C:  TODOs marked throughout for higher-fidelity follow-up.

MS2 file format specification: see Appendix E in ms2.html (repo root).
"""

import struct
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from opll_mml import _build_segments, _opll_note, NUM_CH

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MS2_SIGNATURE = bytes([0x4D, 0x53, 0xC2, 0xB2, 0x0D, 0x0A, 0x00, 0x1A])

# Path to the reference YM2413.MS2 file containing the MoonBlaster v1.4 instrument library.
_REFERENCE_YM2413_MS2 = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'ms2_reference', 'OP2', 'YM2413.MS2',
)

# YM2413 ROM preset patches (R0..R7), indexed 1..15.
# Source: well-known YM2413 documentation / Nuked-OPL reference.
OPLL_ROM_PATCHES: dict[int, bytes] = {
    1:  bytes([0x71, 0x61, 0x1e, 0x07, 0xd3, 0x01, 0x99, 0x00]),  # Violin
    2:  bytes([0x13, 0x41, 0x1a, 0x06, 0xd3, 0x83, 0x40, 0x00]),  # Guitar
    3:  bytes([0x11, 0x61, 0x0e, 0x08, 0xf5, 0x70, 0x00, 0x00]),  # Piano
    4:  bytes([0x32, 0x21, 0x1e, 0x06, 0xe1, 0x76, 0x01, 0x00]),  # Flute
    5:  bytes([0x21, 0x22, 0x16, 0x05, 0xf0, 0x71, 0x00, 0x00]),  # Clarinet
    6:  bytes([0x31, 0x23, 0x16, 0x06, 0xf0, 0x00, 0x00, 0x00]),  # Oboe
    7:  bytes([0x21, 0x61, 0x19, 0x07, 0xf6, 0x65, 0x00, 0x00]),  # Trumpet
    8:  bytes([0x22, 0x21, 0x14, 0x08, 0xff, 0xf8, 0x00, 0x00]),  # Organ
    9:  bytes([0x31, 0x22, 0x11, 0x06, 0xf0, 0x00, 0x00, 0x00]),  # Horn
    10: bytes([0x32, 0x21, 0x1e, 0x08, 0xe0, 0x00, 0x00, 0x00]),  # Synthesizer
    11: bytes([0x01, 0x61, 0x0f, 0x06, 0xe6, 0x65, 0x00, 0x00]),  # Harpsichord
    12: bytes([0x32, 0x21, 0x15, 0x00, 0xf6, 0xaa, 0x00, 0x00]),  # Vibraphone
    13: bytes([0x01, 0x02, 0x1d, 0x07, 0xf3, 0xf5, 0x00, 0x00]),  # Synth Bass
    14: bytes([0x21, 0x22, 0x08, 0x00, 0xf6, 0xf3, 0x00, 0x00]),  # Acoustic Bass
    15: bytes([0x21, 0x62, 0x1b, 0x07, 0xf5, 0xf6, 0x00, 0x00]),  # Electric Guitar
}

OPLL_PRESET_NAMES: dict[int, str] = {
    0:  'User Defined',
    1:  'Violin',
    2:  'Guitar',
    3:  'Piano',
    4:  'Flute',
    5:  'Clarinet',
    6:  'Oboe',
    7:  'Trumpet',
    8:  'Organ',
    9:  'Horn',
    10: 'Synthesizer',
    11: 'Harpsichord',
    12: 'Vibraphone',
    13: 'Synth Bass',
    14: 'Acoustic Bass',
    15: 'Electric Guitar',
}

# Timer interrupt frequencies used for tempo and quantization calculations.
# SongSettings+67: 0 = VDP timer (NTSC/PAL), 1 = 50 Hz fixed.
# SongSettings+68: timer divider (16-bit 8.8 fixed-point), = freq_hz * 3840 / bpm.
_FREQ_NTSC: float = 59.988527908187   # NTSC VDP interrupt frequency (Hz)
_FREQ_PAL:  float = 50.214039335288   # PAL VDP / 50 Hz fixed interrupt frequency (Hz)

# Deprecated lookup table (60 Hz NTSC approximation, kept for reference).
# Replaced by the freq-based formula: round(freq_hz * 3840 / bpm).
_BPM_TO_DIVIDER: dict[int, int] = {
    60:  0x0F00,   # 15.0
    75:  0x0C00,   # 12.0
    100: 0x0900,   # 9.0
    120: 0x0780,   # 7.5
    150: 0x0600,   # 6.0
}

STEPS_PER_PATTERN = 16      # MS2 whole-note = 16 steps (16th-note granularity)
TICKS_PER_STEP_75BPM = 12   # at 60 fps, 75 BPM: 1/16 note = 12 ticks


# ---------------------------------------------------------------------------
# YM2413 → OPL4 register mapping
# ---------------------------------------------------------------------------

def opll_patch_to_opl4(patch_bytes: bytes) -> dict:
    """Convert 8-byte YM2413 user-patch (R0..R7) to OPL4 2OP FM register values.

    YM2413 register layout (same as Appendix in ms2.html spec context):
        R0: AM(7) PM(6) EG(5) KR(4) MULT(3:0)   – Modulator
        R1: KL(7:6) TL(5:0)                      – Modulator
        R2: AR(7:4) DR(3:0)                       – Modulator
        R3: SL(7:4) RR(3:0)                       – Modulator
        R4: AM(7) PM(6) EG(5) KR(4) MULT(3:0)   – Carrier
        R5: KL(7:6) DC(4) DM(3) FB(2:0)         – Carrier (DC/DM = waveforms)
        R6: AR(7:4) DR(3:0)                       – Carrier
        R7: SL(7:4) RR(3:0)                       – Carrier

    OPL4 2OP instrument registers (MS2 chunk type 3 spec):
        20H: mod {AM VIB EGT KSR MULT}  – same bit layout as YM2413 R0
        21H: car {AM VIB EGT KSR MULT}  – same as R4
        40H: mod {KSL TL}               – same as R1
        41H: car {KSL TL}               – R5[7:6] for KSL, TL=0 (carrier not attenuated)
        60H: mod {AR DR}                 – same as R2
        61H: car {AR DR}                 – same as R6
        80H: mod {SL RR}                 – same as R3
        81H: car {SL RR}                 – same as R7
        E0H: mod waveform                – DM bit (R5 bit 3)
        E1H: car waveform                – DC bit (R5 bit 4)
        C0H: {FB CNT}                    – R5[2:0] as FB, CNT=0 (FM)

    Returns a dict: {20: byte, 21: byte, 40: byte, 41: byte, 60: byte, 61: byte,
                     80: byte, 81: byte, E0: byte, E1: byte, C0: byte}
    """
    r = (bytes(patch_bytes)[:8] + bytes(max(0, 8 - len(patch_bytes))))[:8]

    reg20 = r[0]                                # mod AM/VIB/EGT/KSR/MULT
    reg21 = r[4]                                # car AM/VIB/EGT/KSR/MULT
    reg40 = r[1] & 0xFF                         # mod KSL/TL (all 8 bits)
    reg41 = r[5] & 0xC0                         # car KSL only (TL=0)
    reg60 = r[2]                                # mod AR/DR
    reg61 = r[6]                                # car AR/DR
    reg80 = r[3]                                # mod SL/RR
    reg81 = r[7]                                # car SL/RR
    reg_e0 = (r[5] >> 3) & 0x01                # mod waveform: DM bit
    reg_e1 = (r[5] >> 4) & 0x01                # car waveform: DC bit
    reg_c0 = (r[5] & 0x07) << 1                # FB in bits 3:1, CNT=0

    return {
        0x20: reg20, 0x21: reg21,
        0x40: reg40, 0x41: reg41,
        0x60: reg60, 0x61: reg61,
        0x80: reg80, 0x81: reg81,
        0xE0: reg_e0, 0xE1: reg_e1,
        0xC0: reg_c0,
    }


def opll_patch_raw(patch_bytes: bytes) -> dict:
    """Pack 8-byte YM2413 patch (R0..R7) directly into OPL4 2OP register fields.

    This is the "raw" mode packing: each OPLL source byte is placed into the
    corresponding OPL4 register without applying any additional conversion
    tables or OPLL→OPL multiplier scaling.  No ``opll_load_voice()``-style
    conversion is performed.

    Mapping (direct field assignment):
        reg20 = R0            (mod AM/VIB/EGT/KSR/MULT – identical layout)
        reg21 = R4            (car AM/VIB/EGT/KSR/MULT – identical layout)
        reg40 = R1            (mod KSL/TL – all 8 bits)
        reg41 = R5 & 0xC0    (car KSL bits [7:6]; carrier TL=0, no TL field in OPLL)
        reg60 = R2            (mod AR/DR – identical layout)
        reg61 = R6            (car AR/DR – identical layout)
        reg80 = R3            (mod SL/RR – identical layout)
        reg81 = R7            (car SL/RR – identical layout)
        regE0 = (R5 >> 3) & 1  (mod waveform: DM bit)
        regE1 = (R5 >> 4) & 1  (car waveform: DC bit)
        regC0 = (R5 & 7) << 1  (feedback FB in bits [3:1], CNT=0)

    Returns a dict: {0x20: byte, 0x21: byte, 0x40: byte, 0x41: byte,
                     0x60: byte, 0x61: byte, 0x80: byte, 0x81: byte,
                     0xE0: byte, 0xE1: byte, 0xC0: byte}
    """
    r = (bytes(patch_bytes)[:8] + b'\x00' * max(0, 8 - len(patch_bytes)))[:8]

    return {
        0x20: r[0],                  # mod AM/VIB/EGT/KSR/MULT – direct
        0x21: r[4],                  # car AM/VIB/EGT/KSR/MULT – direct
        0x40: r[1],                  # mod KSL/TL – all 8 bits, direct
        0x41: r[5] & 0xC0,           # car KSL [7:6]; TL=0 (no carrier TL in OPLL)
        0x60: r[2],                  # mod AR/DR – direct
        0x61: r[6],                  # car AR/DR – direct
        0x80: r[3],                  # mod SL/RR – direct
        0x81: r[7],                  # car SL/RR – direct
        0xE0: (r[5] >> 3) & 0x01,   # mod waveform: DM bit
        0xE1: (r[5] >> 4) & 0x01,   # car waveform: DC bit
        0xC0: (r[5] & 0x07) << 1,   # feedback FB, CNT=0
    }


# ---------------------------------------------------------------------------
# Pitch conversion: OPLL Fnum + Block → MS2 note index (0-based, C1=0)
# ---------------------------------------------------------------------------

def fnum_block_to_ms2_note(fnum: int, block: int) -> int | None:
    """Convert YM2413 Fnum+Block to an MS2 note index (0=C0, 1=C#0, …, 95=B7).

    MS2 note 0 = C0 = MIDI 12.  The valid range is 0..95 (C0 to B7).

    MGSDRV (MSX) octave convention: "o1" corresponds to OPLL block 0 (≈C0 =
    16 Hz), so octave N in MGSDRV maps to OPLL block N-1.  Subtracting MIDI 12
    (rather than MIDI 24) gives the correct MS2 note for all test cases:
        block=0 fnum=172 → 16 Hz ≈ C0  → ms2 note 0  ✓ (blockb "o1 c")
        block=2 fnum=172 → 65 Hz ≈ C2  → ms2 note 24 ✓ (scalech "o3 c")
        block=3 fnum=172 → 130 Hz ≈ C3 → ms2 note 36 ✓ (3chtest "o4 c")

    Returns None when fnum==0 or frequency maps outside 0..95.
    """
    if fnum == 0:
        return None
    freq = 49716.0 * fnum * (1 << block) / (1 << 19)
    if freq < 8.0:
        return None
    # MIDI note: A4=440 Hz=MIDI 69
    midi = 69.0 + 12.0 * math.log2(freq / 440.0)
    midi_int = round(midi)
    # MS2 note 0 = C0 = MIDI 12
    ms2_note = midi_int - 12
    if ms2_note < 0 or ms2_note > 95:
        return None
    return ms2_note


def opll_vol_to_ms2_vol(opll_vol: int) -> int:
    """Convert OPLL volume (0=max, 15=silent) to MS2 carrier TL (0=max, 63=min).

    OPLL volume is a 4-bit attenuation field: 0=loudest, 15=most attenuated.
    Each OPLL step = 2 dB.  MS2 volume event directly sets the OPL4 carrier
    Total Level (TL), where 0=maximum output and 63=minimum output.
    Each OPL4 TL step ≈ 0.75 dB.

    Empirical calibration from reference MS2 files (ms2_reference/):
        OPLL vol 1 → MS2 TL 4   (confirmed from SCALECH.MS2 / 3CHTEST.MS2)
        OPLL vol 2 → MS2 TL 8   (confirmed from BLOCKB.MS2)
    This gives a ratio of 4 TL steps per OPLL vol step, which approximates the
    2 dB / 0.75 dB ≈ 2.67 exact ratio while matching the reference hand-authored
    values.  A factor of 4 per step (2 × nearest even integer) is used.
    """
    v = max(0, min(15, opll_vol))
    # OPLL: 2 dB/step, OPL4 TL: ~0.75 dB/step => 2 / 0.75 = 2.666...
    return min(63, int(round(v * (2.0 / 0.75))))


# ---------------------------------------------------------------------------
# Chunk builder helpers
# ---------------------------------------------------------------------------

def _build_chunk(chunk_type: int, data: bytes) -> bytes:
    """Build an MS2 chunk: 4-byte header + data.

    Chunk header:
        +0  byte  Chunk type
        +1  byte  Version (0)
        +2  word  Chunk data size (≤ 16384; -1 = extended, not used here)
    """
    if len(data) > 16384:
        raise ValueError(f"Chunk data too large: {len(data)} bytes (max 16384)")
    return struct.pack('<BBH', chunk_type, 0, len(data)) + data


def _sentinel_chunk() -> bytes:
    return struct.pack('<BBH', 0, 0, 0)


# ---------------------------------------------------------------------------
# Chunk type 1: Song Settings (291 bytes)
# ---------------------------------------------------------------------------

def build_song_settings_data(
    description: str,
    n_patterns: int,
    n_tunings: int,
    n_2op_inst: int,
    n_4op_inst: int = 1,
    n_wav_inst: int = 1,
    n_pitch_env: int = 1,
    n_channels: int = NUM_CH,
    timer: int = 0,
    timer_divider: int = 0x0C00,
    modulation_depth: int = 0xC0,
    starting_instruments: list | None = None,
) -> bytes:
    """Build the 291-byte Song Settings chunk data (chunk type 1).

    Args:
        description:        Up to 65 ASCII chars (padded with spaces).
        n_patterns:         Total number of patterns (song length).
        n_tunings:          Number of tuning entries (≥ 1).
        n_2op_inst:         Number of 2OP instruments (≥ 1).
        n_4op_inst:         Number of 4OP instruments (≥ 1).
        n_wav_inst:         Number of WAV instruments (≥ 1).
        n_pitch_env:        Number of pitch envelopes (≥ 1).
        n_channels:         Number of FM channels (all 2OP).
        timer:              0 = VDP timer, 1 = 50 Hz timer.
        timer_divider:      8.8 fixed-point timer divider (controls tempo).
        modulation_depth:   OPL4 modulation depth flags.
        starting_instruments: List of 0-based instrument indices per channel.
    """
    data = bytearray(291)

    # +0: song description (65 bytes, ASCII, space-padded)
    desc_b = description.encode('ascii', errors='replace')
    desc_b = (desc_b + b' ' * 65)[:65]
    data[0:65] = desc_b

    # +65: song length (MS² expects last pattern index, i.e. n_patterns-1)
    data[65] = max(0, min(255, n_patterns - 1))
    # +66: loop pattern (0 = loop back to pattern 0)
    data[66] = 0   # loop to pattern 0
    # +67: timer type (0 = VDP, 1 = 50 Hz)
    data[67] = timer
    # +68: timer divider (16-bit, 8.8 fixed-point)
    struct.pack_into('<H', data, 68, timer_divider)
    # +70: modulation depth
    data[70] = modulation_depth
    # +71: total channels
    data[71] = min(42, n_channels)
    # +72: 2OP FM channels
    data[72] = min(42, n_channels)
    # +73: 4OP FM channels  (0 for OPLL-only export)
    data[73] = 0
    # +74: WAV channels     (0 for OPLL-only export)
    data[74] = 0

    # +75: channel type array (42 bytes) – 1 = 2OP FM
    for i in range(min(42, n_channels)):
        data[75 + i] = 1   # 2OP FM enabled (bit 7 = 0 means enabled)

    # +117: starting instrument index per channel (42 bytes, 0-based)
    if starting_instruments:
        for i, idx in enumerate(starting_instruments[:min(42, n_channels)]):
            data[117 + i] = idx & 0xFF

    # +159: stereo panning per channel (42 bytes, signed 4-bit) – 0 = centre
    # already 0 by default.

    # +201: WAV channel start volume (42 bytes) – 0 for non-WAV
    # already 0.

    # +243 .. +272: counts (each: 2-byte word + 3 zero bytes = 5 bytes)
    def _write_count(offset: int, value: int):
        struct.pack_into('<H', data, offset, max(1, value))
        # 3 zero bytes at offset+2 are already 0

    _write_count(243, n_tunings)      # tunings
    _write_count(248, n_2op_inst)     # 2OP instruments
    _write_count(253, n_4op_inst)     # 4OP instruments  (min 1)
    _write_count(258, n_wav_inst)     # WAV instruments  (min 1)
    _write_count(263, n_patterns)     # patterns
    _write_count(268, n_pitch_env)    # pitch envelopes  (min 1)

    # +273: LFO settings (18 bytes, 9×2) – 0 = defaults
    # already 0.

    return bytes(data)


# ---------------------------------------------------------------------------
# Reference instrument library (MoonBlaster v1.4 via YM2413.MS2)
# ---------------------------------------------------------------------------

def _read_ms2_chunk_data(data: bytes, target_type: int) -> bytes | None:
    """Extract raw chunk data for a given chunk type from an MS2 binary.

    Skips the 8-byte file signature and walks chunks until the target type is
    found.  Returns the chunk payload bytes (not including the 4-byte header),
    or None if not found.
    """
    pos = 8  # skip 8-byte MS2 file signature
    while pos + 4 <= len(data):
        chunk_type, _version, chunk_size = struct.unpack_from('<BBH', data, pos)
        if chunk_type == 0:
            break
        if chunk_type == target_type:
            return data[pos + 4: pos + 4 + chunk_size]
        pos += 4 + chunk_size
    return None


def _load_reference_library() -> tuple[bytes, bytes, int, int]:
    """Load tunings and 2OP instrument chunk data from the reference YM2413.MS2.

    Reads chunk type 2 (Tunings) and chunk type 3 (2OP Instruments) verbatim
    from ms2_reference/OP2/YM2413.MS2 (MoonBlaster v1.4 instrument library).
    These are copied unchanged into every generated MS2 file so that instrument
    indices align with the reference expectations.

    Returns:
        (tunings_data, instruments_data, n_tunings, n_2op_inst)

    Falls back to a minimal equal-temperament library if the reference file
    cannot be read.
    """
    try:
        with open(_REFERENCE_YM2413_MS2, 'rb') as f:
            ref_bytes = f.read()
        tunings_data = _read_ms2_chunk_data(ref_bytes, 2)
        instruments_data = _read_ms2_chunk_data(ref_bytes, 3)
        if tunings_data and instruments_data and len(tunings_data) >= 2 and len(instruments_data) >= 2:
            n_tunings = struct.unpack_from('<H', tunings_data, 0)[0]
            n_2op_inst = struct.unpack_from('<H', instruments_data, 0)[0]
            return tunings_data, instruments_data, n_tunings, n_2op_inst
    except (OSError, struct.error):
        pass
    # Fallback: minimal single equal-temperament tuning, single Organ preset
    tunings_data = build_tunings_chunk_data(1)
    instruments_data = build_2op_instruments_chunk_data(
        [(OPLL_ROM_PATCHES[8], 'Organ')]
    )
    return tunings_data, instruments_data, 1, 1


def _load_library_from_ms2(
    base_ms2_path: str,
    max_presets: int = 16,
) -> tuple[bytes, bytes, int, int]:
    """Load tunings and 2OP instrument chunk data from a user-specified MS2 file.

    Like ``_load_reference_library()`` but reads from *base_ms2_path* and
    silently truncates the instruments chunk to the first *max_presets* entries
    (default 16).  If the file has fewer than *max_presets* instruments a
    warning is printed to stderr but the function succeeds with however many
    are available.

    Returns:
        (tunings_data, instruments_data, n_tunings, n_2op_inst)
    where *n_2op_inst* ≤ *max_presets* and *instruments_data* contains only
    those entries.
    """
    import warnings as _warnings
    try:
        with open(base_ms2_path, 'rb') as f:
            ref_bytes = f.read()
        tunings_data = _read_ms2_chunk_data(ref_bytes, 2)
        instruments_data = _read_ms2_chunk_data(ref_bytes, 3)
        if not (tunings_data and instruments_data
                and len(tunings_data) >= 2 and len(instruments_data) >= 2):
            raise ValueError("Missing tunings or instruments chunk")
        n_tunings = struct.unpack_from('<H', tunings_data, 0)[0]
        n_2op_inst = struct.unpack_from('<H', instruments_data, 0)[0]
        if n_2op_inst < max_presets:
            _warnings.warn(
                f"--base-ms2 '{base_ms2_path}' contains only {n_2op_inst} "
                f"instruments (expected >= {max_presets}); "
                f"OPLL presets beyond {n_2op_inst} will fall back to index 0.",
                stacklevel=3,
            )
            # Keep as-is (fewer instruments than max_presets is acceptable).
        elif n_2op_inst > max_presets:
            # Truncate to the first max_presets instruments.
            # instruments_data layout: 2-byte count + n_2op_inst × 30 bytes
            _INST_RECORD_SIZE = 30
            truncated = (
                struct.pack('<H', max_presets)
                + instruments_data[2: 2 + max_presets * _INST_RECORD_SIZE]
            )
            instruments_data = truncated
            n_2op_inst = max_presets
        return tunings_data, instruments_data, n_tunings, n_2op_inst
    except (OSError, struct.error, ValueError) as exc:
        import warnings as _warnings
        _warnings.warn(
            f"Could not load base MS2 from '{base_ms2_path}': {exc}. "
            "Falling back to built-in reference library.",
            stacklevel=3,
        )
        return _load_reference_library()


def _opll_inst_to_ms2_idx(inst: int, n_instruments: int) -> int:
    """Map an OPLL preset number (1–15) to a 0-based MS2 instrument index.

    The reference instrument library (from YM2413.MS2) stores the 15 OPLL
    preset voices at indices 0–14, ordered as:
        index 0 → OPLL preset 1 (Violin)
        index 1 → OPLL preset 2 (Guitar)
        …
        index 14 → OPLL preset 15 (Electric Guitar)

    User patches (inst == 0) fall back to index 0.

    Args:
        inst:         OPLL preset number (0 = user patch, 1–15 = ROM preset).
        n_instruments: Total instruments in the loaded library (clamps the index).
    """
    if inst == 0:
        return 0
    return max(0, min(n_instruments - 1, inst - 1))


def _parse_user_v_num(at_token: str) -> int | None:
    """Parse the 1-based user-patch number from an at_token of the form '@vN'.

    Returns the integer N, or None if the token does not match the expected
    format (e.g. it is a preset token like '@5' rather than '@v5').
    """
    m = re.match(r'^@v(\d+)$', at_token or '')
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Chunk type 2: Tunings (equal temperament)
# ---------------------------------------------------------------------------

def _equal_temperament_tuning() -> bytes:
    """Build one 208-byte tuning record for 12-TET equal temperament.

    96 notes × 2 bytes (signed 16-bit, binary cents from C1) + 16-byte name.
    For equal temperament, note N has offset N * 128 binary cents.
    """
    buf = bytearray(208)
    for note in range(96):
        # 1 binary cent = 1 semitone / 128 (MS2 spec: signed offset from C1 in
        # units of semitone/128). For 12-TET, note N above C1 = N semitones,
        # so offset = N × 128 binary cents.
        offset_cents = note * 128
        struct.pack_into('<h', buf, note * 2, offset_cents)
    # Name (bytes 192..207, 16 ASCII chars, space-padded)
    name = b'Equal Temp.     '[:16]
    buf[192:208] = name
    return bytes(buf)


def build_tunings_chunk_data(n_tunings: int = 1) -> bytes:
    """Build chunk-type-2 data: count word + n_tunings × tuning records."""
    tuning = _equal_temperament_tuning()
    count_bytes = struct.pack('<H', n_tunings)
    return count_bytes + tuning * n_tunings


# ---------------------------------------------------------------------------
# Chunk type 3: 2OP Instruments (30 bytes per instrument)
# ---------------------------------------------------------------------------

def _build_2op_instrument(
    opl4_regs: dict,
    tuning_idx: int = 0,
    name: str = '',
) -> bytes:
    """Build one 30-byte 2OP instrument record.

    Layout:
        +0  byte   Tuning index (0-based)
        +1  2 bytes  Must be zero
        +3  byte   OPL4 reg 20H (mod AM/VIB/EGT/KSR/MULT)
        +4  byte   OPL4 reg 21H (car AM/VIB/EGT/KSR/MULT)
        +5  byte   OPL4 reg 40H (mod KSL/TL)
        +6  byte   OPL4 reg 41H (car KSL/TL)
        +7  byte   OPL4 reg 60H (mod AR/DR)
        +8  byte   OPL4 reg 61H (car AR/DR)
        +9  byte   OPL4 reg 80H (mod SL/RR)
        +10 byte   OPL4 reg 81H (car SL/RR)
        +11 byte   OPL4 reg E0H (mod waveform)
        +12 byte   OPL4 reg E1H (car waveform)
        +13 byte   OPL4 reg C0H (feedback/connection)
        +14 16 bytes  Name (ASCII, space-padded)
    """
    buf = bytearray(30)
    buf[0] = tuning_idx
    # buf[1:3] must be zero (already 0)
    reg_order = [0x20, 0x21, 0x40, 0x41, 0x60, 0x61, 0x80, 0x81, 0xE0, 0xE1, 0xC0]
    for i, reg in enumerate(reg_order):
        buf[3 + i] = opl4_regs.get(reg, 0) & 0xFF
    name_bytes = (name.encode('ascii', errors='replace') + b' ' * 16)[:16]
    buf[14:30] = name_bytes
    return bytes(buf)


def build_2op_instruments_chunk_data(
    instruments: list[tuple[bytes, str]],
) -> bytes:
    """Build chunk-type-3 data from a list of (patch_bytes, name) tuples.

    Each entry is converted via opll_patch_to_opl4().
    Prepends a 2-byte count word.
    """
    count = len(instruments)
    data = struct.pack('<H', count)
    for patch_bytes, name in instruments:
        opl4 = opll_patch_to_opl4(patch_bytes)
        data += _build_2op_instrument(opl4, tuning_idx=0, name=name)
    return data


# ---------------------------------------------------------------------------
# Chunk type 4: 4OP Instruments  (placeholder, 41 bytes per instrument)
# ---------------------------------------------------------------------------

def build_4op_instruments_chunk_data(n: int = 1) -> bytes:
    """Build a minimal chunk-type-4 with n placeholder 4OP instruments."""
    # 4OP instrument = 41 bytes: 25 bytes registers/tuning (all 0) + 16 bytes name
    _EMPTY_4OP = b'\x00' * 25 + b'Empty Instrument'
    return struct.pack('<H', n) + _EMPTY_4OP * n


# ---------------------------------------------------------------------------
# Chunk type 5: WAV Instruments  (placeholder, 36 bytes per instrument)
# ---------------------------------------------------------------------------

def build_wav_instruments_chunk_data(n: int = 1) -> bytes:
    """Build a minimal chunk-type-5 with n placeholder WAV instruments.

    The empty WAV instrument format (36 bytes total) matches TEST.MS2:
        +0  byte  Flag  = 0x01
        +1  byte  Tuning index = 0
        +2..4     Wavetable data: 0x60 0xFF 0xFF  (minimum wavetable)
        +5..19    Padding zeros (15 bytes)
        +20..35   Name "Empty Instrument" (16 bytes)
    """
    _EMPTY_WAV = bytes([0x01, 0x00, 0x60, 0xFF, 0xFF]) + b'\x00' * 15 + b'Empty Instrument'
    return struct.pack('<H', n) + _EMPTY_WAV * n


# ---------------------------------------------------------------------------
# Chunk type 8: Pitch Envelopes  (placeholder)
# ---------------------------------------------------------------------------

def build_pitch_envelopes_chunk_data(n: int = 1) -> bytes:
    """Build a minimal chunk-type-8 with n placeholder pitch envelopes.

    A minimal pitch envelope: [0x00, 0x0A, 0x02] = value 0, end (10), loop (2).
    This produces a no-change looping envelope (effectively inactive).
    """
    _EMPTY_PE = bytes([0x00, 0x0A, 0x02])
    return struct.pack('<H', n) + _EMPTY_PE * n


# ---------------------------------------------------------------------------
# Chunk type 6: Pattern encoding helpers
# ---------------------------------------------------------------------------

def _encode_channel_in_pattern(
    events_at_steps: list[tuple],
) -> bytes:
    """Encode one channel's events for a single pattern into MS2 bytes.

    events_at_steps is a list of (step, [event_byte_seq, ...]).
    Each event_byte_seq is a bytes-like object (e.g. bytes([0x00, 36])).

    Structural guarantees (MS2 spec):
      - Each slice is: 0..5 event items, then exactly one time-length code.
      - Time-length code = 0x80 | length,  length ∈ 1..16.
      - The sum of all time-length codes for a channel-pattern = 16.
      - Events at the same step are merged before encoding.
      - If a step has >5 event items, the remainder is pushed into extra
        micro-slices (duration 1 each) so nothing is silently dropped.
    """
    MAX_EVENTS_PER_SLICE = 5

    # ── 1. Merge events at the same step ─────────────────────────────────
    merged: dict[int, list] = {}
    for step, evts in events_at_steps:
        if not (0 <= step < STEPS_PER_PATTERN):
            continue
        if step in merged:
            merged[step].extend(evts)
        else:
            merged[step] = list(evts)

    if not merged:
        return bytes([0x80 | STEPS_PER_PATTERN])   # 0x90: whole-note rest

    buf = bytearray()
    cur_time = 0                        # steps elapsed so far (0..16)
    sorted_steps = sorted(merged.keys())

    for idx, step in enumerate(sorted_steps):
        if cur_time >= STEPS_PER_PATTERN:
            break                       # pattern already full; skip late events

        evts = merged[step]

        # ── 2. Fill gap before this step ─────────────────────────────────
        gap = step - cur_time
        if gap > 0:
            buf.append(0x80 | gap)
            cur_time += gap

        # ── 3. Emit events in slices of ≤ MAX_EVENTS_PER_SLICE ──────────
        # Determine the time we need to reach after all events at this step.
        if idx + 1 < len(sorted_steps):
            target_time = sorted_steps[idx + 1]
        else:
            target_time = STEPS_PER_PATTERN

        total_items = len(evts)
        offset = 0
        while offset < total_items:
            chunk = evts[offset: offset + MAX_EVENTS_PER_SLICE]
            offset += MAX_EVENTS_PER_SLICE

            for ev in chunk:
                buf.extend(bytes(ev))

            if offset >= total_items:
                # Last (or only) chunk → advance to target_time
                duration = target_time - cur_time
                if duration <= 0:
                    # Target already reached (can happen if previous micro-slices
                    # consumed all remaining steps).  Emit duration 1 to keep the
                    # stream structurally valid, capping at pattern boundary.
                    duration = 1
                duration = min(duration, STEPS_PER_PATTERN - cur_time)
                if duration <= 0:
                    # Pattern is already full; we cannot advance further.
                    # The events we just emitted won't have a time code — which
                    # is invalid.  Remove the event bytes we just added so the
                    # previous time code remains the final code for this stream.
                    # This should only happen when events overflow past step 15.
                    n_removed = 0
                    while buf and not (buf[-1] & 0x80):
                        buf.pop()
                        n_removed += 1
                    if n_removed:
                        import warnings
                        warnings.warn(
                            f"_encode_channel_in_pattern: dropped {n_removed} "
                            f"event byte(s) at step {step} because pattern is "
                            f"already full (cur_time={cur_time})",
                            stacklevel=3,
                        )
                    break
            else:
                # Intermediate chunk: advance by 1 step (minimum valid code)
                duration = 1

            buf.append(0x80 | duration)
            cur_time += duration

    # ── 4. Fill remaining time to reach exactly 16 ───────────────────────
    remaining = STEPS_PER_PATTERN - cur_time
    if remaining > 0:
        buf.append(0x80 | remaining)

    return bytes(buf)


def build_patterns_chunk_data(
    channel_event_lists: list[list],
    n_patterns: int,
    n_channels: int,
) -> bytes:
    """Build chunk-type-6 data (count word + concatenated pattern data).

    channel_event_lists[pat][ch] = sorted list of (step, [event_bytes, ...])
    for pattern pat, channel ch.
    """
    buf = struct.pack('<H', n_patterns)
    for pat in range(n_patterns):
        for ch in range(n_channels):
            evts = channel_event_lists[pat][ch] if pat < len(channel_event_lists) else []
            buf += _encode_channel_in_pattern(evts)
    return buf


# ---------------------------------------------------------------------------
# Segment → step grid conversion
# ---------------------------------------------------------------------------

def _build_step_grid(
    segments: dict,
    ticks_per_step: int,
    total_steps: int,
) -> dict:
    """Convert per-channel segment lists to a step grid.

    Returns a dict keyed by (ch, step) where value is
    (keyon, ms2_note, inst, vol_opll, retrigger, at_token) representing the
    state that *starts* at that step.  Keyon=0 means silent.  retrigger=True
    means the note was explicitly re-attacked at this step (the note-on opcode
    should be emitted even if the pitch has not changed from the previous step).

    Uses event-based note-start assignment: each segment is assigned to the
    step where it begins (tick_start // ticks_per_step).  Steps between note
    events carry forward the most recent state.  This correctly handles notes
    that start slightly after a step boundary (e.g. 2 ticks after step 0)
    which the old snapshot-at-step-start approach would miss.

    When a key-off and a new key-on happen at the same step (adjacent notes
    with no gap in between), the note-on overrides the key-off AND the
    retrigger flag is set to True so that a NOTE_ON opcode is always emitted,
    even if the pitch happens to be identical to the previous note.
    """
    grid: dict[tuple, tuple] = {}

    for ch in range(NUM_CH):
        segs = segments.get(ch, [])

        # ── Collect keyoff steps and build note-on assignments ────────────
        assignments: dict[int, tuple] = {}   # step → (keyon, ms2_note, inst, vol, retrigger, at_token)
        keyoff_steps: set = set()

        # Pass 1: collect all steps that have a keyoff segment.  Doing this
        # before processing keyon segments makes retrigger detection
        # order-independent when keyoff and keyon segments share the same
        # quantised step (e.g. the zero-length keyoff marker inserted by
        # _build_segments() for hidden retriggers).
        for seg in segs:
            if seg.keyon:
                continue
            start_step = seg.tick_start // ticks_per_step
            if start_step < total_steps:
                keyoff_steps.add(start_step)

        # Pass 2: build per-step assignments.
        for seg in segs:
            start_step = seg.tick_start // ticks_per_step
            if start_step >= total_steps:
                continue

            at_token = getattr(seg, 'at_token', '')
            if seg.keyon:
                ms2_note = fnum_block_to_ms2_note(seg.fnum, seg.block)
                if ms2_note is not None:
                    # Retrigger if a key-off also lands on this step (adjacent
                    # notes with no inter-step gap: note-off and note-on share
                    # the same step boundary).
                    retrigger = start_step in keyoff_steps
                    # Later note-on at same step overrides earlier one.
                    assignments[start_step] = (1, ms2_note, seg.inst, seg.vol,
                                               retrigger, at_token)
            else:
                # Only record key-off if no note-on already occupies this step.
                if start_step not in assignments or assignments[start_step][0] == 0:
                    assignments[start_step] = (0, 0, seg.inst, seg.vol, False,
                                               at_token)

        # ── Carry forward state across steps ─────────────────────────────
        # Steps without explicit events inherit the state of the previous step.
        # The retrigger flag is cleared on carry-forward so it is only True for
        # the exact step where the re-attack occurred.
        cur_state: tuple = (0, 0, 0, 0, False, '')   # start silent
        for step in range(total_steps):
            if step in assignments:
                cur_state = assignments[step]
            else:
                # Clear the retrigger flag when carrying forward
                if cur_state[4]:
                    cur_state = cur_state[:4] + (False, cur_state[5])
            grid[(ch, step)] = cur_state

    return grid


def _build_instrument_table(
    segments: dict,
    user_patches: dict,
) -> tuple[list, dict]:
    """Build the MS2 instrument list from segment voice information.

    Scans all segments for unique (inst, patch_key) pairs and builds a
    0-based instrument table.

    Returns:
        instruments: list of (patch_bytes, name) tuples, in encounter order
        inst_map:    dict mapping (inst, patch_key) → 0-based MS2 index
    """
    instruments: list[tuple[bytes, str]] = []
    inst_map: dict[tuple, int] = {}

    def _patch_key(inst: int, user_patches_local: dict) -> tuple:
        if inst == 0:
            # Use the first user patch encountered (or zeros as fallback)
            pb = next(iter(user_patches_local.values()), bytes(8))
            return ('user', pb)
        return ('preset', inst)

    def _add_instrument(key: tuple) -> int:
        if key in inst_map:
            return inst_map[key]
        idx = len(instruments)
        inst_map[key] = idx
        if key[0] == 'user':
            patch_b = key[1]
            instruments.append((patch_b, 'User Patch'))
        else:
            preset_num = key[1]
            patch_b = OPLL_ROM_PATCHES.get(preset_num, bytes(8))
            name = OPLL_PRESET_NAMES.get(preset_num, f'Preset {preset_num}')
            instruments.append((patch_b, name))
        return idx

    # Walk all segments, collecting unique voice keys
    # For user patches, we need the actual patch bytes (from user_patches dict)
    for ch in range(NUM_CH):
        for seg in segments.get(ch, []):
            if seg.inst == 0:
                # Find the user patch bytes by matching at_token or voice_id
                # _assign_voice_ids stores user_patches as {at_v_num: patch_bytes}
                # We stored the patch in seg.at_token as '@N' (1-based)
                try:
                    v_num = int(seg.at_token.lstrip('@v').lstrip('@'))
                    patch_b = user_patches.get(v_num, bytes(8))
                except (ValueError, AttributeError):
                    patch_b = bytes(8)
                key = ('user', bytes(patch_b))
            else:
                key = ('preset', seg.inst)
            _add_instrument(key)

    # Ensure at least 1 instrument
    if not instruments:
        _add_instrument(('preset', 8))   # Organ as fallback

    return instruments, inst_map


def _get_ms2_inst_idx(
    inst: int,
    at_token: str,
    user_patches: dict,
    inst_map: dict,
) -> int:
    """Return the 0-based MS2 instrument index for a segment."""
    if inst == 0:
        try:
            v_num = int(at_token.lstrip('@v').lstrip('@'))
            patch_b = bytes(user_patches.get(v_num, bytes(8)))
        except (ValueError, AttributeError):
            patch_b = bytes(8)
        key = ('user', patch_b)
    else:
        key = ('preset', inst)
    return inst_map.get(key, 0)


# ---------------------------------------------------------------------------
# High-level: segments → MS2 patterns
# ---------------------------------------------------------------------------

def segments_to_patterns(
    segments: dict,
    ticks_per_step: int,
    n_patterns: int,
    n_channels: int,
    n_instruments: int,
    user_patches: dict | None = None,
    ref_count: int = 0,
) -> list[list]:
    """Convert OPLL segment data to MS2 pattern event lists.

    Returns channel_event_lists[pat][ch] = sorted list of
        (step_in_pattern, [event_bytes, ...]).

    Args:
        segments:       Per-channel segment dicts (remapped to 0-based channel indices).
        ticks_per_step: VGM ticks per MS2 step (quantisation resolution).
        n_patterns:     Total number of MS2 patterns to generate.
        n_channels:     Number of active channels (= MS2 channel count).
        n_instruments:  Total number of instruments in the reference library plus
                        any appended user-patch instruments.  Used to clamp OPLL
                        preset indices when mapping to MS2 indices.
        user_patches:   Mapping from 1-based v-number to 8-byte patch bytes for
                        user patches (inst==0).  When provided, segments with
                        inst==0 are mapped to indices ref_count + (v_num - 1).
        ref_count:      Number of instruments in the reference library.  User-patch
                        instruments are appended starting at this index.

    Behaviour:
      - Pattern 0 / step 0: INST is always emitted for each channel that plays
        a note there (even if already set), providing a safe state anchor that
        prevents MS² from using an uninitialised instrument register.
      - Pattern 1+: INST and VOL are only emitted when they change relative to
        the previously emitted value – no forced re-emission at pattern starts.
      - Within each step, events are ordered NOTE → VOL → INST (highest-
        priority musical information first, as per the MS² reference convention).
    """
    if user_patches is None:
        user_patches = {}

    total_steps = n_patterns * STEPS_PER_PATTERN
    grid = _build_step_grid(segments, ticks_per_step, total_steps)

    # channel_event_lists[pat][ch] = list of (step, [event_bytes])
    channel_event_lists: list[list] = []
    for _ in range(n_patterns):
        channel_event_lists.append([[] for _ in range(n_channels)])

    for ch in range(n_channels):
        prev_state = (0, 0, 0, 0, False, '')   # (keyon, ms2_note, inst, vol, retrigger, at_token)
        prev_inst_idx = -1
        prev_vol_ms2 = -1

        for step in range(total_steps):
            pat = step // STEPS_PER_PATTERN
            step_in_pat = step % STEPS_PER_PATTERN

            state = grid.get((ch, step), (0, 0, 0, 0, False, ''))
            keyon, ms2_note, inst, vol_opll, retrigger, at_token = state
            prev_keyon = prev_state[0]
            prev_note  = prev_state[1]

            vol_ms2 = opll_vol_to_ms2_vol(vol_opll)

            evts_here: list[bytes] = []

            if keyon and ms2_note is not None:
                # Resolve MS2 instrument index.
                # For user patches (inst==0) map via at_token '@vN' to the
                # appended slot ref_count + (N-1); fall back to 0 on error.
                # For ROM presets use the direct reference-library mapping.
                if inst == 0 and user_patches:
                    v_num = _parse_user_v_num(at_token)
                    if v_num is not None and v_num in user_patches:
                        inst_idx = ref_count + (v_num - 1)
                    else:
                        inst_idx = 0
                else:
                    inst_idx = _opll_inst_to_ms2_idx(inst, n_instruments)

                # Emit NOTE_ON when: pitch changed, first note after silence,
                # instrument changed, or the note was explicitly re-attacked
                # (keyoff + keyon at the same step boundary, retrigger=True).
                note_changed = (ms2_note != prev_note) or (not prev_keyon) or retrigger
                inst_changed = (inst_idx != prev_inst_idx)
                vol_changed  = (vol_ms2 != prev_vol_ms2)

                # Pat 0 / step 0: always force INST for safety (reference behaviour).
                force_inst = (pat == 0 and step_in_pat == 0)

                emit_note = note_changed or inst_changed
                emit_vol  = vol_changed
                emit_inst = inst_changed or force_inst

                # Event ordering: NOTE first, then VOL, then INST.
                if emit_note:
                    evts_here.append(bytes([0x00, ms2_note & 0xFF]))
                if emit_vol:
                    evts_here.append(bytes([0x05, vol_ms2 & 0xFF]))
                    prev_vol_ms2 = vol_ms2
                if emit_inst:
                    evts_here.append(bytes([0x04, inst_idx & 0xFF]))
                    prev_inst_idx = inst_idx

            elif not keyon and prev_keyon:
                # Note just turned off → emit key-off.
                # Do NOT reset prev_inst_idx / prev_vol_ms2 here: keeping them
                # avoids spurious @/V re-emissions when the same instrument and
                # volume resumes after a short gap.
                evts_here.append(bytes([0x01]))

            if evts_here:
                channel_event_lists[pat][ch].append((step_in_pat, evts_here))

            prev_state = state

    return channel_event_lists


def _get_segment_inst_idx_at_step(
    segments: dict,
    ch: int,
    step: int,
    ticks_per_step: int,
    user_patches: dict,
    inst_map: dict,
) -> int:
    """Find the MS2 instrument index for channel ch at the given step.

    Uses the same event-based start-step assignment as _build_step_grid:
    find the last segment whose tick_start // ticks_per_step <= step.
    """
    segs = segments.get(ch, [])
    found = None
    for seg in segs:
        seg_step = seg.tick_start // ticks_per_step
        if seg_step <= step:
            found = seg
        else:
            break
    if found is not None and found.keyon:
        return _get_ms2_inst_idx(found.inst, found.at_token, user_patches, inst_map)
    return 0


def _get_starting_instruments(
    segments: dict,
    n_channels: int,
    n_instruments: int = 15,
    ticks_per_step: int = TICKS_PER_STEP_75BPM,
    user_patches: dict | None = None,
    ref_count: int = 0,
) -> list[int]:
    """Return the starting instrument index for each channel.

    Uses the direct OPLL-preset → reference-library index mapping
    (_opll_inst_to_ms2_idx) to stay consistent with the reference library.
    For user patches (inst==0), resolves via at_token '@vN' to the appended
    instrument slot ref_count + (N-1).
    """
    if user_patches is None:
        user_patches = {}
    result = []
    for ch in range(n_channels):
        segs = segments.get(ch, [])
        # Find the first keyon segment to determine the channel's opening instrument.
        first_keyon = next((s for s in segs if s.keyon), None)
        if first_keyon is None:
            result.append(0)
        elif first_keyon.inst == 0 and user_patches:
            v_num = _parse_user_v_num(getattr(first_keyon, 'at_token', ''))
            if v_num is not None and v_num in user_patches:
                result.append(ref_count + (v_num - 1))
            else:
                result.append(0)
        else:
            result.append(_opll_inst_to_ms2_idx(first_keyon.inst, n_instruments))
    return result

def build_instrument_library_ms2(
    patches: list,
    library_name: str = 'OPLL Library',
    n_presets: int = 16,
    raw_mode: bool = False,
) -> bytes:
    """Build a minimal MS2 file that contains only tunings and 2OP instruments.

    This is used to export instrument-only MS2 files (no song data) for use
    as base preset libraries via ``--base-ms2``.

    Args:
        patches:      List of at least *n_presets* 8-byte OPLL patch sequences
                      (each element is bytes-like with 8 bytes, R0..R7).
        library_name: Up to 65 ASCII chars used as the MS2 description.
        n_presets:    Number of preset instruments to include (default 16).
                      Uses the first *n_presets* entries from *patches*.
        raw_mode:     When ``True``, pack patch bytes into OPL4 register fields
                      using :func:`opll_patch_raw` (direct field mapping, no
                      additional OPLL→OPL conversion tables).
                      When ``False`` (default), use :func:`opll_patch_to_opl4`
                      (the "converted" mode, current default behaviour).

    Returns raw bytes of a complete, loadable .MS2 file.
    """
    _converter = opll_patch_raw if raw_mode else opll_patch_to_opl4

    # Build the 2OP instruments chunk from the first n_presets patches.
    preset_count = min(n_presets, len(patches))
    instruments_bytes = struct.pack('<H', preset_count)
    for i in range(preset_count):
        patch = bytes(patches[i])[:8]
        opl4 = _converter(patch)
        name = f'Preset {i + 1}'
        instruments_bytes += _build_2op_instrument(opl4, tuning_idx=0, name=name)

    # Use a single equal-temperament tuning.
    tunings_bytes = build_tunings_chunk_data(1)
    n_tunings = 1

    # Minimal song settings: 1 pattern, 1 channel (all silent, no notes).
    song_settings = build_song_settings_data(
        description        = library_name[:65],
        n_patterns         = 1,
        n_tunings          = n_tunings,
        n_2op_inst         = preset_count,
        n_4op_inst         = 1,
        n_wav_inst         = 1,
        n_pitch_env        = 1,
        n_channels         = 1,
        timer              = 0,
        timer_divider      = 0x0C00,
    )

    # Minimal patterns chunk: 1 pattern with 1 empty channel (whole-note rest).
    patterns_data = build_patterns_chunk_data(
        channel_event_lists=[[[]]],
        n_patterns=1,
        n_channels=1,
    )
    four_op_data   = build_4op_instruments_chunk_data(1)
    wav_data       = build_wav_instruments_chunk_data(1)
    pitch_env_data = build_pitch_envelopes_chunk_data(1)

    ms2_bytes = (
        MS2_SIGNATURE
        + _build_chunk(1, song_settings)
        + _build_chunk(2, tunings_bytes)
        + _build_chunk(3, instruments_bytes)
        + _build_chunk(4, four_op_data)
        + _build_chunk(5, wav_data)
        + _build_chunk(6, patterns_data)
        + _build_chunk(8, pitch_env_data)
        + _sentinel_chunk()
    )
    return ms2_bytes


# ---------------------------------------------------------------------------
# Top-level file builder
# ---------------------------------------------------------------------------

def build_ms2_file(
    segments: dict,
    voice_csv_path: str | None,
    stem: str,
    bpm: int = 120,
    timer: int = 0,
    base_ms2: str | None = None,
    video: str = 'ntsc',
    debug: bool = False,
) -> bytes:
    """Build a complete MS2 binary from OPLL segment data.

    Args:
        segments:       Per-channel segment lists (output of _build_segments).
        voice_csv_path: Path to the OPLL voice/patch CSV (for user patches).
        stem:           Song name (used as description).
        bpm:            Song tempo in BPM (default 120).
        timer:          0 = VDP timer (default), 1 = 50 Hz timer.
        base_ms2:       Optional path to an MS2 file to use as the base preset
                        instrument library.  When provided, the first 16
                        instruments from that file are used as OPLL presets
                        1..16 (index 0 = inst 1, …, index 15 = inst 16).
                        Instruments beyond index 15 are ignored; user patches
                        (inst==0) are still appended after index 15 as usual.
                        Falls back to the built-in YM2413.MS2 if the file
                        cannot be loaded.
        video:          'ntsc' or 'pal'; only used when timer=0 (VDP).
        debug:          Print timing diagnostics when True.

    Returns raw bytes of the complete .MS2 file.
    """
    # ── Assign voice IDs and collect user patches ────────────────────────
    from opll_mml import _assign_voice_ids  # noqa: PLC0415
    voice_table, user_patches, _warnings = _assign_voice_ids(segments, voice_csv_path)

    # ── Timing parameters ────────────────────────────────────────────────
    # Choose interrupt frequency based on timer mode and video system.
    # timer=1 (50 Hz fixed) always uses PAL frequency regardless of --video.
    # timer=0 (VDP) uses NTSC or PAL depending on --video.
    # TODO: timer >= 2 (OPL Timer1, N1 = timer value) is not yet implemented.
    if timer == 1:
        freq_hz = _FREQ_PAL
    elif video == 'pal':
        freq_hz = _FREQ_PAL
    else:
        freq_hz = _FREQ_NTSC

    # Use floor to bias timer divider to the lower integer (selects the
    # slightly faster quantisation slot for the given nominal BPM).
    # Example: for NTSC and bpm=120 raw≈1919.6329 -> floor->1919 -> implied BPM≈120.03
    timer_divider  = int(math.floor(freq_hz * 3840 / bpm))  # 8.8 fixed-point: 3840 = 15 * 256
    # Ensure stays in 16-bit range and at least 1
    timer_divider  = max(1, min(0xFFFF, timer_divider))
    ticks_per_step = max(1, round(freq_hz * 15 / bpm))

    if debug:
        bpm2 = freq_hz * 3840 / timer_divider
        print(f"  freq_hz           = {freq_hz}")
        print(f"  timer_divider_raw = {timer_divider} (0x{timer_divider:04X}, "
              f"/{256}: {timer_divider / 256:.6f})")
        print(f"  ticks_per_step    = {ticks_per_step}")
        print(f"  implied BPM       = {bpm2:.4f}")

    # ── Compute total number of patterns ─────────────────────────────────
    # Use the tick of the last NOTE-ON segment end (not key-off) to avoid
    # adding extra empty patterns for long trailing silences.
    last_note_end_tick = 0
    for ch in range(NUM_CH):
        for seg in segments.get(ch, []):
            if seg.keyon:
                last_note_end_tick = max(last_note_end_tick, seg.tick_end)
    if last_note_end_tick == 0:
        # No notes found: fall back to total key-off tick
        for ch in range(NUM_CH):
            for seg in segments.get(ch, []):
                last_note_end_tick = max(last_note_end_tick, seg.tick_end)
    n_patterns = max(1, math.ceil(last_note_end_tick / (ticks_per_step * STEPS_PER_PATTERN)))

    # ── Determine active channel count ───────────────────────────────────
    # Count only OPLL channels that actually have note data (keyon=1 segments).
    # Unused channels are excluded to keep the MS2 file small and correct.
    active_channels = []
    for ch in range(NUM_CH):
        if any(seg.keyon for seg in segments.get(ch, [])):
            active_channels.append(ch)
    n_channels = max(1, len(active_channels))

    # ── Load instrument library ───────────────────────────────────────────
    # When --base-ms2 is provided, load tunings and instruments from that file,
    # truncating to the first 16 instruments (OPLL presets 1..16 → indices 0..15).
    # Otherwise fall back to the built-in MoonBlaster v1.4 reference library.
    if base_ms2 is not None:
        tunings_data, instruments_data, n_tunings, n_2op_inst = _load_library_from_ms2(
            base_ms2, max_presets=16
        )
    else:
        tunings_data, instruments_data, n_tunings, n_2op_inst = _load_reference_library()

    # ── Append user-patch instruments to the instruments chunk ────────────
    # Reference library instruments are kept at their original indices 0..(N-1).
    # Each distinct user patch is appended after them so that pattern events
    # can reference inst==0 channels at index ref_count + (v_num - 1).
    ref_count = n_2op_inst
    if user_patches:
        extra_records = b''
        for v_num in sorted(user_patches):
            patch_bytes = user_patches[v_num]
            opl4 = opll_patch_to_opl4(patch_bytes)
            name = f'User Patch v{v_num}'
            extra_records += _build_2op_instrument(opl4, tuning_idx=0, name=name)
        new_total = ref_count + len(user_patches)
        instruments_data = struct.pack('<H', new_total) + instruments_data[2:] + extra_records
        n_2op_inst = new_total

    # ── Compute per-channel starting instrument indices ───────────────────
    # Remap active channels to contiguous 0-based indices for MS2 output.
    remapped_segments = {
        new_ch: segments.get(orig_ch, [])
        for new_ch, orig_ch in enumerate(active_channels)
    }
    starting_instruments = _get_starting_instruments(
        remapped_segments, n_channels, n_2op_inst, ticks_per_step,
        user_patches=user_patches, ref_count=ref_count,
    )

    # ── Build pattern data ────────────────────────────────────────────────
    channel_event_lists = segments_to_patterns(
        remapped_segments, ticks_per_step,
        n_patterns, n_channels, n_2op_inst,
        user_patches=user_patches, ref_count=ref_count,
    )

    # ── Assemble chunks ───────────────────────────────────────────────────
    description = stem[:65]

    song_settings = build_song_settings_data(
        description     = description,
        n_patterns      = n_patterns,
        n_tunings       = n_tunings,
        n_2op_inst      = n_2op_inst,
        n_channels      = n_channels,
        timer           = timer,
        timer_divider   = timer_divider,
        starting_instruments = starting_instruments,
    )

    patterns_data   = build_patterns_chunk_data(
        channel_event_lists, n_patterns, n_channels
    )
    four_op_data    = build_4op_instruments_chunk_data(1)
    wav_data        = build_wav_instruments_chunk_data(1)
    pitch_env_data  = build_pitch_envelopes_chunk_data(1)

    # ── Concatenate into final file ───────────────────────────────────────
    ms2_bytes = (
        MS2_SIGNATURE
        + _build_chunk(1, song_settings)
        + _build_chunk(2, tunings_data)
        + _build_chunk(3, instruments_data)
        + _build_chunk(4, four_op_data)
        + _build_chunk(5, wav_data)
        + _build_chunk(6, patterns_data)
        + _build_chunk(8, pitch_env_data)
        + _sentinel_chunk()
    )
    return ms2_bytes
