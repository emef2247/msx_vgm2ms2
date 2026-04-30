"""
opll_mml.py - OPLL (YM2413) melody MML generator (EXTENDED)
Adds: dynamic NUM_CH, voice_id, rhythm columns, extended CSV output for MIDI/rendering.

Channels: dynamic (auto-detect from CSV, supports 6~9~)
"""

import sys
import os
import math
import csv

sys.path.insert(0, os.path.dirname(__file__))
from mml_utils import (
    estimate_mml_used, estimate_alloc, track_id_to_mgsdrv,
    compress_mml_text, get_mgs_note_token_pct, ticks_to_mml_length, get_octave, get_scale,get_ticks
)

CH_OFFSET = 9        # OPLL ch0 → MGSDRV track 9

# Trace CSV column indices (existing)
_COL_TYPE   = 0
_COL_TIME   = 1
_COL_CH     = 2
_COL_TICKS  = 3
_COL_KEYON  = 4
_COL_FNUM   = 5
_COL_BLOCK  = 6
_COL_INST   = 7
_COL_VOL    = 8
_COL_SUS    = 9 

# -----------------------------------------------------------------------------
# OPLL Fnum + Block → (octave, scale_name) conversion (for MS2, MIDI, MML等)
# -----------------------------------------------------------------------------
_SCALE_NAMES = ['c', 'c+', 'd', 'd+', 'e', 'f', 'f+', 'g', 'g+', 'a', 'a+', 'b']

def _opll_note(fnum: int, block: int):
    """
    Return (octave, scale_name) for the given OPLL Fnum+Block pair.
    Returns ('r', 0) for invalid notes (fnum==0 or frequency out-of-range).
    """
    if fnum == 0:
        return 1, 'r'
    freq = 49716.0 * fnum * (1 << block) / (1 << 19)
    if freq < 16.0:
        return 1, 'r'
    midi = 69.0 + 12.0 * math.log2(freq / 440.0)
    midi_int = round(midi)
    octave = midi_int // 12 - 1
    octave = max(1, min(8, octave))
    scale_idx = midi_int % 12
    return octave, _SCALE_NAMES[scale_idx]
    
# -----------------------------------------------------------------------------
# Detect channel count
# -----------------------------------------------------------------------------
def detect_num_ch(trace_csv):
    max_ch = -1
    with open(trace_csv, "r") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split(",")
            try:
                ch = int(parts[_COL_CH])
                if ch > max_ch:
                    max_ch = ch
            except Exception:
                continue
    return (max_ch + 1) if max_ch >= 0 else 9

# -----------------------------------------------------------------------------
# Rhythm state tracker
# -----------------------------------------------------------------------------
class RhythmState:
    def __init__(self):
        self.is_ryt = 0 # Rhythm mode ON/OFF
        self.bd     = 0
        self.sd     = 0
        self.tom    = 0
        self.tc     = 0
        self.hh     = 0
        self.last_tick = -1
    def update(self, regval, tick):
        self.is_ryt = (regval >> 5) & 1
        bits = regval & 0x1F
        self.bd  = (bits >> 0) & 1
        self.sd  = (bits >> 1) & 1
        self.tom = (bits >> 2) & 1
        self.tc  = (bits >> 3) & 1
        self.hh  = (bits >> 4) & 1
        self.last_tick = tick
    def as_tuple(self):
        return (self.is_ryt, self.bd, self.sd, self.tom, self.tc, self.hh)

def parse_opll_regs_for_rhythm(regs_csv_path):
    """
    _trace.opll_regs.csv から type=rhythm 行だけ抜き出して
    (tick, regval) のリストを返す
    """
    timeline = []
    with open(regs_csv_path, newline='') as f:
        for row in csv.reader(f):
            if not row or row[0].startswith('#') or len(row) < 5:
                continue
            if row[0] == 'rhythm':
                try:
                    tick = int(float(row[2]))
                    regval = int(row[4])
                    timeline.append((tick, regval))
                except Exception:
                    continue
    return timeline
    
# -----------------------------------------------------------------------------
# Segment representation (with extended fields)
# -----------------------------------------------------------------------------
class _Segment:
    """
    Segment for one OPLL channel (melody + rhythm + retrigger + MS2 support)
    """

    __slots__ = (
        # 追加フィールド
        'reg_type',     # trace.csv のレジスタ種別（fNumL / fNumH / keyBlk / vol / inst / rhythm …）
        'time',         # VGM 経過時間（秒 or サンプル）
        'ch',           # ch               
        'l',            # 音価（前 Segment からの tick 差）
        'ms2_len',      # MS2 の 1 グリッド（grid_sz beat）を何個並べるか
        'beat_sz',      # beatは何ticksか
        'grid_sz',      # 「何分音符にするか」
        'o',            # オクターブ（block を MML 用に変換した値）
        'scale',        # 音階（0–11）
        'odiff',        # 音価（前 Segment からの o 差）
        'vdiff',        # 音価（前 Segment からの v 差）
        # 既存フィールド
        'beat_start', 'beat_end', 'keyon', 'fnum', 'block', 'inst', 'vol', 'sus',
        'beat', 'fb', 'kb', 'vb',
        'is_ryt', 'bd', 'sd', 'tom', 'tc', 'hh',
        'fnum_ch6', 'vol_ch6', 'sus_ch6',
        'fnum_ch7', 'vol_ch7', 'sus_ch7',
        'fnum_ch8', 'vol_ch8', 'sus_ch8',

        # MS2 用
        'at_token', 'voice_id',

        # retrigger
        'retrigger_ticks',
    )

    def __init__(
        self,
        reg_type, time,ch,
        beat_start, keyon, fnum, block, inst, vol, sus,
        beat=0,fb=0, kb=0, vb=0,
        is_ryt=0, bd=0, sd=0, tom=0, tc=0, hh=0,
        fnum_ch6=0, vol_ch6=0, sus_ch6=0,
        fnum_ch7=0, vol_ch7=0, sus_ch7=0,
        fnum_ch8=0, vol_ch8=0, sus_ch8=0,
    ):
        # 追加フィールド
        self.reg_type = reg_type
        self.time     = time
        self.ch       = ch
        self.l        = 0          # 後で _build_segments() 側で計算して埋める
        self.ms2_len  = 0
        self.beat_sz  = 0
        self.grid_sz  = 0
        self.o        = block      # とりあえず block をそのまま入れる（後で MML 用に変換）
        self.scale    = -1         # fnum → 音階変換で後から埋める
        self.odiff    = 0
        self.vdiff    = 0

        # 既存
        self.beat_start = beat_start
        self.beat_end   = beat_start

        self.keyon      = keyon
        self.fnum       = fnum
        self.block      = block
        self.inst       = inst
        self.vol        = vol
        self.sus        = sus

        self.beat       = beat
        self.fb         = fb
        self.kb         = kb
        self.vb         = vb

        self.is_ryt     = is_ryt
        self.bd         = bd
        self.sd         = sd
        self.tom        = tom
        self.tc         = tc
        self.hh         = hh

        self.fnum_ch6   = fnum_ch6
        self.vol_ch6    = vol_ch6
        self.sus_ch6    = sus_ch6

        self.fnum_ch7   = fnum_ch7
        self.vol_ch7    = vol_ch7
        self.sus_ch7    = sus_ch7

        self.fnum_ch8   = fnum_ch8
        self.vol_ch8    = vol_ch8
        self.sus_ch8    = sus_ch8

        self.at_token   = ''
        self.voice_id   = -1

        self.retrigger_ticks = []

    def mml_vol(self):
        return 15 - self.vol


