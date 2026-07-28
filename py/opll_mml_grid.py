from __future__ import annotations

import argparse
import math
import os

from vgm_reader import parse_vgm
from opll_mml import (
    _build_segments,
    _assign_voice_ids,
    _user_patch_mml_defs,
    _voice_table_comments,
    parse_opll_regs_for_rhythm,
    NUM_CH,
    RHYTHM_CH_MAP,
    RHYTHM_VOICE_ID_MAP,
)
from ms2_writer import (
    _build_step_grid,
    fnum_block_to_ms2_note,
    STEPS_PER_PATTERN,
    TICKS_PER_STEP_75BPM,
    _FREQ_NTSC,
)
from mml_utils import track_id_to_mgsdrv, estimate_mml_used, estimate_alloc, compress_mml_text
from segment_utils import attach_grid_info

CH_OFFSET = 9
_MELODY_CHS = tuple(range(6))
_RHYTHM_CHS = (
    RHYTHM_CH_MAP["bd"],
    RHYTHM_CH_MAP["sd"],
    RHYTHM_CH_MAP["tom"],
    RHYTHM_CH_MAP["tc"],
    RHYTHM_CH_MAP["hh"],
)
_RHYTHM_SYMBOLS = (
    (RHYTHM_CH_MAP["bd"], "b"),
    (RHYTHM_CH_MAP["sd"], "s"),
    (RHYTHM_CH_MAP["tom"], "m"),
    (RHYTHM_CH_MAP["tc"], "c"),
    (RHYTHM_CH_MAP["hh"], "h"),
)

# keep import referenced for static analyzers / parity with required imports
_ = (RHYTHM_VOICE_ID_MAP, fnum_block_to_ms2_note, TICKS_PER_STEP_75BPM)

_SCALE_NAMES = ['c', 'c+', 'd', 'd+', 'e', 'f', 'f+', 'g', 'g+', 'a', 'a+', 'b']
_STEP_LENGTH_TABLE = [16, 12, 8, 6, 4, 3, 2, 1]


def _ms2_note_to_octave_scale(ms2_note: int) -> tuple[int, str]:
    octave = ms2_note // 12 + 1
    scale = _SCALE_NAMES[ms2_note % 12]
    return octave, scale


def _opll_vol_to_mml_v(opll_vol: int) -> int:
    return 15 - max(0, min(15, int(opll_vol)))


