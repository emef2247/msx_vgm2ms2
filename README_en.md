# vgm2ms2

A script that converts VGM files (YM2413 / OPLL) into .MS2 binaries for playback in MoonSound Music Studio (MS²).

## Overview

- Exports YM2413 OPLL FM channels only (PSG and SCC are ignored)
- PAL/NTSC-aware tempo and quantization calculations
- Uses the OPL4 reference instrument library (MoonBlaster v1.4)

## Usage

```bash
python vgm2ms2.py --outdir <output directory> <input vgm>
```

### Example

```bash
python vgm2ms2.py --outdir out/ mysong.vgm
```

Output: `out/mysong.MS2`

## Options

| Option | Description |
|--------|-------------|
| `--outdir OUTDIR` | Output directory (default: `outputs/<stem>/`) |
| `--bpm BPM` | Song tempo in BPM (default: 120) |
| `--timer 0\|1` | Timer source: `0` = VDP timer (default), `1` = 50 Hz fixed |
| `--video ntsc\|pal` | Video system: `ntsc` (default) or `pal`. Only used when `--timer 0` |
| `--debug` | Print timing diagnostics (freq_hz, timer divider, quantization, implied BPM) |
| `--no-validate` | Skip post-build validation of the Patterns chunk |

### `--timer` and `--video` details

- `--timer 0` (VDP timer):
  - `--video ntsc` (default) → uses NTSC VDP interrupt frequency 59.988527... Hz
  - `--video pal` → uses PAL VDP interrupt frequency 50.214039... Hz
- `--timer 1` (50 Hz fixed):
  - `--video` is ignored; always uses 50.214039... Hz

Tempo formulas:
- `SongSettings+68 (timer_divider_raw) = round(freq_hz × 3840 / bpm)`
- Quantization step `ticks_per_step = max(1, round(freq_hz × 15 / bpm))`

## License

MIT License
