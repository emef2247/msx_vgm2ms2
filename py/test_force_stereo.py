"""
test_force_stereo.py – Unit tests for the --force_stereo option.

Tests cover:
- build_song_settings_data writes correct pan bytes when channel_pan is given
- Without channel_pan, pan bytes remain zero (backward compat)
- build_ms2_file assigns L/R alternating pan to active channels when force_stereo=True
- build_ms2_file leaves pan at zero when force_stereo=False (default)
"""

from __future__ import annotations

import importlib
import struct
import sys
import types
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_ms2_writer_with_stub_opll(monkeypatch):
    """Reload ms2_writer with a stubbed opll_mml module to avoid heavy deps."""
    fake_opll_mml = types.ModuleType('opll_mml')
    fake_opll_mml.NUM_CH = 18
    fake_opll_mml._build_segments = lambda *args, **kwargs: {}
    fake_opll_mml._opll_note = lambda *args, **kwargs: 60
    fake_opll_mml.detect_num_ch = lambda *args, **kwargs: 18
    fake_opll_mml._assign_voice_ids = lambda *args, **kwargs: ({}, {}, [])
    monkeypatch.setitem(sys.modules, 'opll_mml', fake_opll_mml)

    import ms2_writer
    return importlib.reload(ms2_writer)


def _get_pan_bytes(ms2_bytes: bytes) -> bytes:
    """Extract the 42 stereo-pan bytes from the Song Settings chunk (offset 159)."""
    # Song Settings chunk (type 1) starts after 8-byte signature.
    # Chunk header: 1-byte type, 1-byte version, 2-byte size → 4 bytes header
    pos = 8
    while pos + 4 <= len(ms2_bytes):
        chunk_type = ms2_bytes[pos]
        chunk_size = struct.unpack_from('<H', ms2_bytes, pos + 2)[0]
        if chunk_type == 1:
            # Song Settings data starts at pos+4
            data = ms2_bytes[pos + 4: pos + 4 + chunk_size]
            return data[159:201]  # 42 pan bytes
        pos += 4 + chunk_size
    return b''


def _make_segment(tick_end: int = 100) -> SimpleNamespace:
    return SimpleNamespace(keyon=1, tick_end=tick_end, min_l=None)


# ---------------------------------------------------------------------------
# Tests: build_song_settings_data
# ---------------------------------------------------------------------------

def test_song_settings_pan_default_is_zero():
    """Without channel_pan, all 42 pan bytes should be 0x00."""
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    import ms2_writer

    data = ms2_writer.build_song_settings_data(
        description='test',
        n_patterns=1,
        n_tunings=1,
        n_channels=18,
        n_2op_inst=1,
    )
    pan_bytes = data[159:201]
    assert all(b == 0 for b in pan_bytes), f"Expected all zeros, got {list(pan_bytes)}"


def test_song_settings_pan_written_correctly():
    """channel_pan values should appear at offset 159 as signed bytes."""
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    import ms2_writer

    # Alternating L=-7, R=+7 for 4 channels, rest center
    pans = [-7, 7, -7, 7] + [0] * 14
    data = ms2_writer.build_song_settings_data(
        description='test',
        n_patterns=1,
        n_tunings=1,
        n_channels=18,
        n_2op_inst=1,
        channel_pan=pans,
    )
    pan_bytes = data[159:201]
    # -7 stored as 0xF9 (signed byte via & 0xFF), +7 as 0x07
    assert pan_bytes[0] == (-7 & 0xFF), f"CH0 pan wrong: {pan_bytes[0]}"
    assert pan_bytes[1] == 7, f"CH1 pan wrong: {pan_bytes[1]}"
    assert pan_bytes[2] == (-7 & 0xFF), f"CH2 pan wrong: {pan_bytes[2]}"
    assert pan_bytes[3] == 7, f"CH3 pan wrong: {pan_bytes[3]}"
    # unused channels remain 0
    for i in range(4, 18):
        assert pan_bytes[i] == 0, f"CH{i} pan should be 0, got {pan_bytes[i]}"


def test_song_settings_pan_clamped():
    """Values outside -7..+7 should be clamped before storing."""
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    import ms2_writer

    pans = [-99, 99] + [0] * 16
    data = ms2_writer.build_song_settings_data(
        description='test',
        n_patterns=1,
        n_tunings=1,
        n_channels=18,
        n_2op_inst=1,
        channel_pan=pans,
    )
    pan_bytes = data[159:201]
    assert pan_bytes[0] == (-7 & 0xFF), f"Expected -7 clamped, got {pan_bytes[0]}"
    assert pan_bytes[1] == 7, f"Expected +7 clamped, got {pan_bytes[1]}"