def _steps_to_lengths(step_count: int) -> list[str]:
    remain = max(0, int(step_count))
    parts: list[str] = []
    while remain > 0:
        for size in _STEP_LENGTH_TABLE:
            if size <= remain:
                parts.append('8.' if size == 3 else '4.' if size == 6 else '2.' if size == 12 else str(16 // size))
                remain -= size
                break
    return parts or ['1']


def _octave_change_token(prev_octave: int | None, next_octave: int) -> str:
    if prev_octave is None:
        return f'o{next_octave}'
    diff = next_octave - prev_octave
    if 0 < diff <= 3:
        return '>' * diff
    if -3 <= diff < 0:
        return '<' * (-diff)
    if diff == 0:
        return ''
    return f'o{next_octave}'


def _emit_note_token(scale: str, steps: int) -> str:
    lengths = _steps_to_lengths(steps)
    token = f'{scale}{lengths[0]}'
    for ln in lengths[1:]:
        token += f'&{scale}{ln}'
    return token


def _emit_rest_tokens(steps: int) -> list[str]:
    return [f'r{ln}' for ln in _steps_to_lengths(steps)]


def _build_melody_track_tokens(
    grid: dict,
    ch: int,
    step_begin: int,
    step_end: int,
    state: dict,
) -> list[str]:
    tokens: list[str] = []
    step = step_begin
    while step < step_end:
        keyon, ms2_note, _inst, vol, retrigger, at_token, _is_legato = grid.get(
            (ch, step),
            (0, 0, 0, 15, False, '', 0),
        )

        if keyon:
            run = 1
            while step + run < step_end:
                n_keyon, n_note, _ni, _nv, n_retrigger, _nat, _nl = grid.get(
                    (ch, step + run),
                    (0, 0, 0, 15, False, '', 0),
                )
                if n_keyon and n_note == ms2_note and not n_retrigger:
                    run += 1
                else:
                    break

            if at_token and at_token != state['at_token']:
                tokens.append(at_token)
                state['at_token'] = at_token

            mml_v = _opll_vol_to_mml_v(vol)
            if state['mml_v'] != mml_v:
                tokens.append(f'v{mml_v}')
                state['mml_v'] = mml_v

            octave, scale = _ms2_note_to_octave_scale(ms2_note)
            oct_tok = _octave_change_token(state['octave'], octave)
            if oct_tok:
                tokens.append(oct_tok)
            state['octave'] = octave

            tokens.append(_emit_note_token(scale, run))
            step += run
            continue

        run = 1
        while step + run < step_end:
            n_keyon = grid.get((ch, step + run), (0, 0, 0, 15, False, '', 0))[0]
            if n_keyon:
                break
            run += 1
        tokens.extend(_emit_rest_tokens(run))
        step += run

    return tokens


def _build_rhythm_track_tokens(grid: dict, step_begin: int, step_end: int) -> list[str]:
    symbols: list[str] = []
    for step in range(step_begin, step_end):
        on_symbols = []
        for ch, symbol in _RHYTHM_SYMBOLS:
            keyon = grid.get((ch, step), (0, 0, 0, 15, False, '', 0))[0]
            if keyon:
                on_symbols.append(symbol)
        symbols.append(''.join(on_symbols) if on_symbols else 'r')

    tokens: list[str] = []
    i = 0
    while i < len(symbols):
        cur = symbols[i]
        run = 1
        while i + run < len(symbols) and symbols[i + run] == cur:
            run += 1
        if cur == 'r':
            tokens.extend(_emit_rest_tokens(run))
        else:
            for ln in _steps_to_lengths(run):
                tokens.append(f'{cur}{ln}')
        i += run
    return tokens


def _grid_to_pattern_mml(
    grid: dict,
    n_patterns: int,
    stem: str,
    user_patches: dict,
    voice_table: dict,
    warnings: list[str],
) -> str:
    track_lines: dict[str, list[str]] = {track_id_to_mgsdrv(ch + CH_OFFSET): [] for ch in _MELODY_CHS}
    track_lines[track_id_to_mgsdrv(15)] = []

    melody_states = {
        ch: {'at_token': None, 'mml_v': None, 'octave': None}
        for ch in _MELODY_CHS
    }

    body_lines: list[str] = []
    for pat in range(n_patterns):
        body_lines.append(f'; === Pattern {pat} ===')
        step_begin = pat * STEPS_PER_PATTERN
        step_end = step_begin + STEPS_PER_PATTERN

        for ch in _MELODY_CHS:
            track_id = track_id_to_mgsdrv(ch + CH_OFFSET)
            tokens = _build_melody_track_tokens(grid, ch, step_begin, step_end, melody_states[ch])
            if not tokens:
                tokens = ['r1']
            line = f"{track_id} {' '.join(tokens)}"
            body_lines.append(line)
            track_lines[track_id].append(line)

        rhythm_track_id = track_id_to_mgsdrv(15)
        rhythm_tokens = _build_rhythm_track_tokens(grid, step_begin, step_end)
        if not rhythm_tokens:
            rhythm_tokens = ['r1']
        rhythm_line = f"{rhythm_track_id} {' '.join(rhythm_tokens)}"
        body_lines.append(rhythm_line)
        track_lines[rhythm_track_id].append(rhythm_line)

    lines: list[str] = []
    lines.append(';[name=opll]')
    lines.append('#opll_mode 1')
    lines.append('#tempo 225')
    lines.append(f'#title {{ "{stem}" }}')

    for ch in _MELODY_CHS:
        track_id = track_id_to_mgsdrv(ch + CH_OFFSET)
        lines.append(f'#alloc {track_id}={estimate_alloc(estimate_mml_used(track_lines[track_id]))}')
    rhythm_track_id = track_id_to_mgsdrv(15)
    lines.append(f'#alloc {rhythm_track_id}={estimate_alloc(estimate_mml_used(track_lines[rhythm_track_id]))}')

    if user_patches:
        lines.append('')
        lines.extend(_user_patch_mml_defs(user_patches))
    if voice_table:
        lines.extend(_voice_table_comments(voice_table))
    if warnings:
        lines.extend(warnings)

    lines.append('')
    lines.extend(body_lines)
    lines.append('')
    return compress_mml_text('\n'.join(lines) + '\n')


def _compute_ticks_per_step(
    segments: dict,
    bpm_detected: int,
    ticks_per_step_override: int | None,
) -> int:
    bpm = max(1, int(bpm_detected) if bpm_detected else 75)
    ticks_per_step_16th = max(1, round(_FREQ_NTSC * 15 / bpm))

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
    if min_l is not None:
        ticks_per_step = max(1, min(int(min_l), ticks_per_step_16th))

    if ticks_per_step_override is not None:
        ticks_per_step = max(2, int(ticks_per_step_override))
    return ticks_per_step


def _build_grid_mml_from_inputs(
    trace_csv: str,
    voice_csv: str | None,
    regs_csv: str | None,
    output_dir: str,
    stem: str,
    ticks_per_step_override: int | None,
) -> str:
    rhythm_timeline = parse_opll_regs_for_rhythm(regs_csv) if regs_csv and os.path.exists(regs_csv) else None
    segments_by_ch, bpm_detected = _build_segments(
        trace_csv,
        include_rhythm=True,
        rhythm_reg_timeline=rhythm_timeline,
        ticks_per_step_override=ticks_per_step_override,
    )
    voice_table, user_patches, warnings = _assign_voice_ids(segments_by_ch, voice_csv)

    ticks_per_step = _compute_ticks_per_step(segments_by_ch, bpm_detected, ticks_per_step_override)
    attach_grid_info(segments_by_ch, ticks_per_step)

    last_tick = 0
    for ch_segs in segments_by_ch.values():
        for seg in ch_segs:
            last_tick = max(last_tick, int(getattr(seg, 'tick_end', 0) or 0))
    n_patterns = max(1, math.ceil(last_tick / (ticks_per_step * STEPS_PER_PATTERN)))
    total_steps = n_patterns * STEPS_PER_PATTERN

    grid = _build_step_grid(segments_by_ch, ticks_per_step, total_steps)
    mml_text = _grid_to_pattern_mml(grid, n_patterns, stem, user_patches, voice_table, warnings)

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f'{stem}.opll.grid.mml')
    with open(out_path, 'w', newline='\n') as fh:
        fh.write(mml_text)
    return out_path


def process_vgm_to_mml_grid(
    vgm_path: str,
    output_dir: str | None = None,
    ticks_per_step_override: int | None = None,
) -> str:
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(vgm_path))

    (
        _psg_log,
        _scc_log,
        _psg_trace,
        _scc_trace,
        _opll_log,
        opll_trace_csv,
        opll_voice_csv,
        opll_regs_csv,
    ) = parse_vgm(vgm_path, output_dir)

    stem = os.path.splitext(os.path.basename(vgm_path))[0]
    return _build_grid_mml_from_inputs(
        trace_csv=opll_trace_csv,
        voice_csv=opll_voice_csv if os.path.exists(opll_voice_csv) else None,
        regs_csv=opll_regs_csv if os.path.exists(opll_regs_csv) else None,
        output_dir=output_dir,
        stem=stem,
        ticks_per_step_override=ticks_per_step_override,
    )


