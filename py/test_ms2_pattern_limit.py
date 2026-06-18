from __future__ import annotations

import importlib
import struct
import sys
import types
from types import SimpleNamespace


def _load_ms2_writer_with_stub_opll(monkeypatch):
    fake_opll_mml = types.ModuleType('opll_mml')
    fake_opll_mml.NUM_CH = 18
    fake_opll_mml._build_segments = lambda *args, **kwargs: {}
    fake_opll_mml._opll_note = lambda *args, **kwargs: 60
    fake_opll_mml.detect_num_ch = lambda *args, **kwargs: 18
    fake_opll_mml._assign_voice_ids = lambda *args, **kwargs: ({}, {}, [])
    monkeypatch.setitem(sys.modules, 'opll_mml', fake_opll_mml)

    import ms2_writer

    return importlib.reload(ms2_writer)


def _segments_with_long_note():
    segs = {ch: [] for ch in range(18)}
    segs[0].append(SimpleNamespace(keyon=1, tick_end=9600, min_l=None))
    return segs


def test_build_ms2_file_clamps_pattern_count_to_ms2_limit(monkeypatch, capsys):
    ms2_writer = _load_ms2_writer_with_stub_opll(monkeypatch)
    captured = {}

    monkeypatch.setattr(
        ms2_writer,
        '_load_reference_library',
        lambda: (b'', struct.pack('<H', 1) + (b'\x00' * 30), 1, 1),
    )
    monkeypatch.setattr(ms2_writer, '_get_starting_instruments', lambda *args, **kwargs: [0] * 18)
    monkeypatch.setattr(
        ms2_writer,
        'segments_to_patterns',
        lambda _segments, _ticks_per_step, n_patterns, n_channels, *_args, **_kwargs: (
            captured.setdefault('n_patterns', n_patterns),
            [[[] for _ in range(n_channels)] for _ in range(n_patterns)],
        )[1],
    )
    monkeypatch.setattr(
        ms2_writer,
        'build_song_settings_data',
        lambda **kwargs: (captured.setdefault('song_settings_n_patterns', kwargs['n_patterns']), b'')[1],
    )
    monkeypatch.setattr(ms2_writer, 'build_patterns_chunks', lambda *args, **kwargs: b'')
    monkeypatch.setattr(ms2_writer, '_build_chunk', lambda *args, **kwargs: b'')
    monkeypatch.setattr(ms2_writer, '_sentinel_chunk', lambda *args, **kwargs: b'')

    ms2_writer.build_ms2_file(
        segments=_segments_with_long_note(),
        voice_csv_path=None,
        stem='unit',
        ticks_per_step_override=2,
    )

    out = capsys.readouterr().out
    assert 'build_ms2_file' in out
    assert 'n_patterns=300 exceeds MS2 max (255)' in out
    assert captured['n_patterns'] == ms2_writer.MS2_MAX_PATTERNS
    assert captured['song_settings_n_patterns'] == ms2_writer.MS2_MAX_PATTERNS


def test_build_mgsdrvmml_file_clamps_pattern_count_to_ms2_limit(monkeypatch, capsys):
    ms2_writer = _load_ms2_writer_with_stub_opll(monkeypatch)
    captured = {}

    monkeypatch.setattr(
        ms2_writer,
        '_build_step_grid',
        lambda _segments, _ticks_per_step, total_steps: (
            captured.setdefault('total_steps', total_steps),
            {},
        )[1],
    )
    monkeypatch.setattr(
        ms2_writer,
        '_grid_to_mml_text',
        lambda _grid, n_patterns, *_args: (
            captured.setdefault('n_patterns', n_patterns),
            '; ok',
        )[1],
    )

    ms2_writer.build_mgsdrvmml_file(
        segments=_segments_with_long_note(),
        voice_csv_path=None,
        stem='unit',
        ticks_per_step_override=2,
    )

    out = capsys.readouterr().out
    assert 'build_mgsdrvmml_file' in out
    assert 'n_patterns=300 exceeds MS2 max (255)' in out
    assert captured['n_patterns'] == ms2_writer.MS2_MAX_PATTERNS
    assert captured['total_steps'] == ms2_writer.MS2_MAX_PATTERNS * ms2_writer.STEPS_PER_PATTERN