def dump_segments(segments):
    print("\n================ SEGMENT DUMP (ONE LINE CSV) ================")

    # --- ヘッダ行を出力 ---
    # すべての __slots__ をカンマ区切りで表示
    header = ",".join(_Segment.__slots__)
    print("HEADER,ch," + header)

    for ch in sorted(segments.keys()):
        segs = sorted(segments[ch], key=lambda s: s.beat_start)

        for i, seg in enumerate(segs):
            values = []
            for attr in _Segment.__slots__:
                try:
                    val = getattr(seg, attr)
                except AttributeError:
                    val = "(missing)"
                values.append(str(val))

            # 1 行 CSV
            line = f"[{i}],{ch}," + ",".join(values)
            print(line)



# -----------------------------------------------------------------------------
# Build per-channel segment lists from the trace CSV - with dynamic NUM_CH
# -----------------------------------------------------------------------------
import csv

NUM_CH = 18  # ch=0~17

# rhythmチャンネル mapping
RHYTHM_CH_MAP = {
    "bd": 9,  # Bass Drum
    "sd": 10,  # Snare Drum
    "tom": 11, # Tom
    "tc": 12,  # Top Cymbal
    "hh": 13,  # Hi-Hat
}

RHYTHM_VOICE_ID_MAP = {
    "bd": 16,
    "sd": 17,
    "tom": 18,
    "tc": 19,
    "hh": 20,
}

NUM_CH = 18  # 0-8: OPLL, 13-17: Rhythm など

from collections import defaultdict
import csv
import math

IMPLICIT_RETRIGGER_MIN_GAP = 2
IMPLICIT_RETRIGGER_MAX_GAP = 32
NUM_CH = 18

def pass0_read_csv(trace_csv_path):
    """
    PASS 0:
      CSV の行をそのまま event として読み込む。
      ticks, keyon, fnum, block, inst, vol, sus などは CSV のまま保持する。
      l, fl, kl はまだ空欄のまま。
    """
    events = []

    with open(trace_csv_path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('#type', '').startswith('#'):
                continue

            # row をそのまま event として保持
            events.append(row)

    return events

from collections import defaultdict

from collections import defaultdict

def pass1_compute_l(events):
    """
    PASS 1（簡易版）:
      - ch ごとに ticks 昇順で並べる
      - l = next_ticks - ticks
      - fl, kl はまだ計算しない（0 のまま）
    """

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # ch ごとにグループ化
    ch_events = defaultdict(list)
    for ev in events:
        ch = _ival(ev.get("ch"), -1)
        ch_events[ch].append(ev)

    # 各 ch ごとに処理
    for ch, buf in ch_events.items():
        # ticks 昇順にソート（念のため）
        buf.sort(key=lambda e: _ival(e.get("ticks")))
        n = len(buf)

        for i, ev in enumerate(buf):
            tick = _ival(ev.get("ticks"))

            if i + 1 < n:
                next_tick = _ival(buf[i+1].get("ticks"))
                ev["l"] = next_tick - tick
            else:
                ev["l"] = 0  # 最後の行

            # fl, kl はとりあえず 0 に固定
            ev["fl"] = _ival(ev.get("fl"), 0)
            ev["kl"] = _ival(ev.get("kl"), 0)

    return events

def pass1_compute_l_from_time(events):
    """
    PASS 1（改良版）:
      - l = get_ticks(next_time) - get_ticks(current_time)
      - time の丸め誤差による l=1 の発生を防ぐ
    """

    def _fval(v, default=0.0):
        if v is None or v == "":
            return default
        try:
            return float(v)
        except:
            return default

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # ch ごとにグループ化
    from collections import defaultdict
    ch_events = defaultdict(list)
    for ev in events:
        ch = _ival(ev.get("ch"), -1)
        ch_events[ch].append(ev)

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: _fval(e.get("time")))

        n = len(buf)
        for i, ev in enumerate(buf):
            t = _fval(ev.get("time"))

            if i + 1 < n:
                t_next = _fval(buf[i+1].get("time"))
                l = get_ticks(t_next) - get_ticks(t)
                ev["l"] = max(l, 0)
            else:
                ev["l"] = 0

            # fl/kl/vl はまだ 0 のまま
            ev["fl"] = 0
            ev["kl"] = 0
            ev["vl"] = 0

    return events

def pass1_dump_csv(events, trace_csv_path):
    base, ext = os.path.splitext(trace_csv_path)
    out_csv_path = base + ".pass1.csv"

    fieldnames = list(events[0].keys())

    with open(out_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)

    print(f"[PASS1] Debug CSV written: {out_csv_path}")

def pass2_filter_l0(events):
    """
    PASS 2:
      l=0 の event をすべて削除する簡易版。
      （vol=15=無音、かつ l=0 は意味のある長さを持たない前提）
    """
    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    filtered = []
    for ev in events:
        l = _ival(ev.get("l"))
        if l > 0:
            filtered.append(ev)

    return filtered

def pass2_filter_and_recalc_l(events):
    """
    PASS 2:
      1. l=0 のイベントを削除
      2. 削除後のイベント列で l を再計算する
    """

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    def _fval(v, default=0.0):
        if v is None or v == "":
            return default
        try:
            return float(v)
        except:
            return default

    # --- 1. l=0 を削除 ---
    filtered = [ev for ev in events if _ival(ev.get("l")) > 0]

    # --- 2. l を再計算 ---
    for i, ev in enumerate(filtered):
        if i + 1 < len(filtered):
            t  = _fval(ev.get("time"))
            t2 = _fval(filtered[i+1].get("time"))
            ev["l"] = get_ticks(t2) - get_ticks(t)
        else:
            ev["l"] = 0

    return filtered

def pass2_dump_csv(events, trace_csv_path):
    """
    PASS 2:
      l=0 event を吸収して削除する。
      time, ticks, l, fl, kl は前の event の値を維持。
      fnum, block, inst, vol, sus などのレジスタ値は l=0 event の値で上書き。
    """

    if not events:
        return

    # 元ファイル名: <stem>_trace.opll.csv
    base, ext = os.path.splitext(trace_csv_path)
    # 出力ファイル名: <stem>_trace.opll.pass1.csv
    out_csv_path = base + ".pass2.csv"

    fieldnames = list(events[0].keys())

    with open(out_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)

    print(f"[PASS2] Debug CSV written: {out_csv_path}")

from collections import defaultdict