def process_trace_to_mml_grid(
    trace_opll_csv: str,
    output_dir: str | None = None,
    ticks_per_step_override: int | None = None,
) -> str:
    trace_abs = os.path.abspath(trace_opll_csv)
    base = os.path.basename(trace_abs)
    stem = base.replace('_trace.opll.csv', '')
    if stem == base:
        stem = os.path.splitext(os.path.splitext(base)[0])[0]

    if output_dir is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(repo_root, 'outputs', stem)

    voice_csv = trace_abs.replace('_trace.opll.csv', '_trace.opll_voice.csv')
    if voice_csv == trace_abs or not os.path.exists(voice_csv):
        voice_csv = None
    regs_csv = trace_abs.replace('_trace.opll.csv', '_trace.opll_regs.csv')
    if regs_csv == trace_abs or not os.path.exists(regs_csv):
        regs_csv = None

    return _build_grid_mml_from_inputs(
        trace_csv=trace_abs,
        voice_csv=voice_csv,
        regs_csv=regs_csv,
        output_dir=output_dir,
        stem=stem,
        ticks_per_step_override=ticks_per_step_override,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Generate pattern-based OPLL grid MML from *_trace.opll.csv',
    )
    parser.add_argument('trace_opll_csv', help='input *_trace.opll.csv path')
    parser.add_argument('output_dir', nargs='?', default=None, help='output directory')
    parser.add_argument('--ticks-per-step', type=int, default=None, help='override ticks per step')
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    out_path = process_trace_to_mml_grid(
        args.trace_opll_csv,
        output_dir=args.output_dir,
        ticks_per_step_override=args.ticks_per_step,
    )
    print(f'Wrote {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
