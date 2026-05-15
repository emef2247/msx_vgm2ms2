# py/ms2_writer.py
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
from opll_mml import _build_segments, _opll_note, detect_num_ch,NUM_CH

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

MS2_TUNINIG = [
(0,'Equal Temp.',[0, 128, 256, 384, 512, 640, 768, 896, 1024, 1152, 1280, 1408]),
(1,'MB Amiga detune',[-3, 125, 255, 381, 514, 640, 768, 898, 1026, 1152, 1279, 1413]),
(2,'MB turboR detune',[0, 128, 256, 384, 511, 640, 767, 896, 1024, 1152, 1279, 1408]),
(3,'MB FM Editor T-3',[-46, 80, 212, 342, 470, 600, 732, 862, 990, 1120, 1250, 1380]),
(4,'MB FM Editor T-2',[-34, 92, 222, 352, 480, 610, 742, 870, 998, 1128, 1258, 1386]),
(5,'MB FM Editor T-1',[-20, 106, 236, 364, 490, 620, 752, 880, 1006, 1136, 1264, 1392]),
(6,'MB FM Editor T+0',[-8, 118, 246, 374, 500, 630, 760, 888, 1014, 1144, 1272, 1400]),
(7,'MB FM Editor T+1',[4, 130, 258, 384, 510, 640, 770, 896, 1022, 1152, 1280, 1406]),
(8,'MB FM Editor T+2',[16, 142, 268, 396, 522, 648, 778, 904, 1030, 1158, 1286, 1412]),
(9,'MB FM Editor T+3',[30, 154, 280, 406, 532, 658, 788, 912, 1038, 1166, 1294, 1420]),
(10,'MB FM Player T-3',[-40, 86, 218, 348, 476, 606, 738, 866, 994, 1124, 1254, 1382]),
(11,'MB FM Player T-2',[-28, 98, 228, 358, 486, 616, 746, 874, 1002, 1132, 1262, 1390]),
(12,'MB FM Player T-1',[-16, 110, 240, 368, 496, 624, 756, 884, 1010, 1140, 1268, 1396]),
(13,'MB FM Player T+0',[-2, 124, 252, 380, 506, 634, 764, 892, 1018, 1148, 1276, 1404]),
(14,'MB FM Player T+1',[10, 136, 264, 390, 516, 644, 774, 898, 1026, 1154, 1282, 1408]),
(15,'MB FM Player T+2',[24, 148, 274, 400, 526, 654, 782, 908, 1034, 1162, 1290, 1416]),
(16,'MB FM Player T+3',[36, 160, 286, 412, 536, 664, 792, 916, 1042, 1170, 1298, 1424]),
(17,'MB WAV detune -3',[-13, 104, 232, 362, 491, 620, 749, 878, 1007, 1136, 1266, 1394]),
(18,'MB WAV detune -2',[-9, 111, 240, 369, 498, 627, 756, 884, 1013, 1142, 1271, 1399]),
(19,'MB WAV detune -1',[-4, 120, 248, 377, 505, 633, 762, 890, 1018, 1147, 1276, 1403]),
(20,'MB WAV detune +1',[8, 136, 263, 391, 519, 646, 774, 902, 1029, 1157, 1285, 1412]),
(21,'MB WAV detune +2',[17, 144, 271, 399, 526, 653, 780, 907, 1034, 1162, 1290, 1417]),
(22,'MB WAV detune +3',[25, 152, 279, 406, 533, 659, 786, 913, 1040, 1167, 1295, 1422]),
]
  
MS2_RHYTHM_INSTRUMENTS = [
(15,'Bass Drum 1     ',0,bytes([0x20,0x20,0x09,0x00,0xFA,0xF6,0xFF,0xFF,0x00,0x00,0x00])),
(16,'Snare Drum      ',0,bytes([0x20,0x00,0x00,0x00,0xF1,0xF7,0x00,0xB8,0x00,0x00,0x0E])),
(17,'Acid drum       ',0,bytes([0x04,0x00,0x03,0x0D,0xF9,0xF9,0xF9,0xF9,0x00,0x00,0x00])),
(18,'Closed Hihat    ',0,bytes([0x2F,0x0F,0x00,0x00,0xF1,0x99,0x00,0xB8,0x00,0x00,0x0E])),
(19,'Cymbal          ',0,bytes([0x02,0x02,0x00,0x00,0x33,0x33,0x73,0x73,0x00,0x00,0x03])),
]

