"""
test_ms2_export.py  –  Integration test harness for the vgm2ms2 exporter.

Runs vgm2ms2.py on every VGM found in ms2_tests_input/ and validates the
resulting MS2 file with ms2_validate.  Optionally checks that selected
testcases produce the expected number of patterns and channels.

Usage (pytest):
    python -m pytest py/test_ms2_export.py -v

Usage (standalone):
    python py/test_ms2_export.py
"""

from __future__ import annotations

import os
import sys
import subprocess
import tempfile
import pytest

# Resolve paths relative to the repository root (one level above py/)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VGM2MS2   = os.path.join(_REPO_ROOT, 'vgm2ms2.py')
_INPUTS    = os.path.join(_REPO_ROOT, 'ms2_tests_input')
_OUTPUTS   = os.path.join(_REPO_ROOT, 'outputs')

sys.path.insert(0, os.path.join(_REPO_ROOT, 'py'))
from ms2_validate import validate_ms2_bytes
from ms2_dump import iter_chunks
import struct


def _run_exporter(vgm_path: str, out_dir: str) -> str:
    """Run vgm2ms2.py on *vgm_path*, writing output to *out_dir*.

    Returns the path to the generated .MS2 file.
    Raises AssertionError on non-zero exit code.
    """
    result = subprocess.run(
        [sys.executable, _VGM2MS2, vgm_path, '--outdir', out_dir],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"vgm2ms2.py exited with code {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    stem = os.path.splitext(os.path.basename(vgm_path))[0]
    ms2_path = os.path.join(out_dir, f'{stem}.MS2')
    assert os.path.exists(ms2_path), f"Expected output not found: {ms2_path}"
    return ms2_path


def _get_ms2_info(ms2_bytes: bytes) -> tuple[int, int]:
    """Return (n_patterns, n_channels) from an MS2 file's chunks."""
    n_channels = 0
    n_patterns = 0
    for _offset, ctype, _version, cdata in iter_chunks(ms2_bytes):
        if ctype == 1 and len(cdata) >= 72:
            n_channels = cdata[71]
        elif ctype == 6 and len(cdata) >= 2:
            n_patterns = struct.unpack_from('<H', cdata, 0)[0]
        elif ctype == 0:
            break
    return n_patterns, n_channels


# ---------------------------------------------------------------------------
# Collect all test VGMs
# ---------------------------------------------------------------------------

_all_vgms = sorted(
    os.path.join(_INPUTS, f)
    for f in os.listdir(_INPUTS)
    if f.endswith('.vgm')
) if os.path.isdir(_INPUTS) else []


# ---------------------------------------------------------------------------
# Per-testcase expectations (stem → (min_patterns, max_patterns, n_channels))
# Used as loose bounds; None means "don't check".
# Derived from reference MS2 files and test MML content.
# ---------------------------------------------------------------------------
_EXPECTATIONS: dict[str, tuple[int | None, int | None, int | None]] = {
    'blockb':   (3,    4,    1),   # 1-ch arpeggio scale, ~3 patterns
    'scalech':  (2,    5,    1),   # 1-ch chromatic scale, 2–5 patterns
    '3chtest':  (1,    4,    3),   # 3-ch OPLL output (ch0/1/2 all active)
    'chmix':    (1,    4,    3),   # 3-ch OPLL output (ch0/1/2 all active)
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('vgm_path', _all_vgms, ids=lambda p: os.path.basename(p))
def test_export_and_validate(vgm_path: str, tmp_path: str) -> None:
    """Export a VGM and verify the output MS2 is structurally valid."""
    ms2_path = _run_exporter(vgm_path, str(tmp_path))
    ms2_bytes = open(ms2_path, 'rb').read()

    errors = validate_ms2_bytes(ms2_bytes)
    assert not errors, (
        f"Validation errors for {os.path.basename(vgm_path)}:\n"
        + '\n'.join(f'  {e}' for e in errors)
    )


@pytest.mark.parametrize('stem,exp', list(_EXPECTATIONS.items()))
def test_export_metadata(stem: str, exp: tuple, tmp_path: str) -> None:
    """Check that expected testcases produce the right pattern/channel counts."""
    vgm_path = os.path.join(_INPUTS, f'{stem}.vgm')
    if not os.path.exists(vgm_path):
        pytest.skip(f'{stem}.vgm not found in {_INPUTS}')

    ms2_path = _run_exporter(vgm_path, str(tmp_path))
    ms2_bytes = open(ms2_path, 'rb').read()

    n_patterns, n_channels = _get_ms2_info(ms2_bytes)
    min_pat, max_pat, exp_ch = exp

    if min_pat is not None:
        assert n_patterns >= min_pat, (
            f"{stem}: expected >= {min_pat} patterns, got {n_patterns}"
        )
    if max_pat is not None:
        assert n_patterns <= max_pat, (
            f"{stem}: expected <= {max_pat} patterns, got {n_patterns}"
        )
    if exp_ch is not None:
        assert n_channels == exp_ch, (
            f"{stem}: expected {exp_ch} channels, got {n_channels}"
        )


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import tempfile as _tempfile

    passed = 0
    failed = 0
    errors_list: list[str] = []

    for vgm_path in _all_vgms:
        stem = os.path.splitext(os.path.basename(vgm_path))[0]
        with _tempfile.TemporaryDirectory() as tmp:
            try:
                ms2_path = _run_exporter(vgm_path, tmp)
                ms2_bytes = open(ms2_path, 'rb').read()
                errs = validate_ms2_bytes(ms2_bytes)
                if errs:
                    print(f'FAIL  {stem}: validation errors:')
                    for e in errs:
                        print(f'       {e}')
                    failed += 1
                else:
                    n_pat, n_ch = _get_ms2_info(ms2_bytes)
                    exp = _EXPECTATIONS.get(stem)
                    meta = f'{n_pat} pattern(s), {n_ch} channel(s)'
                    if exp:
                        min_p, max_p, exp_c = exp
                        ok = True
                        if min_p and n_pat < min_p: ok = False
                        if max_p and n_pat > max_p: ok = False
                        if exp_c and n_ch != exp_c: ok = False
                        if not ok:
                            print(f'FAIL  {stem}: metadata mismatch – '
                                  f'{meta} (expected {exp})')
                            failed += 1
                            continue
                    print(f'OK    {stem}: {meta}')
                    passed += 1
            except Exception as exc:
                print(f'ERROR {stem}: {exc}')
                failed += 1

    print(f'\n{passed} passed, {failed} failed out of {passed+failed} tests.')
    sys.exit(0 if failed == 0 else 1)
