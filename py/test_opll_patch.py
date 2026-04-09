#!/usr/bin/env python3
"""Self-check tests for OPLL user-patch conversion to MGSDRV @v format.

Run directly:  python3 py/test_opll_patch.py
"""
import sys
import os
import io
import textwrap

# Allow running from repo root or from py/ directory
_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)

from opll_mml import (
    _ym2413_patch_to_mgsdrv,
    _user_patch_mml_defs,
    _assign_voice_ids,
    _Segment,
    NUM_CH,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_segments(inst_map: dict) -> dict:
    """Create minimal per-channel segment dicts for testing.

    *inst_map* maps ch -> inst (int).  All other fields are set to trivial
    values; tick_end = tick_start + 1 so segments are non-empty.
    """
    segs: dict = {ch: [] for ch in range(NUM_CH)}
    for ch, inst in inst_map.items():
        s = _Segment(0, 1, 256, 3, inst, 0)
        s.tick_end = 1
        segs[ch].append(s)
    return segs


def _make_voice_csv(rows: list[str]) -> str:
    """Write rows to a temp file and return its path."""
    import tempfile
    fh = tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                     delete=False, newline='\n')
    fh.write('#type,time,ch,ticks,inst,vol,patch_hex\n')
    for row in rows:
        fh.write(row + '\n')
    fh.close()
    return fh.name


# ---------------------------------------------------------------------------
# Test: _ym2413_patch_to_mgsdrv round-trip
# ---------------------------------------------------------------------------

def test_decode_known_patch():
    """Verify decoding of a known patch against hand-computed expected values.

    The known patch corresponds to the MGSDRV @v15 example from the problem
    statement ('Strings / Lead'):

        @v15 = { ; Strings / Lead
        ;       TL FB
                27, 4,
        ; AR DR SL RR KL MT AM VB EG KR DT
          12, 2,15, 0, 0, 1, 0, 0, 1, 0, 0,
           7, 4, 1, 1, 0, 1, 0, 0, 0, 0, 1 }

    YM2413 register encoding of this patch:
      R0 = 0x21  (AM=0 VB=0 EG=1 KR=0 MT=1)
      R1 = 0x1B  (KL=0 TL=27)
      R2 = 0xC2  (AR=12 DR=2)
      R3 = 0xF0  (SL=15 RR=0)
      R4 = 0x01  (AM=0 VB=0 EG=0 KR=0 MT=1)
      R5 = 0x14  (KL=0 DC=1 DM=0 FB=4)
      R6 = 0x74  (AR=7 DR=4)
      R7 = 0x11  (SL=1 RR=1)
    """
    regs = bytes([0x21, 0x1B, 0xC2, 0xF0, 0x01, 0x14, 0x74, 0x11])
    d = _ym2413_patch_to_mgsdrv(regs)

    assert d['tl'] == 27,  f"TL: got {d['tl']} expected 27"
    assert d['fb'] == 4,   f"FB: got {d['fb']} expected 4"
    assert d['mod'] == (12, 2, 15, 0, 0, 1, 0, 0, 1, 0, 0), \
        f"Mod: got {d['mod']}"
    assert d['car'] == (7, 4, 1, 1, 0, 1, 0, 0, 0, 0, 1), \
        f"Car: got {d['car']}"
    print('PASS: test_decode_known_patch')


def test_decode_all_zeros():
    """All-zero patch should decode to all-zero parameters."""
    d = _ym2413_patch_to_mgsdrv(bytes(8))
    assert d['tl'] == 0
    assert d['fb'] == 0
    assert d['mod'] == (0,) * 11
    assert d['car'] == (0,) * 11
    print('PASS: test_decode_all_zeros')


def test_decode_all_bits():
    """All-0xFF patch should decode with expected max/bit values."""
    d = _ym2413_patch_to_mgsdrv(bytes([0xFF] * 8))
    # TL = 0xFF & 0x3F = 63
    assert d['tl'] == 63, f"TL: got {d['tl']}"
    # FB = 0xFF & 0x07 = 7
    assert d['fb'] == 7, f"FB: got {d['fb']}"
    # KL of modulator = (0xFF >> 6) & 3 = 3
    assert d['mod'][4] == 3, f"Mod KL: got {d['mod'][4]}"
    # DM = (0xFF >> 3) & 1 = 1
    assert d['mod'][10] == 1, f"Mod DT: got {d['mod'][10]}"
    # DC = (0xFF >> 4) & 1 = 1
    assert d['car'][10] == 1, f"Car DT: got {d['car'][10]}"
    print('PASS: test_decode_all_bits')


# ---------------------------------------------------------------------------
# Test: _user_patch_mml_defs formatting
# ---------------------------------------------------------------------------

def test_mml_def_formatting():
    """Verify the generated @vNN block matches expected MGSDRV format."""
    regs = bytes([0x21, 0x1B, 0xC2, 0xF0, 0x01, 0x14, 0x74, 0x11])
    lines = _user_patch_mml_defs({15: regs})

    joined = '\n'.join(lines)
    assert '@v15 = {' in joined, 'Missing @v15 header'
    assert ';       TL FB' in joined, 'Missing TL FB comment'
    assert '27, 4,' in joined, 'Missing TL/FB values'
    assert '; AR DR SL RR KL MT AM VB EG KR DT' in joined, 'Missing operator comment'
    # Check both operator rows present
    assert '12,  2, 15,  0,  0,  1,  0,  0,  1,  0,  0,' in joined, \
        'Missing modulator row'
    assert '7,  4,  1,  1,  0,  1,  0,  0,  0,  0,  1 }' in joined, \
        'Missing carrier row'
    print('PASS: test_mml_def_formatting')