def pass3_update_fl_kl_vl(events):
    """
    PASS 3:
      - fl: fnum の継続時間（同じ fnum が続く間 l を加算）
            ただし keyon が変化した行では fl = l から再スタート
      - kl: keyon=1 の継続時間（keyon=1 が続く間 l を加算）
      - vl: vol の継続時間（同じ vol が続く間 l を加算）
            ただし keyon が変化した行では vl = l から再スタート
    """

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    ch_events = defaultdict(list)
    for ev in events:
        ch = _ival(ev.get("ch"), -1)
        ch_events[ch].append(ev)

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: _ival(e.get("ticks")))

        fnum_prev = None
        fl_acc    = 0

        keyon_prev = _ival(buf[0].get("keyon")) if buf else 0
        kl_acc     = 0

        vol_prev = None
        vl_acc   = 0

        for ev in buf:
            l     = _ival(ev.get("l"))
            fnum  = _ival(ev.get("fnum"))
            keyon = _ival(ev.get("keyon"))
            vol   = _ival(ev.get("vol"))

            keyon_changed = (keyon != keyon_prev)

            # --- fl ---
            if keyon_changed:
                # keyon が変わったら fl をリセットして l から再スタート
                fl_acc = l
            else:
                if fnum_prev is None:
                    fl_acc = 0
                else:
                    if fnum != fnum_prev:
                        fl_acc = l
                    else:
                        fl_acc += l
            ev["fl"] = fl_acc
            fnum_prev = fnum

            # --- kl ---
            if keyon_prev != keyon and keyon == 1:
                kl_acc = l
            elif keyon_prev == keyon and keyon == 1:
                kl_acc += l
            else:
                kl_acc = 0
            ev["kl"] = kl_acc

            # --- vl ---
            if keyon_changed:
                # keyon が変わったら vl もリセットして l から再スタート
                vl_acc = l
            else:
                if vol_prev is None:
                    vl_acc = 0
                else:
                    if vol != vol_prev:
                        vl_acc = l
                    else:
                        vl_acc += l
            ev["vl"] = vl_acc

            keyon_prev = keyon
            vol_prev   = vol

    return events

def pass3_dump_csv(events, trace_csv_path):
    """
    PASS 2:
      l=0 event を吸収して削除する。
      time, ticks, l, fl, kl は前の event の値を維持。
      fnum, block, inst, vol, sus などのレジスタ値は l=0 event の値で上書き。
    """

    if not events:
        return

    # 元ファイル名: <stem>_trace.opll.csv
    base, ext = os.path.splitext(trace_csv_path)
    # 出力ファイル名: <stem>_trace.opll.pass1.csv
    out_csv_path = base + ".pass3.csv"

    fieldnames = list(events[0].keys())

    with open(out_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)

    print(f"[PASS3] Debug CSV written: {out_csv_path}")

def pass4_compute_beats(events):
    """
    PASS 4（平均 min_l + beat 下限1 + keyon ロジック完全版）
    """

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # ------------------------------------------------------------
    # 1. min_l を keyon=1 の l の「平均ベース」で決定
    # ------------------------------------------------------------
    ls = [ _ival(ev.get("l")) for ev in events
           if _ival(ev.get("keyon")) == 1 and _ival(ev.get("l")) > 0 ]

    if not ls:
        return events

    base = min(ls)
    countA = sum(1 for v in ls if v == base)
    countB = sum(1 for v in ls if v == base + 1)

    if countB == 0:
        min_l = float(base)
    else:
        min_l = (base * countA + (base + 1) * countB) / (countA + countB)

    # ------------------------------------------------------------
    # 2. beat を計算（下限 1）
    # ------------------------------------------------------------
    for ev in events:
        l = _ival(ev.get("l"))
        keyon = _ival(ev.get("keyon"))

        if keyon == 1 and l > 0:
            beat = max(1, int(round(l / min_l)))
        else:
            beat = 0

        ev["beat"] = beat

    # ------------------------------------------------------------
    # 3. fb/kb/vb を計算（keyon ロジック）
    # ------------------------------------------------------------
    ch_events = defaultdict(list)
    for ev in events:
        ch = _ival(ev.get("ch"), -1)
        ch_events[ch].append(ev)

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: _ival(e.get("ticks")))

        fb_acc = kb_acc = vb_acc = 0
        fl_prev = kl_prev = vl_prev = 0
        keyon_prev = 0

        for ev in buf:
            beat  = _ival(ev.get("beat"))
            fl    = _ival(ev.get("fl"))
            kl    = _ival(ev.get("kl"))
            vl    = _ival(ev.get("vl"))
            keyon = _ival(ev.get("keyon"))

            if keyon == 0:
                fb_acc = kb_acc = vb_acc = 0
                ev["fb"] = ev["kb"] = ev["vb"] = 0
                keyon_prev = keyon
                fl_prev = fl
                kl_prev = kl
                vl_prev = vl
                continue

            if keyon_prev == 0 and keyon == 1:
                fb_acc = kb_acc = vb_acc = beat
                ev["fb"] = fb_acc
                ev["kb"] = kb_acc
                ev["vb"] = vb_acc
                keyon_prev = keyon
                fl_prev = fl
                kl_prev = kl
                vl_prev = vl
                continue

            if fl > fl_prev:
                fb_acc += beat
            if kl > kl_prev:
                kb_acc += beat
            if vl > vl_prev:
                vb_acc += beat

            ev["fb"] = fb_acc
            ev["kb"] = kb_acc
            ev["vb"] = vb_acc

            keyon_prev = keyon
            fl_prev = fl
            kl_prev = kl
            vl_prev = vl

    return events, min_l

def pass4_dump_csv(events, trace_csv_path):
    """
    PASS 4:
      - beat = int(l  / min_l)
      - fb   = int(fl / min_l)
      - kb   = int(kl / min_l)
      - vb   = int(vl / min_l)
      ここで min_l は l>0 の最小値。
    """

    if not events:
        return

    # 元ファイル名: <stem>_trace.opll.csv
    base, ext = os.path.splitext(trace_csv_path)
    # 出力ファイル名: <stem>_trace.opll.pass1.csv
    out_csv_path = base + ".pass4.csv"

    fieldnames = list(events[0].keys())

    with open(out_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)

    print(f"[PASS4] Debug CSV written: {out_csv_path}")

def _opll_vol_to_ms2(vol_opll: int) -> int:
    """
    OPLL vol: 0 (max) ... 15 (mute)
    MS2 vol: 0 (min) ... 63 (max)
    """
    if vol_opll >= 15:
        return 0
    v = (15 - vol_opll) * 4
    if v > 63:
        v = 63
    return v

