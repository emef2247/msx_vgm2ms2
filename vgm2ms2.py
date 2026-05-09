#!/usr/bin/env python3
"""
vgm2ms2.py - Top-level CLI: VGM binary → MS2 (MoonSound Music Studio) binary

Produces an OPLL-only .MS2 file for playback in MS² (MoonSound Music Studio).
Only the YM2413 (OPLL) FM channels are exported; PSG and SCC are ignored.

Usage:
    python vgm2ms2.py <vgm_file> [--outdir <dir>] [--bpm <bpm>] [--debug]
    python vgm2ms2.py --export-inst-libraries <output_dir>

Arguments:
    vgm_file                    Path to the input VGM file.
    --outdir <dir>              Output directory (default: outputs/<song_stem>/).
    --bpm <bpm>                 Song tempo in BPM (default: 120).
    --timer <0|1>               0 = VDP timer (default), 1 = 50 Hz timer.
    --video <ntsc|pal>          Video system for VDP timer (default: ntsc).
                                Ignored when --timer 1.
    --base-ms2 <path>           MS2 file to use as the base preset instrument
                                library for song export.  The first 16
                                instruments from this file map to OPLL presets
                                1..16.  Instruments beyond index 15 are
                                ignored; user patches are appended after them.
    --debug                     Print extra info and dump the generated MS2
                                structure.
    --compare                   Also run ms2_dump.py --compare against
                                ms2_example/TEST.MS2.
    --export-inst-libraries <dir>
                                Generate instrument-only MS2 files for all
                                built-in OPLL preset tables and write them to
                                <dir>.  Does not require a VGM input file.
    --inst-lib-mode {converted,raw}
                                Conversion mode for --export-inst-libraries
                                (default: converted).
                                  converted – use opll_patch_to_opl4() which
                                    maps each OPLL register field to its OPL4
                                    counterpart (existing behaviour).
                                  raw – use opll_patch_raw() which packs the
                                    8-byte OPLL patch bytes directly into the
                                    OPL4 chunk-3 register fields with no
                                    additional conversion tables or scaling.
                                    Useful for experimentation when the
                                    converted output does not sound as expected.

Example:
    python vgm2ms2.py "inputs/ym2413/Ys-SMS-FM/Ys (FM) - 01 - Feena.vgm" --debug
    python vgm2ms2.py "song.vgm" --base-ms2 ms2_reference/OP2/YMVOPLL.MS2
    python vgm2ms2.py --export-inst-libraries out/inst_libraries/
    python vgm2ms2.py --export-inst-libraries out/raw/ --inst-lib-mode raw
"""

import sys
import os
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, 'py'))

from vgm_reader import parse_vgm
from opll_mml import _build_segments, _assign_voice_ids, _assign_voice_ids
from ms2_writer import build_ms2_file, build_instrument_library_ms2
from ms2_dump import dump_ms2, compare_signatures
from ms2_validate import validate_ms2_bytes


def export_inst_libraries(output_dir: str, raw_mode: bool = False) -> None:
    """Generate one instrument-only MS2 file per built-in OPLL preset table.

    Output filenames follow 8.3 naming as defined in opll_presets.PRESET_LIBRARY_MAP.

    Args:
        output_dir: Directory in which to write the generated .MS2 files.
        raw_mode:   When ``True``, use the "raw" packing mode (direct byte
                    mapping via :func:`ms2_writer.opll_patch_raw`).
                    When ``False`` (default), use the "converted" mode
                    (:func:`ms2_writer.opll_patch_to_opl4`).
    """
    from opll_presets import PRESET_LIBRARY_MAP
    os.makedirs(output_dir, exist_ok=True)
    mode_label = 'raw' if raw_mode else 'converted'
    for preset_key, (table, filename) in PRESET_LIBRARY_MAP.items():
        patches = [bytes(row) for row in table[:16]]
        ms2_bytes = build_instrument_library_ms2(
            patches=patches,
            library_name=preset_key[:65],
            n_presets=16,
            raw_mode=raw_mode,
        )
        out_path = os.path.join(output_dir, filename)
        with open(out_path, 'wb') as f:
            f.write(ms2_bytes)
        print(f"  {filename}  ({len(ms2_bytes)} bytes)  [{preset_key}]  mode={mode_label}")


