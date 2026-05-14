from __future__ import annotations

import os
import struct
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from ms2_dump import iter_chunks
from ms2_writer import (
    MAX_PATTERN_CHUNK_SIZE,
    MS2_SIGNATURE,
    build_patterns_chunk_data,
    build_patterns_chunks,
)


def _iter_pattern_chunks(pattern_chunks_blob: bytes) -> list[bytes]:
    ms2_bytes = MS2_SIGNATURE + pattern_chunks_blob + struct.pack('<BBH', 0, 0, 0)
    return [cdata for _off, ctype, _ver, cdata in iter_chunks(ms2_bytes) if ctype == 6]


def test_build_patterns_chunks_keeps_single_chunk_when_small():
    channel_event_lists = [[[], []], [[], []], [[], []]]
    expected_payload = build_patterns_chunk_data(channel_event_lists, n_patterns=3, n_channels=2)

    chunks_blob = build_patterns_chunks(channel_event_lists, n_patterns=3, n_channels=2)
    pattern_chunks = _iter_pattern_chunks(chunks_blob)

    assert len(pattern_chunks) == 1
    assert pattern_chunks[0] == expected_payload
    assert len(pattern_chunks[0]) <= MAX_PATTERN_CHUNK_SIZE


def test_build_patterns_chunks_splits_when_payload_exceeds_limit():
    n_patterns = 1000
    n_channels = 18
    channel_event_lists = [[[] for _ in range(n_channels)] for _ in range(n_patterns)]

    chunks_blob = build_patterns_chunks(channel_event_lists, n_patterns=n_patterns, n_channels=n_channels)
    pattern_chunks = _iter_pattern_chunks(chunks_blob)

    assert len(pattern_chunks) >= 2
    assert all(len(chunk) <= MAX_PATTERN_CHUNK_SIZE for chunk in pattern_chunks)
    assert sum(struct.unpack_from('<H', chunk, 0)[0] for chunk in pattern_chunks) == n_patterns