# ---------------------------------------------------------------------------
# Test: _assign_voice_ids with synthetic voice CSV
# ---------------------------------------------------------------------------

def test_assign_voice_ids_preset():
    """Preset instruments get @N at_tokens; no user_patches returned."""
    segs = _make_segments({0: 5, 1: 10, 2: 5})
    voice_table, user_patches, warnings = _assign_voice_ids(segs, None)

    for seg in segs[0]:
        assert seg.at_token == '@5', f"ch0 at_token: {seg.at_token}"
    for seg in segs[1]:
        assert seg.at_token == '@10', f"ch1 at_token: {seg.at_token}"
    for seg in segs[2]:
        assert seg.at_token == '@5', f"ch2 at_token: {seg.at_token}"

    assert len(user_patches) == 0, f"Expected no user patches, got {user_patches}"
    print('PASS: test_assign_voice_ids_preset')


def test_assign_voice_ids_user_patch():
    """inst=0 with a voice CSV gets @v1 at_token and user_patches populated."""
    # A known patch: TL=27, FB=4, etc.
    patch_hex = '211bc2f001147411'
    voice_csv = _make_voice_csv([
        f'patch,0.0,-1,0,0,0,{patch_hex}',
    ])

    segs = _make_segments({0: 0})
    voice_table, user_patches, warnings = _assign_voice_ids(segs, voice_csv)
    os.unlink(voice_csv)

    for seg in segs[0]:
        assert seg.at_token == '@v1', f"ch0 at_token: {seg.at_token}"

    assert 1 in user_patches, f"user_patches missing @v1: {user_patches}"
    patch_bytes = user_patches[1]
    assert patch_bytes == bytes.fromhex(patch_hex), \
        f"Patch bytes mismatch: {patch_bytes.hex()} != {patch_hex}"
    print('PASS: test_assign_voice_ids_user_patch')


def test_assign_voice_ids_multiple_patches():
    """Multiple distinct user patches each get a unique @vN token."""
    patch1 = '211bc2f001147411'
    patch2 = '66010c0782d630c7'
    voice_csv = _make_voice_csv([
        f'patch,0.0,-1,0,0,0,{patch1}',
        f'patch,0.001,-1,100,0,0,{patch2}',
    ])

    # ch0 uses inst=0 at tick=0 (patch1), ch1 uses inst=0 at tick=100 (patch2)
    segs: dict = {ch: [] for ch in range(NUM_CH)}
    s0 = _Segment(0, 1, 256, 3, 0, 0); s0.tick_end = 50
    s1 = _Segment(100, 1, 256, 3, 0, 0); s1.tick_end = 150
    segs[0].append(s0)
    segs[1].append(s1)

    voice_table, user_patches, warnings = _assign_voice_ids(segs, voice_csv)
    os.unlink(voice_csv)

    assert s0.at_token == '@v1', f"s0 at_token: {s0.at_token}"
    assert s1.at_token == '@v2', f"s1 at_token: {s1.at_token}"
    assert len(user_patches) == 2, f"Expected 2 user patches, got {len(user_patches)}"
    print('PASS: test_assign_voice_ids_multiple_patches')


def test_assign_voice_ids_same_patch_deduplication():
    """Two channels using the same patch content share the same @vN token."""
    patch1 = '211bc2f001147411'
    voice_csv = _make_voice_csv([
        f'patch,0.0,-1,0,0,0,{patch1}',
    ])

    segs: dict = {ch: [] for ch in range(NUM_CH)}
    s0 = _Segment(0, 1, 256, 3, 0, 0); s0.tick_end = 10
    s1 = _Segment(0, 1, 256, 3, 0, 0); s1.tick_end = 10
    segs[0].append(s0)
    segs[1].append(s1)

    voice_table, user_patches, warnings = _assign_voice_ids(segs, voice_csv)
    os.unlink(voice_csv)

    assert s0.at_token == '@v1'
    assert s1.at_token == '@v1'
    assert len(user_patches) == 1, f"Expected 1 unique patch, got {len(user_patches)}"
    print('PASS: test_assign_voice_ids_same_patch_deduplication')


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    tests = [
        test_decode_known_patch,
        test_decode_all_zeros,
        test_decode_all_bits,
        test_mml_def_formatting,
        test_assign_voice_ids_preset,
        test_assign_voice_ids_user_patch,
        test_assign_voice_ids_multiple_patches,
        test_assign_voice_ids_same_patch_deduplication,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f'FAIL: {t.__name__}: {e}')
            failed += 1
        except Exception as e:
            print(f'ERROR: {t.__name__}: {e}')
            import traceback; traceback.print_exc()
            failed += 1
    if failed:
        print(f'\n{failed}/{len(tests)} tests FAILED')
        sys.exit(1)
    else:
        print(f'\nAll {len(tests)} tests passed.')