def main():
    parser = argparse.ArgumentParser(
        description='Convert VGM (OPLL/YM2413) to MS2 binary for MoonSound Music Studio'
    )
    parser.add_argument('vgm_file', nargs='?', default=None, help='Input VGM file')
    parser.add_argument(
        '--outdir', default=None,
        help='Output directory (default: outputs/<song_stem>/)'
    )
    parser.add_argument(
        '--bpm', type=int, default=120,
        help='Song tempo in BPM (default: 120)'
    )
    parser.add_argument(
        '--timer', type=int, default=0, choices=[0, 1],
        help='0 = VDP timer (default), 1 = 50 Hz timer'
    )
    parser.add_argument(
        '--base-ms2', default=None, metavar='PATH',
        help=(
            'Path to an MS2 file to use as the base preset instrument library. '
            'The first 16 instruments map to OPLL presets 1..16.'
        ),
    )

    parser.add_argument(
        '--video', default='ntsc', choices=['ntsc', 'pal'],
        help='Video system: ntsc (default) or pal; only used when --timer 0',
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='Print extra information and dump the generated MS2 structure'
    )
    parser.add_argument(
        '--compare', action='store_true',
        help='Compare generated file against ms2_example/TEST.MS2'
    )
    parser.add_argument(
        '--no-validate', dest='validate', action='store_false', default=True,
        help='Skip post-build validation of Patterns chunk (validation is on by default)'
    )
    parser.add_argument(
        '--export-inst-libraries', metavar='DIR', default=None,
        help=(
            'Generate instrument-only MS2 files for all built-in OPLL preset'
            ' tables and write them to DIR.  No VGM input is required.'
        ),
    )
    parser.add_argument(
        '--inst-lib-mode', choices=['converted', 'raw'], default='converted',
        metavar='{converted,raw}',
        help=(
            'Conversion mode for --export-inst-libraries (default: converted). '
            '"converted" maps OPLL patch bytes to OPL4 registers using '
            'opll_patch_to_opl4() (existing behaviour). '
            '"raw" packs bytes directly into the OPL4 chunk-3 register fields '
            'using opll_patch_raw() with no additional conversion tables or '
            'scaling — useful for experimentation.'
        ),
    )
    args = parser.parse_args()

    # ── Instrument-library export mode ────────────────────────────────────
    if args.export_inst_libraries is not None:
        raw_mode = (args.inst_lib_mode == 'raw')
        print(f"Exporting instrument libraries to: {args.export_inst_libraries}  (mode={args.inst_lib_mode})")
        export_inst_libraries(args.export_inst_libraries, raw_mode=raw_mode)
        print("Done.")
        return

    # ── Normal song-export mode ───────────────────────────────────────────
    if args.vgm_file is None:
        parser.error('vgm_file is required unless --export-inst-libraries is specified')

    vgm_path = args.vgm_file
    if not os.path.isfile(vgm_path):
        print(f"ERROR: VGM file not found: {vgm_path}", file=sys.stderr)
        sys.exit(1)

    if args.base_ms2 and not os.path.isfile(args.base_ms2):
        print(f"ERROR: --base-ms2 file not found: {args.base_ms2}", file=sys.stderr)
        sys.exit(1)

    # ── Derive output directory and stem ─────────────────────────────────
    base_name = os.path.splitext(os.path.basename(vgm_path))[0]

    if args.outdir:
        output_dir = args.outdir
    else:
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'outputs',
            base_name,
        )
    os.makedirs(output_dir, exist_ok=True)

    # ── Parse VGM → trace CSVs ───────────────────────────────────────────
    print(f"Parsing VGM: {vgm_path}")
    (
        _psg_log, _scc_log,
        _psg_trace, _scc_trace,
        _opll_log, opll_trace_csv,
        opll_voice_csv, _opll_regs_csv,
    ) = parse_vgm(vgm_path, output_dir)

    print(f"  OPLL trace CSV: {opll_trace_csv}")
    print(f"  OPLL voice CSV: {opll_voice_csv}")

    # ── Build OPLL segments ───────────────────────────────────────────────
    print("Building OPLL segments …")
    num_ch = 18
    #segments = _build_segments(opll_trace_csv)
    #segments = _build_segments_no_rhythm(opll_trace_csv)
    segments, bpm_detected = _build_segments(opll_trace_csv, include_rhythm=True)
    n_segs = sum(len(v) for v in segments.values())
    print(f"  {n_segs} segments across {num_ch} channels")
    
    # ── Generate MS2 binary ───────────────────────────────────────────────
    print(f"Generating MS2 (BPM={bpm_detected}, timer={args.timer}) …")
    if args.base_ms2:
        print(f"  Using base instrument library: {args.base_ms2}")
    ms2_bytes = build_ms2_file(
        segments       = segments,
        voice_csv_path = opll_voice_csv,
        stem           = base_name,
        bpm            = bpm_detected,
        timer          = args.timer,
        base_ms2       = args.base_ms2,
        video          = args.video,
        debug          = args.debug,
    )

    # ── Write output file ─────────────────────────────────────────────────
    out_name = base_name + '.MS2'
    out_path = os.path.join(output_dir, out_name)
    with open(out_path, 'wb') as f:
        f.write(ms2_bytes)

    print(f"Written: {out_path}  ({len(ms2_bytes)} bytes)")

    # ── Optional: validate Patterns chunk structure ───────────────────────
    if args.validate:
        errors = validate_ms2_bytes(ms2_bytes)
        if errors:
            print(f"\nVALIDATION FAILED ({len(errors)} error(s)):")
            for e in errors:
                print(f"  {e}")
            print("Aborting: generated file is structurally invalid for MS².")
            # Remove the invalid file so the user doesn't accidentally load it
            try:
                os.remove(out_path)
            except OSError:
                pass
            sys.exit(1)
        else:
            print("Validation OK: Patterns chunk is structurally valid.")

    # ── Optional: dump structure ──────────────────────────────────────────
    if args.debug:
        print()
        print('=' * 60)
        print('MS2 structure dump:')
        print('=' * 60)
        dump_ms2(out_path, show_pattern_bytes=True, max_patterns=2)

    # ── Optional: compare with TEST.MS2 ──────────────────────────────────
    if args.compare:
        test_ms2 = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'ms2_example', 'TEST.MS2'
        )
        if os.path.isfile(test_ms2):
            print()
            print('Comparing with TEST.MS2:')
            compare_signatures(out_path, test_ms2)
        else:
            print(f"WARNING: TEST.MS2 not found at {test_ms2}", file=sys.stderr)


if __name__ == '__main__':
    main()