# 例:
# build_inst_ms2_from_text(INST_LIST_TEXT, "MyCustomLibrary.MS2")
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
DEFAULT_RHYTHM_NOTE = 36    # Default MS2 note (C3) for rhythm hits with no explicit fnum
MAX_PATTERN_CHUNK_SIZE = 16384  # MS2 tool hangs if a Patterns chunk exceeds this

def build_tunings_chunk_data_from_list(tuning_defs):
    """
    tuning_defs: [(idx, name, [offsets12]), ...]
    """
    count = len(tuning_defs)
    buf = struct.pack('<H', count)
    for idx, name, cents in tuning_defs:
        tb = bytearray(208)
        for o in range(96):
            octave = o // 12
            note   = o % 12
            val = cents[note] + octave * 128
            struct.pack_into('<h', tb, o*2, val)
        tb[192:208] = (name.encode('ascii', errors='replace') + b' '*16)[:16]
        buf += tb
    return buf

def build_2op_instruments_chunk_data_from_list(instlist):
    """
    instlist: list of (idx, name, tuning_idx, bytes11)
    """
    data = struct.pack('<H', len(instlist))
    for idx, name, tuning, regbytes in instlist:
        buf = bytearray(30)
        buf[0] = tuning   # tuning index
        # buf[1], buf[2] reserved (0)
        buf[3:14] = regbytes  # 11バイト
        name_enc = (name.encode('ascii', 'replace') + b' '*16)[:16]
        buf[14:30] = name_enc
        data += buf
    return data
    
def build_tunings_chunk_data_from_defs(defs):
    """
    [(name, [offsets12])...]からチャンク2形式バイト列を生成
    """
    count = len(defs)
    buf = struct.pack('<H', count)
    for name, cents in defs:
        tb = bytearray(208)
        for o in range(96):
            octave = o // 12
            note   = o % 12
            val = cents[note] + octave * 128 * 1  # 1=純正律ベースだがMS2仕様による
            struct.pack_into('<h', tb, o*2, val)
        tb[192:208] = (name.encode('ascii', errors='replace') + b' '*16)[:16]
        buf += tb
    return buf
    
# ---------------------------------------------------------------------------
# YM2413 → OPL4 register mapping
# ---------------------------------------------------------------------------