# ---------------------------------------------------------------------------
# Tests: build_ms2_file with force_stereo
# ---------------------------------------------------------------------------

def test_build_ms2_file_force_stereo_assigns_lr(monkeypatch):
    """force_stereo=True: active channels get alternating L/R pan in output."""
    ms2_writer = _load_ms2_writer_with_stub_opll(monkeypatch)

    monkeypatch.setattr(
        ms2_writer,
        '_load_reference_library',
        lambda: (
            ms2_writer.build_tunings_chunk_data(1),
            struct.pack('<H', 1) + (b'\x00' * 30),
            1, 1,
        ),
    )
    monkeypatch.setattr(ms2_writer, '_get_starting_instruments', lambda *a, **kw: [0] * 18)
    monkeypatch.setattr(
        ms2_writer,
        'segments_to_patterns',
        lambda *a, **kw: [[[] for _ in range(18)] for _ in range(1)],
    )

    # Segments: ch 2 and ch 5 are active
    segs = {ch: [] for ch in range(18)}
    segs[2].append(_make_segment())
    segs[5].append(_make_segment())

    ms2_bytes = ms2_writer.build_ms2_file(
        segments=segs,
        voice_csv_path=None,
        stem='test',
        force_stereo=True,
        ticks_per_step_override=2,
    )

    pan = _get_pan_bytes(ms2_bytes)
    # CH2 → rank 0 → L (-7 = 0xF9)
    # CH5 → rank 1 → R (+7 = 0x07)
    assert pan[2] == (-7 & 0xFF), f"CH2 should be L(-7), got {pan[2]}"
    assert pan[5] == 7, f"CH5 should be R(+7), got {pan[5]}"
    # Other active-channels are 0 (not in segs)
    assert pan[0] == 0
    assert pan[1] == 0


def test_build_ms2_file_no_force_stereo_pan_zero(monkeypatch):
    """force_stereo=False (default): all channels have center pan (0)."""
    ms2_writer = _load_ms2_writer_with_stub_opll(monkeypatch)

    monkeypatch.setattr(
        ms2_writer,
        '_load_reference_library',
        lambda: (
            ms2_writer.build_tunings_chunk_data(1),
            struct.pack('<H', 1) + (b'\x00' * 30),
            1, 1,
        ),
    )
    monkeypatch.setattr(ms2_writer, '_get_starting_instruments', lambda *a, **kw: [0] * 18)
    monkeypatch.setattr(
        ms2_writer,
        'segments_to_patterns',
        lambda *a, **kw: [[[] for _ in range(18)] for _ in range(1)],
    )

    segs = {ch: [] for ch in range(18)}
    segs[2].append(_make_segment())
    segs[5].append(_make_segment())

    ms2_bytes = ms2_writer.build_ms2_file(
        segments=segs,
        voice_csv_path=None,
        stem='test',
        force_stereo=False,
        ticks_per_step_override=2,
    )

    pan = _get_pan_bytes(ms2_bytes)
    assert all(b == 0 for b in pan), f"Expected all-zero pan, got {list(pan)}"


def test_build_ms2_file_force_stereo_three_channels(monkeypatch):
    """Three active channels get L, R, L assignments."""
    ms2_writer = _load_ms2_writer_with_stub_opll(monkeypatch)

    monkeypatch.setattr(
        ms2_writer,
        '_load_reference_library',
        lambda: (
            ms2_writer.build_tunings_chunk_data(1),
            struct.pack('<H', 1) + (b'\x00' * 30),
            1, 1,
        ),
    )
    monkeypatch.setattr(ms2_writer, '_get_starting_instruments', lambda *a, **kw: [0] * 18)
    monkeypatch.setattr(
        ms2_writer,
        'segments_to_patterns',
        lambda *a, **kw: [[[] for _ in range(18)] for _ in range(1)],
    )

    segs = {ch: [] for ch in range(18)}
    segs[0].append(_make_segment())
    segs[3].append(_make_segment())
    segs[7].append(_make_segment())

    ms2_bytes = ms2_writer.build_ms2_file(
        segments=segs,
        voice_csv_path=None,
        stem='test',
        force_stereo=True,
        ticks_per_step_override=2,
    )

    pan = _get_pan_bytes(ms2_bytes)
    # CH0 → rank 0 → L (-7)
    # CH3 → rank 1 → R (+7)
    # CH7 → rank 2 → L (-7)
    assert pan[0] == (-7 & 0xFF), f"CH0 should be L(-7), got {pan[0]}"
    assert pan[3] == 7, f"CH3 should be R(+7), got {pan[3]}"
    assert pan[7] == (-7 & 0xFF), f"CH7 should be L(-7), got {pan[7]}"