def estimate_bpm_from_segments(segments_by_ch, vgm_rate):
    """
    segments_by_ch: {ch: [Segment, ...]}
    vgm_rate: VGM の tick/sec（vgm header の rate）
    """
    beat_durations = []

    for ch, segs in segments_by_ch.items():
        for seg in segs:
            if seg.l <= 0:
                continue
            if seg.fnum == 0:
                continue  # 休符は除外

            ticks = seg.beat_end - seg.beat_start
            if ticks <= 0:
                continue

            ticks_per_beat = ticks / seg.l
            beat_durations.append(ticks_per_beat)

    if not beat_durations:
        return None

    # 中央値で外れ値除去
    beat_durations.sort()
    ticks_per_beat = beat_durations[len(beat_durations)//2]

    bpm = 60 * vgm_rate / ticks_per_beat
    return round(bpm)


def _build_segments(trace_csv_path):
    # -----------------------------
    # 1. CSV → events（pass0〜pass4）
    # -----------------------------
    events = pass0_read_csv(trace_csv_path)

    # Pass 1: time 差分から l 再計算
    events = pass1_compute_l_from_time(events)
    pass1_dump_csv(events, trace_csv_path)

    # Pass 2: l=0 削除＋後続 l 再計算
    events = pass2_filter_and_recalc_l(events)
    pass2_dump_csv(events, trace_csv_path)

    # Pass 3: fl, kl, vl 更新
    events = pass3_update_fl_kl_vl(events)
    pass3_dump_csv(events, trace_csv_path)

    # Pass 4: beat, fb, kb, vb 計算（min_l は keyon=1 の l から）
    events, min_l = pass4_compute_beats(events)
    pass4_dump_csv(events, trace_csv_path)

    # -----------------------------
    # 2. ch ごとにイベントをまとめる
    # -----------------------------
    events_by_ch = defaultdict(list)
    for ev in events:
        ch = int(ev["ch"])
        events_by_ch[ch].append(ev)

    for ch in events_by_ch.keys():
        # time か ticks でソート（どちらでも一貫していれば OK）
        events_by_ch[ch].sort(key=lambda e: e["ticks"])

    segments_by_ch = {ch: [] for ch in range(NUM_CH)}

    # -----------------------------
    # 3. beat ベースで Segment 生成
    # -----------------------------
    for ch, evs in events_by_ch.items():
        cur_seg = None
        cur_beat_pos = 0  # この ch の「beat 上の位置」
        print(f"[SEG] ch={ch}, num_events={len(evs)}")

        for ev in evs:
            reg_type = ev["#type"]
            time     = float(ev["time"])
            ticks    = int(ev["ticks"])

            keyon    = int(ev["keyon"])
            fnum     = int(ev["fnum"])
            block    = int(ev["block"])
            inst     = int(ev["inst"])
            vol_opll = int(ev["vol"])
            sus      = int(ev.get("sus", 0))

            is_ryt   = int(ev.get("is_ryt", 0))
            bd       = int(ev.get("bd", 0))
            sd       = int(ev.get("sd", 0))
            tom      = int(ev.get("tom", 0))
            tc       = int(ev.get("tc", 0))
            hh       = int(ev.get("hh", 0))
            fnum_ch6 = int(ev.get("fnum_ch6", 0))
            vol_ch6  = int(ev.get("vol_ch6", 15))
            fnum_ch7 = int(ev.get("fnum_ch7", 0))
            vol_ch7  = int(ev.get("vol_ch7", 15))
            fnum_ch8 = int(ev.get("fnum_ch8", 0))
            vol_ch8  = int(ev.get("vol_ch8", 15))

            l    = int(ev.get("l", 0))
            beat = int(ev.get("beat", 0))
            fb   = int(ev.get("fb", 0))
            kb   = int(ev.get("kb", 0))
            vb   = int(ev.get("vb", 0))

            print(f"[SEG-RAW] ch={ch} ticks={ev['ticks']} keyon={keyon!r} fnum={fnum!r} vol={vol_opll!r} l={l!r} beat={beat!r}")

            # NOTE_OFF 条件
            is_off = (keyon == 0) or (vol_opll == 15) or (fnum == 0)

            # NOTE_ON 条件（メロディ／リズム共通で判定）
            is_on  = (keyon == 1 and beat > 0 and fnum != 0 and vol_opll != 15)

            # まず NOTE_OFF を処理（今鳴っている音を閉じる）
            if is_off and cur_seg is not None:
                cur_seg.beat_end = cur_beat_pos
                cur_seg.l = cur_seg.beat_end - cur_seg.beat_start
                segments_by_ch[ch].append(cur_seg)
                cur_seg = None

            # NOTE_ON なら新しい Segment を開始
            if is_on:
                # もしまだ開いている Segment があれば閉じてから開始
                if cur_seg is not None:
                    cur_seg.beat_end = cur_beat_pos
                    cur_seg.l = cur_seg.beat_end - cur_seg.beat_start
                    segments_by_ch[ch].append(cur_seg)

                ms2_vol = _opll_vol_to_ms2(vol_opll)

                cur_seg = _Segment(
                    reg_type,
                    time,
                    ch,
                    beat_start=cur_beat_pos,  # ここでは beat を beat_start として使う
                    keyon=keyon,
                    fnum=fnum,
                    block=block,
                    inst=inst,
                    vol=ms2_vol,
                    sus=sus,
                    beat=beat,                    # ここは好みで fb/kb/vb を入れる
                    fb=fb,                    # ここは好みで fb/kb/vb を入れる
                    kb=kb,
                    vb=vb,
                    is_ryt=is_ryt,
                    bd=bd,
                    sd=sd,
                    tom=tom,
                    tc=tc,
                    hh=hh,
                    fnum_ch6=fnum_ch6,
                    vol_ch6=_opll_vol_to_ms2(vol_ch6),
                    sus_ch6=ev.get("sus_ch6", 0),
                    fnum_ch7=fnum_ch7,
                    vol_ch7=_opll_vol_to_ms2(vol_ch7),
                    sus_ch7=ev.get("sus_ch7", 0),
                    fnum_ch8=fnum_ch8,
                    vol_ch8=_opll_vol_to_ms2(vol_ch8),
                    sus_ch8=ev.get("sus_ch8", 0),
                )

            # beat 位置を進める（NOTE_ON/OFF に関係なく）
            cur_beat_pos += beat

        # ch の最後に開いている Segment があれば閉じる
        if cur_seg is not None:
            cur_seg.beat_sz = min_l
            cur_seg.beat_end = cur_beat_pos
            cur_seg.l = cur_seg.beat_end - cur_seg.beat_start
            segments_by_ch[ch].append(cur_seg)

        # ここで o / scale を埋める（beat ベースでもロジックは同じ）
        for seg in segments_by_ch[ch]:
            seg.o = get_octave(seg.fnum)
            seg.scale = get_scale(seg.fnum)

    # -----------------------------
    # 4. BPM 推定（beat_sz = min_l を使用）
    # -----------------------------
    # VGM の tick/sec（例：44100）
    vgm_rate = events[0].get("vgm_rate", 44100)

    # beat_sz は pass4 の min_l（tick 単位）
    beat_sz = min_l

    if beat_sz > 0:
        bpm = round(60 * vgm_rate / beat_sz)
    else:
        bpm = None

    print(f"[BGM] vgm_rate={vgm_rate} min_l={min_l} beat_sz={beat_sz!r} bpm={bpm!r}")

    return segments_by_ch, bpm


# ---------------------------------------------------------------------------
# Voice table: voice CSV columns
# ---------------------------------------------------------------------------

_VCOL_TYPE      = 0
_VCOL_TIME      = 1
_VCOL_CH        = 2
_VCOL_TICKS     = 3
_VCOL_INST      = 4
_VCOL_VOL       = 5
_VCOL_PATCH_HEX = 6

# Known OPLL preset voice names (inst 1..15; 0 = user defined)
_OPLL_PRESET_NAMES = [
    'User Defined',      # 0
    'Violin',            # 1
    'Guitar',            # 2
    'Piano',             # 3
    'Flute',             # 4
    'Clarinet',          # 5
    'Oboe',              # 6
    'Trumpet',           # 7
    'Organ',             # 8
    'Horn',              # 9
    'Synthesizer',       # 10
    'Harpsichord',       # 11
    'Vibraphone',        # 12
    'Synthesizer Bass',  # 13
    'Acoustic Bass',     # 14
    'Electric Guitar',   # 15
]



def _ym2413_patch_to_mgsdrv(patch_bytes: bytes) -> dict:
    """Decode YM2413 user patch registers (0x00–0x07) into MGSDRV ``@v`` parameters.

    YM2413 user-patch register layout
    ──────────────────────────────────
    R0: AM(7) PM/VIB(6) EG(5) KR(4) MULT(3:0)  – Modulator
    R1: KL(7:6) TL(5:0)                          – Modulator
    R2: AR(7:4) DR(3:0)                           – Modulator
    R3: SL(7:4) RR(3:0)                           – Modulator
    R4: AM(7) PM/VIB(6) EG(5) KR(4) MULT(3:0)  – Carrier
    R5: KL(7:6) DC(4) DM(3) FB(2:0)             – Carrier
    R6: AR(7:4) DR(3:0)                           – Carrier
    R7: SL(7:4) RR(3:0)                           – Carrier

    MGSDRV ``@v`` format
    ────────────────────
    ``@vNN = { TL, FB, mod_AR, mod_DR, ..., mod_DT, car_AR, car_DR, ..., car_DT }``

    Returns a dict with keys ``tl``, ``fb``, ``mod`` (11-tuple), ``car`` (11-tuple).
    Each operator tuple is: (AR, DR, SL, RR, KL, MT, AM, VB, EG, KR, DT).
    """
    r = patch_bytes
    if len(r) < 8:
        r = bytes(r) + bytes(8 - len(r))

    tl = r[1] & 0x3F
    fb = r[5] & 0x07

    # Modulator operator fields
    m_ar = (r[2] >> 4) & 0x0F
    m_dr =  r[2]       & 0x0F
    m_sl = (r[3] >> 4) & 0x0F
    m_rr =  r[3]       & 0x0F
    m_kl = (r[1] >> 6) & 0x03
    m_mt =  r[0]       & 0x0F
    m_am = (r[0] >> 7) & 0x01
    m_vb = (r[0] >> 6) & 0x01   # VB (vibrato/PM)
    m_eg = (r[0] >> 5) & 0x01   # EG type (sustain)
    m_kr = (r[0] >> 4) & 0x01   # key-scale rate
    m_dt = (r[5] >> 3) & 0x01   # DT: waveform (0=sine, 1=half-sine); DM in YM2413 R5

    # Carrier operator fields
    c_ar = (r[6] >> 4) & 0x0F
    c_dr =  r[6]       & 0x0F
    c_sl = (r[7] >> 4) & 0x0F
    c_rr =  r[7]       & 0x0F
    c_kl = (r[5] >> 6) & 0x03
    c_mt =  r[4]       & 0x0F
    c_am = (r[4] >> 7) & 0x01
    c_vb = (r[4] >> 6) & 0x01   # VB (vibrato/PM)
    c_eg = (r[4] >> 5) & 0x01   # EG type (sustain)
    c_kr = (r[4] >> 4) & 0x01   # key-scale rate
    c_dt = (r[5] >> 4) & 0x01   # DT: waveform (0=sine, 1=half-sine); DC in YM2413 R5

    return {
        'tl':  tl,
        'fb':  fb,
        'mod': (m_ar, m_dr, m_sl, m_rr, m_kl, m_mt, m_am, m_vb, m_eg, m_kr, m_dt),
        'car': (c_ar, c_dr, c_sl, c_rr, c_kl, c_mt, c_am, c_vb, c_eg, c_kr, c_dt),
    }


def _user_patch_mml_defs(user_patches: dict) -> list[str]:
    """Return MML lines for all ``@vNN = {...}`` user-patch definitions.

    *user_patches* maps ``at_v_num (int) → patch_bytes (bytes)``, ordered by
    ``at_v_num``.  Emits one definition block per entry with MGSDRV-style
    comments showing the field labels.
    """
    lines = []
    for at_v_num in sorted(user_patches):
        patch_bytes = user_patches[at_v_num]
        d = _ym2413_patch_to_mgsdrv(patch_bytes)
        tl, fb = d['tl'], d['fb']
        m = d['mod']
        c = d['car']
        lines.append(f'@v{at_v_num} = {{')
        lines.append(';       TL FB')
        lines.append(f'        {tl:2d}, {fb},')
        lines.append('; AR DR SL RR KL MT AM VB EG KR DT')
        m_vals = ', '.join(f'{x:2d}' for x in m)
        lines.append(f'  {m_vals},')
        c_vals = ', '.join(f'{x:2d}' for x in c)
        lines.append(f'  {c_vals} }}')
        lines.append('')
    return lines


# -----------------------------------------------------------------------------
# Assign voice_id, at_token, etc to each segment
# -----------------------------------------------------------------------------

def _assign_voice_ids(segments: dict,
                      voice_csv_path: str | None) -> tuple[dict, dict, list[str]]:
    """Assign voice_id and at_token to every segment.

    Voice key definition:
      - inst != 0 : ('preset', inst)
      - inst == 0 : ('user', patch_bytes_tuple)  where patch_bytes is the
                    8-byte user patch current at the segment's beat_start.

    For preset instruments, ``seg.at_token`` is set to ``'@{inst}'`` (e.g. ``'@5'``).
    For user patches, ``seg.at_token`` is set to ``'@v{N}'`` where N is a
    stable 1-based integer assigned per unique 8-byte patch content.

    When *voice_csv_path* is None or missing, falls back to treating inst=0 as
    an all-zero user patch (produces a ``@v1`` definition with all zeros).

    Returns:
        voice_table  : dict mapping voice_key -> voice_id (0-indexed, for CSV dumps)
        user_patches : dict mapping at_v_num (int) -> patch_bytes (bytes), ordered
                       by first encounter; used to generate ``@vNN = {...}`` blocks
        warnings     : list of warning strings for caller to emit as comments
    """
    warnings: list[str] = []
    NUM_CH = max(segments.keys()) + 1
    # ── Read voice CSV ───────────────────────────────────────────
    # Build a sorted list of (tick, patch_bytes) for all 'patch' events.
    # These are global user-patch register updates (addr 0x00-0x07).
    patch_timeline: list[tuple[int, bytes]] = []   # (tick, patch_bytes)

    has_voice_csv = False
    has_patch_events = False
    if voice_csv_path and os.path.exists(voice_csv_path):
        has_voice_csv = True
        with open(voice_csv_path, 'r', newline='') as fh:
            for line in fh:
                line = line.rstrip('\r\n')
                if not line or line.startswith('#'):
                    continue
                parts = line.split(',')
                if len(parts) < 7:
                    continue
                try:
                    vtype = parts[_VCOL_TYPE]
                    ticks = int(parts[_VCOL_TICKS])
                    patch_hex = parts[_VCOL_PATCH_HEX].strip()
                    patch_bytes = bytes(int(patch_hex[i:i+2], 21)
                                        for i in range(0, 21, 2))
                except (ValueError, IndexError):
                    continue
                if vtype == 'patch':
                    patch_timeline.append((ticks, patch_bytes))
                    has_patch_events = True
    # patch_timeline is already in chronological order (voice CSV is ordered)

    def _get_patch_at_tick(tick: int) -> bytes:
        """Return the effective user patch at or before *tick*."""
        result = bytes(8)
        for t, p in patch_timeline:
            if t <= tick:
                result = p
            else:
                break
        return result

    # ── Assign voice IDs ─────────────────────────────────────────
    voice_table: dict[tuple, int] = {}   # voice_key -> voice_id (sequential, for CSV dump)
    next_id = 0

    # User-patch specific: map patch_bytes -> at_v_num (1-based) and
    # collect user_patches ordered dict for @vNN definition generation.
    user_patch_ids: dict[bytes, int] = {}   # patch_bytes -> at_v_num
    user_patches: dict[int, bytes]   = {}   # at_v_num -> patch_bytes
    next_user_v  = 1

    def _get_voice_id(key: tuple) -> int:
        nonlocal next_id
        if key not in voice_table:
            voice_table[key] = next_id
            next_id += 1
        return voice_table[key]

    def _get_user_v_num(patch: bytes) -> int:
        nonlocal next_user_v
        if patch not in user_patch_ids:
            user_patch_ids[patch] = next_user_v
            user_patches[next_user_v] = patch
            next_user_v += 1
        return user_patch_ids[patch]

    warned_no_voice_csv = False
    warned_no_patch_events = False
    for ch in range(NUM_CH):
        for seg in segments.get(ch, []):
            if seg.inst != 0:
                key = ('preset', seg.inst)
                seg.at_token = f'@{seg.inst}'
            else:
                if has_voice_csv:
                    patch = _get_patch_at_tick(seg.beat_start)
                    # Warn once if user patch registers were never written
                    if not has_patch_events and not warned_no_patch_events:
                        warnings.append(
                            '; NOTE: inst=0 used but no user patch registers '
                            '(0x00-0x07) were written; using all-zero patch')
                        warned_no_patch_events = True
                    key = ('user', patch)
                else:
                    # No voice CSV: treat inst=0 as unknown user patch (all zeros)
                    patch = bytes(8)
                    key = ('user', patch)
                    if not warned_no_voice_csv:
                        warnings.append(
                            '; WARNING: no voice CSV provided; '
                            'inst=0 user patches treated as all-zero')
                        warned_no_voice_csv = True
                user_v_num = _get_user_v_num(patch)
                seg.at_token = f'@v{user_v_num}'

            seg.voice_id = _get_voice_id(key)

    return voice_table, user_patches, warnings


def _voice_table_comments(voice_table: dict) -> list[str]:
    """Return a list of MML comment lines describing the voice table."""
    if not voice_table:
        return []

    # Invert for display: voice_id -> voice_key
    inv = {vid: vk for vk, vid in voice_table.items()}
    lines = ['; === OPLL Voice Table ===']
    for vid in sorted(inv):
        vk = inv[vid]
        if vk[0] == 'preset':
            name = (_OPLL_PRESET_NAMES[vk[1]]
                    if vk[1] < len(_OPLL_PRESET_NAMES) else '?')
            lines.append(f';@voice {vid:02d} preset inst={vk[1]} ({name})')
        else:
            patch_hex = ''.join(f'{b:02x}' for b in vk[1])
            lines.append(f';@voice {vid:02d} user patch={patch_hex}')
    lines.append('; =========================')
    return lines



# ---------------------------------------------------------------------------
# MML generation
# ---------------------------------------------------------------------------

def _generate_mml_impl(segments: dict, stem: str, raw_ticks: bool = False,
                       voice_table: dict | None = None,
                       user_patches: dict | None = None,
                       warnings: list[str] | None = None) -> str:
    """Generate MGSDRV MML text from per-channel segment data.

    When *raw_ticks* is True, emit ``{scale}%{N}`` tick notation and use
    ``#tempo 75`` (pass3.simple.mml style).  When False (default), emit
    standard divisor notation with ``#tempo 225``.
    """
    mml_buffer: dict[int, list] = {ch: [] for ch in range(NUM_CH)}

    for ch in range(NUM_CH):
        ch_num   = ch + CH_OFFSET
        track_id = track_id_to_mgsdrv(ch_num)
        mml_buffer[ch].append(f'\n\n;ch{track_id} start')

        segs = segments.get(ch,[])
        if not segs:
            continue
        l_cnt   = 0
        o_stamp = 0
        v_stamp = -1
        at_stamp = ''
        is_first_group = True
        mml     = ''
        note_cnt = 0

        for seg_idx, seg in enumerate(segs):
            if ch >= 6 and getattr(seg, "is_ryt", 0) == 1:
                continue  
            length = seg.beat_end - seg.beat_start
            if length <= 0:
                continue

            octave, scale = _opll_note(seg.fnum, seg.block)
            v   = seg.mml_vol()

            if not seg.keyon or seg.fnum == 0:
                scale = 'r'

            remaining = length
            while remaining > 0:
                ltmp = min(remaining, 255)

                if note_cnt == 0:
                    at_token = seg.at_token
                    if is_first_group:
                        if raw_ticks:
                            mml = f'\n{track_id} {at_token} v{v}'
                        else:
                            mml = f'\n{track_id} {at_token} v{v} o{octave} l64'
                            o_stamp = octave
                        is_first_group = False
                    else:
                        mml = f'\n{track_id}'
                        if at_token != at_stamp:
                            mml += f' {at_token}'
                        if v != v_stamp:
                            mml += f' v{v}'
                    at_stamp = at_token
                    v_stamp  = v

                if seg.at_token != at_stamp and note_cnt != 0:
                    mml += f' {seg.at_token}'
                    at_stamp = seg.at_token

                if v != v_stamp and note_cnt != 0:
                    mml += f' v{v}'
                    v_stamp = v

                if scale != 'r' and octave != o_stamp:
                    mml += f' o{octave}'
                    o_stamp = octave

                if raw_ticks:
                    mml += f' {scale}%{ltmp}'
                else:
                    mml += f' {ticks_to_mml_length(ltmp, scale)}'
                l_cnt += ltmp

                remaining -= ltmp
                if remaining > 0:
                    mml_buffer[ch].append(mml)
                    mml = ''

            note_cnt += 1
            if note_cnt >= 8 or (not seg.keyon and v == 0):
                mml_buffer[ch].append(mml)
                mml = ''
                mml_buffer[ch].append(f'\n;tick count: {l_cnt}\n')
                note_cnt = 0

        if mml:
            mml_buffer[ch].append(mml)

        mml_buffer[ch].append(f'\n;ch{track_id} end: tick count: {l_cnt}\n')

    # Build header
    tempo = 75 if raw_ticks else 225
    lines = []
    lines.append(';[name=opll]')
    lines.append('#opll_mode 1')
    lines.append(f'#tempo {tempo}')
    lines.append(f'#title {{ "{stem}"}}')
    for ch in range(NUM_CH):
        ch_num   = ch + CH_OFFSET
        track_id = track_id_to_mgsdrv(ch_num)
        used  = estimate_mml_used(mml_buffer[ch])
        alloc = estimate_alloc(used)
        lines.append(f'#alloc {track_id}={alloc}')
    if user_patches:
        lines.append('')
        lines.extend(_user_patch_mml_defs(user_patches))
    if voice_table:
        lines.extend(_voice_table_comments(voice_table))
    if warnings:
        lines.extend(warnings)
    lines.append('')
    lines.append('')

    header_text = '\n'.join(lines)

    body_parts = [header_text]
    for ch in range(NUM_CH):
        for item in mml_buffer[ch]:
            body_parts.append(item)

    result = ''.join(body_parts)
    if not result.endswith('\n'):
        result += '\n'
    return result


def _generate_mml(segments: dict, stem: str,
                  voice_table: dict | None = None,
                  user_patches: dict | None = None,
                  warnings: list[str] | None = None) -> str:
    """Generate MGSDRV MML text from per-channel segment data."""
    return _generate_mml_impl(segments, stem, raw_ticks=False,
                              voice_table=voice_table, user_patches=user_patches,
                              warnings=warnings)


def _generate_mml_mgs_pct(segments: dict, stem: str,
                           voice_table: dict | None = None,
                           user_patches: dict | None = None,
                           warnings: list[str] | None = None) -> str:
    """Generate OPLL MML with MGS delta-token octave/volume and raw tick (%) lengths.

    Applies the same ``<``/``>``/``(``/``)`` delta-token logic as
    :func:`get_mgs_note_token_pct` (±3 threshold):  emits relative octave
    and volume changes when the difference fits in ±3, otherwise emits
    absolute ``oN``/``vN`` tokens.  Note lengths are encoded as
    ``{scale}%{N}`` (raw ticks).  Uses ``#tempo 75``.

    This is the OPLL equivalent of the PSG/SCC ``MGS_pct`` variants.
    """
    mml_buffer: dict[int, list] = {ch: [] for ch in range(NUM_CH)}

    for ch in range(NUM_CH):
        ch_num   = ch + CH_OFFSET
        track_id = track_id_to_mgsdrv(ch_num)
        mml_buffer[ch].append(f'\n\n;ch{track_id} start')

        segs = segments.get(ch,[])
        l_cnt   = 0
        o_stamp = 0
        v_stamp = 0
        at_stamp = ''
        is_first_group = True
        mml     = ''
        note_cnt = 0

        for seg in segs:
            length = seg.beat_end - seg.beat_start
            if length <= 0:
                continue

            octave, scale = _opll_note(seg.fnum, seg.block)
            v   = seg.mml_vol()

            if not seg.keyon or seg.fnum == 0:
                scale = 'r'
                octave = o_stamp if o_stamp != 0 else 1

            remaining = length
            while remaining > 0:
                ltmp = min(remaining, 255)

                if note_cnt == 0:
                    at_token = seg.at_token
                    if is_first_group:
                        mml = f'\n{track_id} {at_token} v{v} o{octave}'
                        at_stamp = at_token
                        v_stamp  = v
                        o_stamp  = octave
                        is_first_group = False
                    else:
                        mml = f'\n{track_id}'
                        if at_token != at_stamp:
                            mml += f' {at_token}'
                            at_stamp = at_token
                        mml += f' v{v}'
                        v_stamp = v

                # Build note token with delta-token logic (cnt always 1 for OPLL)
                note = get_mgs_note_token_pct(
                    ltmp, v, v - v_stamp, scale, 1, octave, o_stamp, v_stamp)
                mml += ' ' + note
                l_cnt += ltmp

                o_stamp = octave
                v_stamp = v

                remaining -= ltmp
                if remaining > 0:
                    mml_buffer[ch].append(mml)
                    mml = ''

            note_cnt += 1
            if note_cnt >= 8 or (not seg.keyon and v == 0):
                mml_buffer[ch].append(mml)
                mml = ''
                mml_buffer[ch].append(f'\n;tick count: {l_cnt}\n')
                note_cnt = 0

        if mml:
            mml_buffer[ch].append(mml)

        mml_buffer[ch].append(f'\n;ch{track_id} end: tick count: {l_cnt}\n')

    # Build header
    lines = []
    lines.append(';[name=opll]')
    lines.append('#opll_mode 1')
    lines.append('#tempo 75')
    lines.append(f'#title {{ "{stem}"}}')
    for ch in range(NUM_CH):
        ch_num   = ch + CH_OFFSET
        track_id = track_id_to_mgsdrv(ch_num)
        used  = estimate_mml_used(mml_buffer[ch])
        alloc = estimate_alloc(used)
        lines.append(f'#alloc {track_id}={alloc}')
    if user_patches:
        lines.append('')
        lines.extend(_user_patch_mml_defs(user_patches))
    if voice_table:
        lines.extend(_voice_table_comments(voice_table))
    if warnings:
        lines.extend(warnings)
    lines.append('')
    lines.append('')

    header_text = '\n'.join(lines)

    body_parts = [header_text]
    for ch in range(NUM_CH):
        for item in mml_buffer[ch]:
            body_parts.append(item)

    result = ''.join(body_parts)
    if not result.endswith('\n'):
        result += '\n'
    return result
              
# -----------------------------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------------------------

def process_opll_csv(trace_path, output_dir, stem=None,
                     dump_passes=False, debug=True,
                     voice_csv_path=None, rhythm_reg_timeline=None):
    """Run the OPLL MML generation pipeline.

    Args:
        trace_path     : path to ``*_trace.opll.csv``
        output_dir     : directory for output files
        stem           : base name for output files (e.g. ``"mysong"``).
                         When *None* the stem is derived from *trace_path*.
        dump_passes    : when True write a pass0 segment CSV for debugging
        debug          : when True (default) write all MML variant files; when
                         False write only the ``pass3.compress.MGS_pct.mml``.
        voice_csv_path : optional path to ``*_trace.opll_voice.csv`` produced
                         by :func:`vgm_reader.parse_vgm`.  When provided the
                         voice table is built from user-patch register data so
                         that inst=0 segments are correctly distinguished by
                         their patch bytes.  When *None* inst=0 is treated as
                         an all-zero user patch.

    Returns:
        path to the generated MML file (``*.opll.mml`` in debug mode, or
        ``*.opll.pass3.compress.MGS_pct.mml`` in non-debug mode).
    """
    if stem is None:
        base = os.path.basename(trace_path)
        root = os.path.splitext(base)[0]
        stem = os.path.splitext(root)[0]
        if stem.endswith('_trace'):
            stem = stem[:-len('_trace')]
    os.makedirs(output_dir, exist_ok=True)
    # Build segments (tick-based final-state evaluation)
    segments, NUM_CH = _build_segments(trace_path,rhythm_reg_timeline=rhythm_reg_timeline)
    
    # Assign voice IDs using the voice CSV (user-patch tracking)
    voice_table, user_patches, warnings = _assign_voice_ids(segments, voice_csv_path)

    if dump_passes:
        # Emit a simple segment dump for debugging
        seg_path = os.path.join(output_dir, f'{stem}.opll.pass0.csv')
        with open(seg_path, 'w', newline='\n') as fh:
            fh.write('#ch,beat_start,beat_end,keyon,fnum,block,inst,vol,voice_id,at_token\n')
            for ch in range(NUM_CH):
                for seg in segments.get(ch, []):
                    fh.write(f'{ch},{seg.beat_start},{seg.beat_end},'
                             f'{seg.keyon},{seg.fnum},{seg.block},'
                             f'{seg.inst},{seg.vol},{seg.voice_id},{seg.at_token}\n')

    # ---- pass3.compress.MGS_pct.mml – always produced (merge source + non-debug output) ----
    simple_mgs_pct_text = _generate_mml_mgs_pct(segments, stem,
                                                  voice_table=voice_table,
                                                  user_patches=user_patches,
                                                  warnings=warnings)
    compress_mgs_pct_path = os.path.join(output_dir, f'{stem}.opll.pass3.compress.MGS_pct.mml')
    with open(compress_mgs_pct_path, 'w', newline='\n') as fh:
        fh.write(compress_mml_text(simple_mgs_pct_text))

    if not debug:
        return compress_mgs_pct_path

    # ---- debug-only variants ----

    mml_text = _generate_mml(segments, stem, voice_table=voice_table,
                              user_patches=user_patches, warnings=warnings)
    mml_path = os.path.join(output_dir, f'{stem}.opll.mml')
    with open(mml_path, 'w', newline='\n') as fh:
        fh.write(mml_text)

    # pass3.simple.mml – raw tick (%N) notation, #tempo 75
    simple_raw_text = _generate_mml_impl(segments, stem, raw_ticks=True,
                                          voice_table=voice_table,
                                          user_patches=user_patches,
                                          warnings=warnings)
    simple_raw_path = os.path.join(output_dir, f'{stem}.opll.pass3.simple.mml')
    with open(simple_raw_path, 'w', newline='\n') as fh:
        fh.write(simple_raw_text)

    # pass3.simple.MGS.mml – same as primary .opll.mml
    simple_mgs_path = os.path.join(output_dir, f'{stem}.opll.pass3.simple.MGS.mml')
    with open(simple_mgs_path, 'w', newline='\n') as fh:
        fh.write(mml_text)

    # pass3.compress.MGS.mml – divisor notation with token-level RLE compression
    compress_path = os.path.join(output_dir, f'{stem}.opll.pass3.compress.MGS.mml')
    with open(compress_path, 'w', newline='\n') as fh:
        fh.write(compress_mml_text(mml_text))

    # pass3.simple.MGS_pct.mml – MGS delta-token, raw tick (%) lengths, #tempo 75
    simple_mgs_pct_path = os.path.join(output_dir, f'{stem}.opll.pass3.simple.MGS_pct.mml')
    with open(simple_mgs_pct_path, 'w', newline='\n') as fh:
        fh.write(simple_mgs_pct_text)

    return mml_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <trace_opll_csv> [output_dir]")
        sys.exit(1)

    in_csv = sys.argv[1]
    if len(sys.argv) > 2:
        out_dir = sys.argv[2]
    else:
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base = os.path.basename(in_csv)
        root = os.path.splitext(os.path.splitext(base)[0])[0]
        if root.endswith('_trace'):
            s = root[:-len('_trace')]
        else:
            s = root
        out_dir = os.path.join(script_dir, 'outputs', s)

    # Optionally look for a companion voice CSV next to the trace CSV.
    # Trace CSV: {dir}/{stem}_trace.opll.csv
    # Voice CSV: {dir}/{stem}_trace.opll_voice.csv
    voice_csv = in_csv.replace('_trace.opll.csv', '_trace.opll_voice.csv')
    if voice_csv == in_csv or not os.path.exists(voice_csv):
        voice_csv = None

    result = process_opll_csv(in_csv, out_dir, voice_csv_path=voice_csv)
    print(f"Wrote {result}")

_COL_TYPE   = 0
_COL_TIME   = 1
def build_segments_trace_compatible(trace_path, NUM_CH=18,_COL_TYPE=0,_COL_TIME=1, _COL_CH=2, _COL_TICKS=3, _COL_KEYON=4, _COL_FNUM=5, _COL_BLOCK=6, _COL_INST=7, _COL_VOL=8):
    """
    旧バージョン風。メロディ部に限定/汎用化も可能
    """
    ch_events = {ch: [] for ch in range(NUM_CH)}
    with open(trace_path, 'r', newline='') as fh:
        for line in fh:
            line = line.rstrip('\r\n')
            if not line or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) < 9:
                continue
            try:
                _type =  int(parts[_COL_TYPE])
                _time = int(parts[_COL_TIME])
                ch    = int(parts[_COL_CH])
                ticks = int(parts[_COL_TICKS])
                keyon = int(parts[_COL_KEYON])
                fnum  = int(parts[_COL_FNUM])
                block = int(parts[_COL_BLOCK])
                inst  = int(parts[_COL_INST])
                vol   = int(parts[_COL_VOL])
            except (ValueError, IndexError):
                continue
            if 0 <= ch < NUM_CH:
                ch_events[ch].append((_type, time, ticks, keyon, fnum, block, inst, vol))
    segments = {ch: [] for ch in range(NUM_CH)}
    for ch in range(NUM_CH):
        events = ch_events[ch]
        if not events:
            continue
        state   = _ChState()
        cur_seg = None
        i       = 0
        n       = len(events)
        while i < n:
            tick = events[i][0]
            # 同tick内eventをまとめて終端状態を見る
            j = i
            while j < n and events[j][0] == tick:
                j += 1
            tick_has_keyoff = any(ev[1] == 0 for ev in events[i:j])
            for _, keyon, fnum, block, inst, vol in events[i:j]:
                state.keyon = keyon
                state.fnum  = fnum
                state.block = block
                state.inst  = inst
                state.vol   = vol
            if cur_seg is None:
                cur_seg = _Segment(tick, state.keyon, state.fnum, state.block,
                                   state.inst, state.vol)
            else:
                hidden_retrigger = (
                    cur_seg.keyon == 1 and tick_has_keyoff and state.keyon == 1
                )
                if hidden_retrigger:
                    cur_seg.beat_end = tick
                    segments[ch].append(cur_seg)
                    # ゼロ長keyoff
                    off_seg = _Segment(tick, 0, state.fnum, state.block,
                                       state.inst, state.vol)
                    off_seg.beat_end = tick
                    segments[ch].append(off_seg)
                    cur_seg = _Segment(tick, state.keyon, state.fnum,
                                       state.block, state.vol)
                else:
                    keyon_edge   = (state.keyon == 1 and cur_seg.keyon == 0)
                    keyoff_edge  = (state.keyon == 0 and cur_seg.keyon == 1)
                    pitch_change = (state.fnum != cur_seg.fnum or state.block != cur_seg.block) and state.keyon == 1
                    inst_change  = (state.inst != cur_seg.inst and state.keyon == 1)
                    vol_change   = (state.vol != cur_seg.vol   and state.keyon == 1)
                    if keyon_edge or keyoff_edge or pitch_change or inst_change or vol_change:
                        cur_seg.beat_end = tick
                        segments[ch].append(cur_seg)
                        cur_seg = _Segment(tick, state.keyon, state.fnum,
                                           state.block, state.inst, state.vol)
                    else:
                        cur_seg.beat_end = tick
            i = j
        if cur_seg is not None:
            segments[ch].append(cur_seg)
    return segments