def opll_patch_to_opl4(patch_bytes: bytes) -> dict:
    """Convert 8-byte YM2413 user-patch (R0..R7) to OPL4 2OP FM register values.

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
    """Pack 8-byte YM2413 patch (R0..R7) directly into OPL4 2OP register fields."""

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
    if fnum == 0:
        return None
    freq = 49716.0 * fnum * (1 << block) / (1 << 19)
    if freq < 8.0:
        return None
    midi = 69.0 + 12.0 * math.log2(freq / 440.0)
    midi_int = round(midi)
    ms2_note = midi_int - 12
    if ms2_note < 0 or ms2_note > 95:
        return None
    return ms2_note


def opll_vol_to_ms2_vol(opll_vol: int) -> int:
    v = max(0, min(15, opll_vol))
    return min(63, int(round(v * (2.0 / 0.75))))


# ---------------------------------------------------------------------------
# Chunk builder helpers
# ---------------------------------------------------------------------------

def _build_chunk(chunk_type: int, data: bytes) -> bytes:
    if len(data) > 65535:
        raise ValueError(f"Chunk data too large: {len(data)} bytes (max 65535)")
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
    n_channels: int,
    n_2op_inst: int,
    n_4op_inst: int = 1,
    n_wav_inst: int = 1,
    n_pitch_env: int = 1,
    timer: int = 0,
    timer_divider: int = 0x0C00,
    modulation_depth: int = 0xC0,
    starting_instruments: list | None = None,
) -> bytes:
    data = bytearray(291)
    desc_b = description.encode('ascii', errors='replace')
    desc_b = (desc_b + b' ' * 65)[:65]
    data[0:65] = desc_b
    data[65] = max(0, min(255, n_patterns - 1))
    data[66] = 0
    data[67] = timer
    struct.pack_into('<H', data, 68, timer_divider)
    data[70] = modulation_depth
    data[71] = min(42, n_channels)
    data[72] = min(42, n_channels)
    data[73] = 0
    data[74] = 0
    for i in range(min(42, n_channels)):
        data[75 + i] = 1
    if starting_instruments:
        for i, idx in enumerate(starting_instruments[:min(42, n_channels)]):
            data[117 + i] = idx & 0xFF
    def _write_count(offset: int, value: int):
        struct.pack_into('<H', data, offset, max(1, value))
    _write_count(243, n_tunings)
    _write_count(248, n_2op_inst)
    _write_count(253, n_4op_inst)
    _write_count(258, n_wav_inst)
    _write_count(263, n_patterns)
    _write_count(268, n_pitch_env)
    return bytes(data)


# ---------------------------------------------------------------------------
# Reference instrument library (MoonBlaster v1.4 via YM2413.MS2)
# ---------------------------------------------------------------------------

def _read_ms2_chunk_data(data: bytes, target_type: int) -> bytes | None:
    pos = 8
    while pos + 4 <= len(data):
        chunk_type, _version, chunk_size = struct.unpack_from('<BBH', data, pos)
        if chunk_type == 0:
            break
        if chunk_type == target_type:
            return data[pos + 4: pos + 4 + chunk_size]
        pos += 4 + chunk_size
    return None


def _load_reference_library() -> tuple[bytes, bytes, int, int]:
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
    tunings_data = build_tunings_chunk_data(1)
    instruments_data = build_2op_instruments_chunk_data(
        [(OPLL_ROM_PATCHES[8], 'Organ')]
    )
    return tunings_data, instruments_data, 1, 1


def _load_library_from_ms2(
    base_ms2_path: str,
    max_presets: int = 16,
) -> tuple[bytes, bytes, int, int]:
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
        elif n_2op_inst > max_presets:
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
    if inst == 0:
        return 0
    return max(0, min(n_instruments - 1, inst - 1))


def _parse_user_v_num(at_token: str) -> int | None:
    m = re.match(r'^@v(\d+)$', at_token or '')
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Chunk type 2: Tunings (equal temperament)
# ---------------------------------------------------------------------------

def _equal_temperament_tuning() -> bytes:
    buf = bytearray(208)
    for note in range(96):
        offset_cents = note * 128
        struct.pack_into('<h', buf, note * 2, offset_cents)
    name = b'Equal Temp.     '[:16]
    buf[192:208] = name
    return bytes(buf)


def build_tunings_chunk_data(n_tunings: int = 1) -> bytes:
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
    buf = bytearray(30)
    buf[0] = tuning_idx
    reg_order = [0x20, 0x21, 0x40, 0x41, 0x60, 0x61, 0x80, 0x81, 0xE0, 0xE1, 0xC0]
    for i, reg in enumerate(reg_order):
        buf[3 + i] = opl4_regs.get(reg, 0) & 0xFF
    name_bytes = (name.encode('ascii', errors='replace') + b' ' * 16)[:16]
    buf[14:30] = name_bytes
    return bytes(buf)


def build_2op_instruments_chunk_data(
    instruments: list[tuple[bytes, str]],
) -> bytes:
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
    _EMPTY_4OP = b'\x00' * 25 + b'Empty Instrument'
    return struct.pack('<H', n) + _EMPTY_4OP * n


# ---------------------------------------------------------------------------
# Chunk type 5: WAV Instruments  (placeholder, 36 bytes per instrument)
# ---------------------------------------------------------------------------

def build_wav_instruments_chunk_data(n: int = 1) -> bytes:
    _EMPTY_WAV = bytes([0x01, 0x00, 0x60, 0xFF, 0xFF]) + b'\x00' * 15 + b'Empty Instrument'
    return struct.pack('<H', n) + _EMPTY_WAV * n


# ---------------------------------------------------------------------------
# Chunk type 8: Pitch Envelopes  (placeholder)
# ---------------------------------------------------------------------------

def build_pitch_envelopes_chunk_data(n: int = 1) -> bytes:
    _EMPTY_PE = bytes([0x00, 0x0A, 0x02])
    return struct.pack('<H', n) + _EMPTY_PE * n


# ---------------------------------------------------------------------------
# Chunk type 6: Pattern encoding helpers
# ---------------------------------------------------------------------------

def _encode_channel_in_pattern(
    events_at_steps: list[tuple],
) -> bytes:
    MAX_EVENTS_PER_SLICE = 5
    merged: dict[int, list] = {}
    for step, evts in events_at_steps:
        if not (0 <= step < STEPS_PER_PATTERN):
            continue
        if step in merged:
            merged[step].extend(evts)
        else:
            merged[step] = list(evts)
    if not merged:
        return bytes([0x80 | STEPS_PER_PATTERN])
    buf = bytearray()
    cur_time = 0
    sorted_steps = sorted(merged.keys())
    for idx, step in enumerate(sorted_steps):
        if cur_time >= STEPS_PER_PATTERN:
            break
        evts = merged[step]
        gap = step - cur_time
        if gap > 0:
            buf.append(0x80 | gap)
            cur_time += gap
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
                duration = target_time - cur_time
                if duration <= 0:
                    duration = 1
                duration = min(duration, STEPS_PER_PATTERN - cur_time)
                if duration <= 0:
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
                duration = 1
            buf.append(0x80 | duration)
            cur_time += duration
    remaining = STEPS_PER_PATTERN - cur_time
    if remaining > 0:
        buf.append(0x80 | remaining)
    return bytes(buf)


def build_patterns_chunk_data(
    channel_event_lists: list[list],
    n_patterns: int,
    n_channels: int,
) -> bytes:
    buf = struct.pack('<H', n_patterns)
    for pat in range(n_patterns):
        for ch in range(n_channels):
            evts = channel_event_lists[pat][ch] if pat < len(channel_event_lists) else []
            buf += _encode_channel_in_pattern(evts)
    return buf


def build_patterns_chunks(
    channel_event_lists: list[list],
    n_patterns: int,
    n_channels: int,
) -> bytes:
    """Build one or more type-6 Patterns chunks, splitting at MAX_PATTERN_CHUNK_SIZE.

    Returns the concatenated raw bytes of all type-6 chunks ready to embed
    directly in the MS2 file byte stream.
    """
    pattern_data_list: list[bytes] = []
    for pat in range(n_patterns):
        pat_bytes = bytearray()
        for ch in range(n_channels):
            evts = channel_event_lists[pat][ch] if pat < len(channel_event_lists) else []
            pat_bytes += _encode_channel_in_pattern(evts)
        pattern_data_list.append(bytes(pat_bytes))

    result = bytearray()
    i = 0
    while i < n_patterns:
        chunk_patterns: list[bytes] = []
        payload_size = 2  # 2-byte pattern count field
        while i < n_patterns:
            psize = len(pattern_data_list[i])
            if payload_size + psize > MAX_PATTERN_CHUNK_SIZE and chunk_patterns:
                break
            chunk_patterns.append(pattern_data_list[i])
            payload_size += psize
            i += 1
        chunk_payload = struct.pack('<H', len(chunk_patterns)) + b''.join(chunk_patterns)
        result += _build_chunk(6, chunk_payload)
    return bytes(result)


# ---------------------------------------------------------------------------
# Segment → step grid conversion
# ---------------------------------------------------------------------------

def _build_step_grid(
    segments: dict,
    ticks_per_step: int,
    total_steps: int,
) -> dict:
    grid: dict[tuple, tuple] = {}
    # Iterate over all channel indices present in 'segments' (not only NUM_CH).
    # Rhythm channels may be added at indices >= NUM_CH, so include them.
    if segments:
        max_ch = max(segments.keys())
    else:
        max_ch = NUM_CH - 1
    for ch in range(max_ch + 1):
        segs = segments.get(ch, [])
        assignments: dict[int, tuple] = {}
        for seg in segs:
            start_step = seg.tick_start // ticks_per_step
            if start_step >= total_steps:
                continue
            at_token = getattr(seg, 'at_token', '')
            if getattr(seg, 'ev_type', 'note') == 'rhythm':
                if seg.keyon:
                    ms2_note = fnum_block_to_ms2_note(seg.fnum, seg.block)
                    if ms2_note is None:
                        ms2_note = DEFAULT_RHYTHM_NOTE  # Default note for unpitched rhythm
                    # Rhythm hits always retrigger.
                    assignments[start_step] = (1, ms2_note, seg.inst, seg.vol, True, at_token,getattr(seg, "is_legato", 0))
                    # Place NOTE_OFF at end_step; if a later NOTE_ON lands on the same step,
                    # processing order naturally overwrites this with the retrigger NOTE_ON.
                    end_tick = getattr(seg, 'tick_end', None)
                    if end_tick is not None:
                        end_step_idx = end_tick // ticks_per_step
                        if 0 < end_step_idx < total_steps:
                            if assignments.get(end_step_idx, (0,))[0] != 1:
                                assignments[end_step_idx] = (0, 0, seg.inst, seg.vol, False, at_token, 0)
            elif seg.keyon:
                ms2_note = fnum_block_to_ms2_note(seg.fnum, seg.block)
                if ms2_note is not None:
                    is_legato_val = getattr(seg, "is_legato", 0)
                    # OPLL retrigger events are represented by is_legato=0 in pass4.
                    # is_legato=1 means true legato and should not emit NOTE_ON.
                    retrigger = (is_legato_val == 0)
                    assignments[start_step] = (1, ms2_note, seg.inst, seg.vol, retrigger, at_token, is_legato_val)
            else:
                if start_step not in assignments or assignments[start_step][0] == 0:
                    assignments[start_step] = (0, 0, seg.inst, seg.vol, False, at_token,0)
        cur_state: tuple = (0, 0, 0, 0, False, '',0)
        for step in range(total_steps):
            if step in assignments:
                cur_state = assignments[step]
            else:
                if cur_state[4]:
                    # (keyon, note, inst, vol, retrigger, at_token, is_legato)
                    cur_state = (
                        cur_state[0],
                        cur_state[1],
                        cur_state[2],
                        cur_state[3],
                        False,
                        cur_state[5],
                        cur_state[6],
                    )
            grid[(ch, step)] = cur_state
    return grid


def _build_instrument_table(
    segments: dict,
    user_patches: dict,
) -> tuple[list, dict]:
    instruments: list[tuple[bytes, str]] = []
    inst_map: dict[tuple, int] = {}
    def _patch_key(inst: int, user_patches_local: dict) -> tuple:
        if inst == 0:
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
    for ch in range(NUM_CH):
        for seg in segments.get(ch, []):
            if seg.inst == 0:
                try:
                    v_num = int(seg.at_token.lstrip('@v').lstrip('@'))
                    patch_b = user_patches.get(v_num, bytes(8))
                except (ValueError, AttributeError):
                    patch_b = bytes(8)
                key = ('user', bytes(patch_b))
            else:
                key = ('preset', seg.inst)
            _add_instrument(key)
    if not instruments:
        _add_instrument(('preset', 8))
    return instruments, inst_map


def _get_ms2_inst_idx(
    inst: int,
    at_token: str,
    user_patches: dict,
    inst_map: dict,
) -> int:
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
    if user_patches is None:
        user_patches = {}
    total_steps = n_patterns * STEPS_PER_PATTERN
    grid = _build_step_grid(segments, ticks_per_step, total_steps)
    channel_event_lists: list[list] = []
    for _ in range(n_patterns):
        channel_event_lists.append([[] for _ in range(n_channels)])
    for ch in range(n_channels):
        prev_state = (0, 0, 0, 0, False, '',0)
        prev_inst_idx = -1
        prev_vol_ms2 = -1
        for step in range(total_steps):
            pat = step // STEPS_PER_PATTERN
            step_in_pat = step % STEPS_PER_PATTERN
            state = grid.get((ch, step), (0, 0, 0, 0, False, '', 0))
            keyon, ms2_note, inst, vol_opll, retrigger, at_token, is_legato = state
            prev_keyon = prev_state[0]
            prev_note  = prev_state[1]
            vol_ms2 = opll_vol_to_ms2_vol(vol_opll)
            evts_here: list[bytes] = []
            # instrument index
            if inst == 0 and user_patches:
                v_num = _parse_user_v_num(at_token)
                if v_num is not None and v_num in user_patches:
                    inst_idx = ref_count + (v_num - 1)
                else:
                    inst_idx = 0
            else:
                inst_idx = _opll_inst_to_ms2_idx(inst, n_instruments)

            if keyon and ms2_note is not None:
                note_changed = (ms2_note != prev_note) or (not prev_keyon) or retrigger
                inst_changed = (inst_idx != prev_inst_idx)
                vol_changed  = (vol_ms2 != prev_vol_ms2)
                force_inst = (pat == 0 and step_in_pat == 0)

                emit_note = note_changed or inst_changed
                emit_vol  = vol_changed
                emit_inst = inst_changed or force_inst

                # -------------------------
                # LEGATO / NOTE_ON
                # -------------------------
                if emit_note:
                    if is_legato == 1:
                        evts_here.append(bytes([0x02, ms2_note & 0xFF]))  # LEGATO
                        # LEGATO のときは INST/VOL を emit しない
                        emit_inst = False
                        emit_vol  = False
                    else:
                        evts_here.append(bytes([0x00, ms2_note & 0xFF]))  # NOTE_ON

                # -------------------------
                # VOL / INST
                # -------------------------
                if emit_vol:
                    evts_here.append(bytes([0x05, vol_ms2 & 0xFF]))
                    prev_vol_ms2 = vol_ms2

                if emit_inst:
                    evts_here.append(bytes([0x04, inst_idx & 0xFF]))
                    prev_inst_idx = inst_idx
            else:
                inst_changed_off = (inst_idx != prev_inst_idx)
                vol_changed_off  = (vol_ms2 != prev_vol_ms2)

                if prev_keyon or inst_changed_off or vol_changed_off:
                    evts_here.append(bytes([0x01]))
                    if vol_changed_off:
                        evts_here.append(bytes([0x05, vol_ms2 & 0xFF]))
                        prev_vol_ms2 = vol_ms2
                    if inst_changed_off:
                        evts_here.append(bytes([0x04, inst_idx & 0xFF]))
                        prev_inst_idx = inst_idx
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
    if user_patches is None:
        user_patches = {}
    result = []
    for ch in range(n_channels):
        segs = segments.get(ch, [])
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
    _converter = opll_patch_raw if raw_mode else opll_patch_to_opl4
    preset_count = min(n_presets, len(patches))
    instruments_bytes = struct.pack('<H', preset_count)
    for i in range(preset_count):
        patch = bytes(patches[i])[:8]
        opl4 = _converter(patch)
        name = f'Preset {i + 1}'
        instruments_bytes += _build_2op_instrument(opl4, tuning_idx=0, name=name)
    tunings_bytes = build_tunings_chunk_data(1)
    n_tunings = 1
    song_settings = build_song_settings_data(
        description        = library_name[:65],
        n_patterns         = 1,
        n_tunings          = n_tunings,
        n_channels         = 1,
        n_2op_inst         = preset_count,
        n_4op_inst         = 1,
        n_wav_inst         = 1,
        n_pitch_env        = 1,
        timer              = 0,
        timer_divider      = 0x0C00,
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
        + build_patterns_chunks(
            channel_event_lists=[[[]]],
            n_patterns=1,
            n_channels=1,
        )
        + _build_chunk(8, pitch_env_data)
        + _sentinel_chunk()
    )
    return ms2_bytes
 
def build_ms2_file(
    segments: dict,
    voice_csv_path: str | None,
    stem: str,
    bpm: int = 120,
    timer: int = 0,
    base_ms2: str | None = None,
    video: str = 'ntsc',
    debug: bool = False,
    rhythm_instruments: dict | None = None,
    ticks_per_step_override: int | None = None,
) -> bytes:
    """
    MS2ファイル生成: メロディはbase_ms2優先(1～15、index 0～14)、16～はリズム etc.
    """

    from opll_mml import _assign_voice_ids
    voice_table, user_patches, _warnings = _assign_voice_ids(segments, voice_csv_path)

    NUM_CH = 18

    # ==== チューニング/メロディ音色取得 ====
    if base_ms2 is not None:
        tunings_data, instruments_data, n_tunings, melody_count = _load_library_from_ms2(
            base_ms2, max_presets=15  # *index 0～14に15音色がある前提
        )
    else:
        tunings_data, instruments_data, n_tunings, melody_count = _load_reference_library()

    # ==== 既存メロディ音色を抽出 ====
    # instruments_data: chunk3。先頭2バイトは個数、以下30バイトずつ
    n_melody = min(melody_count, 15)
    melody_records = [instruments_data[2+i*30:2+(i+1)*30] for i in range(n_melody)]
    all_instruments = list(melody_records)

    # ==== リズム/追加音色を11byte→30byte形式へ追記 ====
    # MS2_RHYTHM_INSTRUMENTS = [(idx, name, tuning_idx, bytes11), ...]
    for inst in MS2_RHYTHM_INSTRUMENTS:
        idx, name, tuning_idx, bytes11 = inst
        buf = bytearray(30)
        buf[0] = tuning_idx
        # buf[1:3] ← 0
        buf[3:14] = bytes11
        buf[14:30] = (name.encode('ascii', 'replace') + b' ' * 16)[:16]
        all_instruments.append(bytes(buf))

    n_2op_inst = len(all_instruments)

    # ==== User Patch追記 ====
    ref_count = n_2op_inst
    if user_patches:
        for v_num in sorted(user_patches):
            patch_bytes = user_patches[v_num]
            reg = opll_patch_to_opl4(patch_bytes)
            reg_order = [0x20,0x21,0x40,0x41,0x60,0x61,0x80,0x81,0xE0,0xE1,0xC0]
            buf = bytearray(30)
            buf[0] = 0  # tuning
            buf[3:14] = bytes([reg[r]&0xFF for r in reg_order])
            name = f'User Patch v{v_num}'
            buf[14:30] = (name.encode('ascii', 'replace') + b' ' * 16)[:16]
            all_instruments.append(bytes(buf))
    n_2op_inst = len(all_instruments)

    # ==== instrumentsチャンク再構築 ====
    instruments_data = struct.pack('<H', n_2op_inst) + b''.join(all_instruments)

    if debug:
        print(f"[build_ms2_file] base_ms2={base_ms2} melody_count={melody_count} n2op={n_2op_inst}")

    # ==== テンポ・タイミング ====
    if timer == 1:
        freq_hz = _FREQ_PAL
    elif video == 'pal':
        freq_hz = _FREQ_PAL
    else:
        freq_hz = _FREQ_NTSC
    legacy_timer_divider = int(freq_hz * 3840 / bpm)
    legacy_timer_divider = max(1, min(0xFFFF, legacy_timer_divider))
    ticks_per_step_16th = max(1, round(freq_hz * 15 / bpm))

    min_l = None
    for ch in range(NUM_CH):
        for seg in segments.get(ch, []):
            seg_min_l = getattr(seg, 'min_l', None)
            if seg_min_l is None:
                continue
            try:
                seg_min_l_f = float(seg_min_l)
            except (TypeError, ValueError):
                continue
            if seg_min_l_f > 0:
                min_l = seg_min_l_f if min_l is None else min(min_l, seg_min_l_f)

    ticks_per_step = ticks_per_step_16th
    timer_divider = legacy_timer_divider
    if min_l is not None:
        # Use int(min_l) intentionally to keep ticks_per_step integer and
        # preserve the existing contract expected by downstream tick mapping.
        min_l_int = int(min_l)
        ticks_per_step = max(1, min(min_l_int, ticks_per_step_16th))
        ticks_per_step = min(ticks_per_step, 0xFFFF // 256)
        # Song settings timer_divider unit is 1/256 of a VDP tick.
        # Keeping timer_divider = 256 * ticks_per_step preserves 1-step duration.
        timer_divider = 256 * ticks_per_step
        if debug:
            print(
                f"[ticks_per_step] min_l={min_l} int(min_l)={min_l_int} "
                f"legacy={ticks_per_step_16th} → using ticks_per_step={ticks_per_step} "
                f"timer_divider={timer_divider}"
            )

    # ticks_per_step_override が指定されていれば上書き
    if ticks_per_step_override is not None:
        ticks_per_step = max(2, int(ticks_per_step_override))
        timer_divider = 256 * ticks_per_step
        if debug:
            print(
                f"[ticks_per_step] override={ticks_per_step_override} "
                f"→ using ticks_per_step={ticks_per_step} timer_divider={timer_divider}"
            )

    # ==== パターン数自動推定 ====
    last_note_end_tick = 0
    for ch in range(NUM_CH):
        for seg in segments.get(ch, []):
            if seg.keyon:
                last_note_end_tick = max(last_note_end_tick, seg.tick_end)
    if last_note_end_tick == 0:
        for ch in range(NUM_CH):
            for seg in segments.get(ch, []):
                last_note_end_tick = max(last_note_end_tick, seg.tick_end)
    n_patterns = max(1, math.ceil(last_note_end_tick / (ticks_per_step * STEPS_PER_PATTERN)))

    # ==== starting_instruments配列生成 ====
    starting_instruments = _get_starting_instruments(
        segments, NUM_CH, n_2op_inst, ticks_per_step,
        user_patches=user_patches, ref_count=ref_count
    )
    if len(starting_instruments) < NUM_CH:
        starting_instruments += [0] * (NUM_CH - len(starting_instruments))

    # ==== パターン(ノート)格納 ====
    channel_event_lists = segments_to_patterns(
        segments, ticks_per_step, n_patterns, NUM_CH, n_2op_inst,
        user_patches=user_patches, ref_count=ref_count
    )

    # ==== チャンクまとめ ====
    song_settings = build_song_settings_data(
        description=stem[:65],
        n_patterns=n_patterns,
        n_tunings=n_tunings,
        n_channels=NUM_CH,
        n_2op_inst=n_2op_inst,
        starting_instruments=starting_instruments,
        timer=timer,
        timer_divider=timer_divider,
    )
    patterns_chunks = build_patterns_chunks(channel_event_lists, n_patterns, NUM_CH)
    four_op_data    = build_4op_instruments_chunk_data(1)
    wav_data        = build_wav_instruments_chunk_data(1)
    pitch_env_data  = build_pitch_envelopes_chunk_data(1)

    ms2_bytes = (
        MS2_SIGNATURE
        + _build_chunk(1, song_settings)
        + _build_chunk(2, tunings_data)
        + _build_chunk(3, instruments_data)
        + _build_chunk(4, four_op_data)
        + _build_chunk(5, wav_data)
        + patterns_chunks
        + _build_chunk(8, pitch_env_data)
        + _sentinel_chunk()
    )
    return ms2_bytes
