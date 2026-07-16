"""Regression tests for same-step MS2 event ordering."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

from ms2_writer import STEPS_PER_PATTERN, _encode_channel_in_pattern, segments_to_patterns


def _make_segment(
    tick_start: int,
    *,
    keyon: int,
    inst: int,
    vol: int,
    fnum: int = 172,
    block: int = 3,
    tick_end: int | None = None,
    is_legato: int = 0,
):
    return SimpleNamespace(
        ev_type='instVol',
        tick_start=tick_start,
        tick_end=tick_start if tick_end is None else tick_end,
        keyon=keyon,
        fnum=fnum,
        block=block,
        inst=inst,
        vol=vol,
        at_token='',
        is_legato=is_legato,
    )


def _event_opcodes_at_step(channel_event_lists, step_in_pat: int) -> list[int]:
    for step, evts in channel_event_lists[0][0]:
        if step == step_in_pat:
            return [evt[0] for evt in evts]
    return []


def _decode_step_events(encoded: bytes, target_step: int) -> list[int]:
    step = 0
    pos = 0
    events: list[int] = []
    while pos < len(encoded) and step < STEPS_PER_PATTERN:
        while pos < len(encoded) and not (encoded[pos] & 0x80):
            opcode = encoded[pos]
            events.append(opcode)
            pos += 1
            if opcode in (0x00, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0C):
                pos += 1
            elif opcode == 0x09:
                pos += 2
        if pos >= len(encoded):
            break
        duration = encoded[pos] & 0x7F
        if step == target_step:
            return events
        pos += 1
        step += duration
        events = []
    return []


def test_same_step_keyon_events_are_inst_scale_vol_ordered():
    segments = {
        0: [
            _make_segment(0, keyon=1, inst=1, vol=2),
            _make_segment(4, keyon=1, inst=2, vol=5),
        ],
    }

    channel_event_lists = segments_to_patterns(
        segments,
        ticks_per_step=1,
        n_patterns=1,
        n_channels=1,
        n_instruments=15,
    )

    assert _event_opcodes_at_step(channel_event_lists, 4) == [0x04, 0x00, 0x05]

    encoded = _encode_channel_in_pattern(channel_event_lists[0][0])
    assert _decode_step_events(encoded, 4) == [0x04, 0x00, 0x05]


def test_same_step_note_off_events_keep_inst_before_vol():
    segments = {
        0: [
            _make_segment(0, keyon=1, inst=1, vol=2),
            _make_segment(4, keyon=0, inst=2, vol=5),
        ],
    }

    channel_event_lists = segments_to_patterns(
        segments,
        ticks_per_step=1,
        n_patterns=1,
        n_channels=1,
        n_instruments=15,
    )

    assert _event_opcodes_at_step(channel_event_lists, 4) == [0x01, 0x04, 0x05]

    encoded = _encode_channel_in_pattern(channel_event_lists[0][0])
    assert _decode_step_events(encoded, 4) == [0x01, 0x04, 0x05]
