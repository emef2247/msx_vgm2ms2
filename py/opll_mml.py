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
        'ev_type',      # trace.csv type（fNumL / fNumH / keyBlk / vol / inst / rhythm)今のところレジスタアクセスの区別。意図としてはeventのタイプ
        'time',         # global time [s], そのeventが起きた時間
        'ch',           # チェンネル
        'ticks',        # 1 tick = 1/60s
        'tick_start',   # keyon=1区間の開始点 [tick]
        'tick_end',     # keyon=1区間の終了点 [tick]
        'tempo',        # BPM. 今のところ曲全体のBPM
        'onset',        #　keyon=1の開始点であることを示すマーカー 0:　keyon=1を維持又はkeyon=0 1: 1: keyon区間の開始 keyon: 0--> 1
        'ioi',          # IOI（Inter‑Onset Interval). keyon=1 区間の 実際の長さ（tick). keyon=1でioi=0はまだkeyonが継続中を表す(ioi未確定のためioi=0)
        'min_ioi',      # ioi の最小値（または mode). 音符の最小長さ. tempo 計算の基準
        'l',            # そのＳｅｇｍｅｎｔの長さ [tick] Next ticks = current ticks + l
        'fl',           # 同じfnumの継続期間 [tick] keyonのposedgeでlにリセット. fl > lはkeyoffを挟まずにfnumが変化していることを表す
        'kl',           # 同じkeyonの継続期間 [tick] keyonのposedgeでlにリセット. kl > lは_Segmentを跨いで同じkeyon状態が継続中であることを示す
        'vl',           # 同じvolの継続期間 [tick] keyonのposedgeでlにリセット. vl > lはkeyoffを挟まずにvolが変化していることを表す
        'min_l',        # min_l　vgmのイベントの最小区間　beat = l / min_l
        'beat_start',   # keyon=1の開始点 [beat]
        'beat_end',     # keyon=1の終了点 [beat]
        'beat_pos',     # _Segmentの開始 [beat] beat_pos = それまでのbeatの累計。 next beat_pos = beat_pos + beat
        'beat',         # vgmのイベントをmin_lで正規化した値。
        'fb',           # flのbeat換算 [beat] keyonのposedgeでbeatにリセット.　fb > beat はkeyoffを挟まずにfnumが変化していることを表す
        'kb',           # kbのbeat換算 [beat] keyonのposedgeでbeatにリセット.　kb > beat は同じkeyon状態が_Segmentを跨いで継続中であることを示す
        'vb',           # vlのbeat換算 [beat] keyonのposedgeでbeatにリセット.　vb > beat はkeyoffを挟まずにvolが変化していることを表す
        'keyon',        # keyonの状態
        'is_legate',    # 一つ前のeventがkeyon=1であり、keyon=0を挟まずkeyon=1が続いている場合に1となる(スラー、タイ)
        'is_vibrato',   # keyon=1区間内でkeyon=0を挟まずfnumが変化
        'is_envelope',  # keyon=1区間内でkeyon=0を挟まずvolが変化
        'fnum',         # frequency
        'block',        # block, octave
        'inst',         # instrument no
        'vol',          # volume
        'sus',          # sustain
        'scale',        # scale
        'voice_id',     # voice id
        'odiff',        # 一つ前のSegmentとのblock(octave)の差分。　MML生成用
        'vdiff',        #　一つ前のSegmentとのvolumeの差分。　MML生成用
        'is_ryt',       # リズムモード 0: メロディモード 1:リズムモード  OPLL 0xEのD5の値が入る
        'r_tempo',      # リズムで計測したtempo
        'r_ioi',        # rhythm 全体の IOI [tick]
        'r_mode_ioi',   # rhythm の最頻 IOI [tick] (mode)
        'r_tempo',      # rhythm の BPM
        'r_hh_ioi',     # ハイハット専用 IOI [tick]
        'r_hh_mode_ioi', # ハイハットの最頻 IOI [tick] (mode)
        'bd',           # Bass  Drum エッジトリガ 1: ON 0: OFF
        'sd',           # Snare Drum エッジトリガ 1: ON 0: OFF
        'tom',          # Tom-tom    エッジトリ  1: ON 0: OFF
        'tc',           # Top Cymbal エッジトリ  1: ON 0: OFF
        'hh',           # High-Hat   エッジトリ  1: ON 0: OFF
        'fnum_ch6',     # リズムモード(ryt==1)時、参照用 ch6のfnum
        'vol_ch6',      # リズムモード(ryt==1)時、参照用 ch6のvol
        'sus_ch6',      # リズムモード(ryt==1)時、参照用 ch6のsus
        'block_ch6',    # リズムモード(ryt==1)時、参照用 ch6のblock
        'fnum_ch7',     # リズムモード(ryt==1)時、参照用 ch7のfnum
        'vol_ch7',      # リズムモード(ryt==1)時、参照用 ch7のvol
        'sus_ch7',      # リズムモード(ryt==1)時、参照用 ch7のsus
        'block_ch7',    # リズムモード(ryt==1)時、参照用 ch8のblock
        'fnum_ch8',     # リズムモード(ryt==1)時、参照用 ch8のfnum
        'vol_ch8',      # リズムモード(ryt==1)時、参照用 ch8のvol
        'sus_ch8',      # リズムモード(ryt==1)時、参照用 ch8のsus
        'block_ch8',    # リズムモード(ryt==1)時、参照用 ch8のblock
        'ms2_vol',      # ms2用volume (0: 最大 63: 無音)
        'ms2_len',      # MS2 の 1 グリッド（grid_sz beat）を何個並べるか
        'beat_sz',      # beatは何ticksか (== min_l)
        'grid_sz',      # 何分音符にするか
        'at_token',
    )

    def __init__(
        self,
        ev_type       ,
        time          ,
        ch            ,
        ticks         ,
        onset         ,
        tempo         ,
        ioi           ,
        min_ioi       ,
        l             ,
        fl            ,
        kl            ,
        vl            ,
        beat_pos      ,
        beat          ,
        fb            ,
        kb            ,
        vb            ,
        keyon         ,
        is_legate     ,
        is_vibrato    ,
        is_envelope   ,
        fnum          ,
        block         ,
        inst          ,
        vol           ,
        sus           ,
        scale         ,
        is_ryt        ,
        r_tempo       ,
        r_ioi         ,
        r_mode_ioi    ,
        r_hh_ioi      ,
        r_hh_mode_ioi ,
        bd            ,
        sd            ,
        tom           ,
        tc            ,
        hh            ,
        fnum_ch6      ,
        vol_ch6       ,
        sus_ch6       ,
        block_ch6     ,
        fnum_ch7      ,
        vol_ch7       ,
        sus_ch7       ,
        block_ch7     ,
        fnum_ch8      ,
        vol_ch8       ,
        sus_ch8       ,
        block_ch8     ,

    ):

        self.ev_type       = ev_type
        self.time          = time
        self.ch            = ch
        self.ticks         = ticks
        self.tick_start   = 0
        self.tick_end     = 0
        self.tempo         = tempo
        self.onset         = onset
        self.ioi           = ioi
        self.min_ioi       = min_ioi
        self.l             = l
        self.fl            = fl
        self.kl            = kl
        self.vl            = vl
        self.min_l         = 0
        self.beat_start    = 0
        self.beat_end      = 0
        self.beat_pos      = beat_pos
        self.beat          = beat
        self.fb            = fb
        self.kb            = kb
        self.vb            = vb
        self.keyon         = keyon
        self.is_legate     = is_legate
        self.is_vibrato    = is_vibrato
        self.is_envelope   = is_envelope
        self.fnum          = fnum
        self.block         = block
        self.inst          = inst
        self.vol           = vol
        self.sus           = sus
        self.scale         = scale
        self.voice_id      = -1
        self.odiff         = 0
        self.vdiff         = 0
        self.is_ryt        = is_ryt
        self.r_tempo       = r_tempo
        self.r_ioi         = r_ioi
        self.r_mode_ioi    = r_mode_ioi
        self.r_tempo       = 0
        self.r_hh_ioi      = r_hh_ioi
        self.r_hh_mode_ioi = r_hh_mode_ioi
        self.bd            = bd
        self.sd            = sd
        self.tom           = tom
        self.tc            = tc
        self.hh            = hh
        self.fnum_ch6      = fnum_ch6
        self.vol_ch6       = vol_ch6
        self.sus_ch6       = sus_ch6
        self.block_ch6     = block_ch6
        self.fnum_ch7      = fnum_ch7
        self.vol_ch7       = vol_ch7
        self.sus_ch7       = sus_ch7
        self.block_ch7     = block_ch7
        self.fnum_ch8      = fnum_ch8
        self.vol_ch8       = vol_ch8
        self.sus_ch8       = sus_ch8
        self.block_ch8     = block_ch8
        self.ms2_vol       = 0
        self.ms2_len       = 0
        self.beat_sz       = 0
        self.grid_sz       = 0
        self.at_token      = ''

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

def pass2_compute_onsets_and_ioi(events):

    def _ival(v, default=0):
        try:
            return int(v)
        except:
            return default

    # ------------------------------------------------------------
    # 1. scale をセット
    # ------------------------------------------------------------
    for ev in events:
        fnum = _ival(ev.get("fnum"))
        ev["scale"] = get_scale(fnum)

    # ------------------------------------------------------------
    # 2. melody onset / ioi（既存処理）
    # ------------------------------------------------------------
    events_by_ch = {}
    for idx, ev in enumerate(events):
        ch = _ival(ev.get("ch"))
        events_by_ch.setdefault(ch, []).append((idx, ev))

    for ch, lst in events_by_ch.items():

        lst.sort(key=lambda x: _ival(x[1].get("ticks")))

        prev_keyon = 0
        prev_vol   = 15

        # onset
        for i in range(len(lst)):
            idx, ev = lst[i]
            keyon = _ival(ev.get("keyon"))
            vol   = _ival(ev.get("vol"))

            if ev.get("#type") == "rhythm":
                ev["onset"] = 0
            else:
                is_onset = False
                if prev_keyon == 0 and keyon == 1 and vol < 15:
                    is_onset = True
                elif prev_keyon == 1 and keyon == 1 and prev_vol == 15 and vol < 15:
                    is_onset = True
                ev["onset"] = 1 if is_onset else 0

            prev_keyon = keyon
            prev_vol   = vol

        # ioi
        total_l = 0
        for i in range(len(lst)):
            idx, ev = lst[i]
            keyon = _ival(ev.get("keyon"))
            vol   = _ival(ev.get("vol"))
            l     = _ival(ev.get("l"))

            if vol == 15 or ev.get("#type") == "rhythm" or keyon == 0:
                total_l = 0
                ev["ioi"] = 0
                continue

            if ev["onset"] == 1:
                total_l = l
            else:
                total_l += l

            if i + 1 < len(lst):
                next_ev = lst[i+1][1]
                next_keyon = _ival(next_ev.get("keyon"))
                next_vol   = _ival(next_ev.get("vol"))

                if next_keyon == 0 or next_vol == 15:
                    ev["ioi"] = total_l
                    total_l = 0
                else:
                    ev["ioi"] = 0
            else:
                ev["ioi"] = total_l
                total_l = 0

    # ------------------------------------------------------------
    # 3. rhythm: r_ioi = l（l=0 は無視）
    # ------------------------------------------------------------
    for ev in events:
        if ev.get("#type") == "rhythm":
            l = _ival(ev.get("l"))
            ev["r_ioi"] = l if l > 0 else 0

    # ------------------------------------------------------------
    # 4. rhythm: r_hh_ioi（hh=1 の間隔を l の積算で求める）
    # ------------------------------------------------------------
    _r_hh_ioi = 0
    for ev in events:
        if ev.get("#type") != "rhythm":
            continue

        l = _ival(ev.get("l"))
        hh = _ival(ev.get("hh"))

        if l == 0:
            continue

        _r_hh_ioi += l

        if hh == 1:
            ev["r_hh_ioi"] = _r_hh_ioi
            _r_hh_ioi = 0
        else:
            ev["r_hh_ioi"] = 0

    # ------------------------------------------------------------
    # 5. r_mode_ioi / r_hh_mode_ioi / r_tempo を計算
    # ------------------------------------------------------------
    r_ioi_list = [ _ival(ev.get("r_ioi")) for ev in events
                   if ev.get("#type") == "rhythm" and _ival(ev.get("r_ioi")) > 0 ]

    r_hh_ioi_list = [ _ival(ev.get("r_hh_ioi")) for ev in events
                      if ev.get("#type") == "rhythm" and _ival(ev.get("r_hh_ioi")) > 0 ]

    from statistics import mode, median

    def pick_mode(lst):
        if not lst:
            return 0
        try:
            return mode(lst)
        except:
            return int(median(lst))

    r_mode_ioi    = pick_mode(r_ioi_list)
    r_hh_mode_ioi = pick_mode(r_hh_ioi_list)
    r_tempo       = 3600.0 / r_mode_ioi if r_mode_ioi > 0 else 0.0

    for ev in events:
        ev["r_mode_ioi"] = r_mode_ioi
        ev["r_hh_mode_ioi"] = r_hh_mode_ioi
        ev["r_tempo"] = r_tempo

    return events

def pass2_mark_legato_vibrato_envelope(events):

    def _ival(v, default=0):
        try:
            return int(v)
        except:
            return default

    # ------------------------------------------------------------
    # 1. ch ごとにイベントを分離し、ticks でソート
    # ------------------------------------------------------------
    events_by_ch = {}
    for ev in events:
        ch = _ival(ev.get("ch"))
        events_by_ch.setdefault(ch, []).append(ev)

    for ch, lst in events_by_ch.items():
        lst.sort(key=lambda ev: _ival(ev.get("ticks")))

        prev_keyon = 0

        # --------------------------------------------------------
        # 2. 各 ch 内で legato / vibrato / vol_envelope を判定
        # --------------------------------------------------------
        for ev in lst:

            if ev.get("#type") == "rhythm":
                ev["is_legato"] = 0
                ev["is_vibrato"] = 0
                ev["is_envelope"] = 0
                continue

            keyon = _ival(ev.get("keyon"))
            l  = _ival(ev.get("l"))
            kl = _ival(ev.get("kl"))
            fl = _ival(ev.get("fl"))
            vl = _ival(ev.get("vl"))

            # keyon posedge → NOTE_ON → legato=0
            if prev_keyon == 0 and keyon == 1:
                ev["is_legato"] = 0

            # legato 条件：prev_keyon=1 かつ kl > l
            elif prev_keyon == 1 and kl > l:
                ev["is_legato"] = 1

            else:
                ev["is_legato"] = 0

            # vibrato / vol envelope は legato のときだけ
            ev["is_vibrato"] = 1 if (ev["is_legato"] == 1 and fl != kl) else 0
            ev["is_envelope"] = 1 if (ev["is_legato"] == 1 and vl != kl) else 0

            prev_keyon = keyon

    # ------------------------------------------------------------
    # 3. 全 ch を結合して返す
    # ------------------------------------------------------------
    merged = []
    for ch in sorted(events_by_ch.keys()):
        merged.extend(events_by_ch[ch])

    return merged


def pass2_expand_rhythm(events, RHYTHM_CH_MAP, RHYTHM_VOICE_ID_MAP):
    new_events = []

    for ev in events:
        ev_type = ev.get("#type", "")
        ch      = int(ev.get("ch", 0))
        is_ryt  = int(ev.get("is_ryt", 0))

        # rhythm 以外はそのまま
        if not ((ev_type == "rhythm") or (ch == -1 and is_ryt == 1)):
            new_events.append(ev)
            continue

        # rhythm 行を 5 種類に分解
        for key in ["bd", "sd", "tom", "tc", "hh"]:
            keyon = int(ev.get(key, 0))

            # チャンネル割り当て
            ch_new = RHYTHM_CH_MAP[key]

            # fnum/vol/sus は ch6/7/8 から取得
            if key == "bd":
                keyonX = int(ev.get("bd", 0))
                fnumX = int(ev.get("fnum_ch6", 0))
                volX  = int(ev.get("vol_ch6", 0))
                susX  = int(ev.get("sus_ch6", 0))
                blockX= int(ev.get("block_ch6", 0))
            elif key == "sd":
                keyonX = int(ev.get("sd", 0))
                fnumX = int(ev.get("fnum_ch7", 0))
                volX  = int(ev.get("vol_ch7", 0))
                susX  = int(ev.get("sus_ch7", 0))
                blockX= int(ev.get("block_ch7", 0))
            elif key == "tom":
                keyonX = int(ev.get("tom", 0))
                # tom / tc / hh
                fnumX = int(ev.get("fnum_ch8", 0))
                volX  = int(ev.get("vol_ch8", 0))
                susX  = int(ev.get("sus_ch8", 0))
                blockX= int(ev.get("block_ch8", 0))
            elif key == "tc": 
                keyonX = int(ev.get("tc", 0))
                # tom / tc / hh
                fnumX = int(ev.get("fnum_ch8", 0))
                volX  = int(ev.get("vol_ch8", 0))
                susX  = int(ev.get("sus_ch8", 0))
                blockX= int(ev.get("block_ch8", 0))
            elif key == "hh":  
                keyonX = int(ev.get("hh", 0))
                # tom / tc / hh
                fnumX = int(ev.get("fnum_ch8", 0)) 
                volX  = int(ev.get("vol_ch8", 0))
                susX  = int(ev.get("sus_ch8", 0))
                blockX= int(ev.get("block_ch8", 0))
            # 新しい ev を作成
            ev_new = dict(ev)
            ev_new["ch"]   = ch_new
            ev_new["keyon"]= keyonX
            ev_new["fnum"] = fnumX
            ev_new["block"]= blockX
            ev_new["inst"] = RHYTHM_VOICE_ID_MAP[key]
            ev_new["vol"]  = volX
            ev_new["sus"]  = susX

            new_events.append(ev_new)

    return new_events

def pass2_recompute_rhythm_lengths(events):
    # rhythm チャンネルだけ抽出
    rhythm_chs = [6, 7, 8]  # RHYTHM_CH_MAP の値に合わせる
    events_by_ch = {ch: [] for ch in rhythm_chs}

    # 1) rhythm イベントをチャンネルごとに集める
    for ev in events:
        ch = int(ev.get("ch", -1))
        if ch in rhythm_chs:
            events_by_ch[ch].append(ev)

    # 2) 各チャンネルごとに tick/l を再計算
    for ch, evs in events_by_ch.items():
        # tick_start でソート
        evs.sort(key=lambda e: int(e.get("ticks", 0)))

        new_evs = []
        for i, ev in enumerate(evs):
            tick_start = int(ev.get("ticks", 0))

            if i < len(evs) - 1:
                next_tick = int(evs[i+1].get("ticks", 0))
                l = max(1, next_tick - tick_start)
            else:
                l = 1  # 最後は最小長さ

            ev["ticks"] = tick_start
            ev["l"] = l
            ev["tick_start"] = tick_start
            ev["tick_end"] = tick_start + l

            new_evs.append(ev)

        # 3) keyon=0 の連続イベントをマージ
        merged = []
        for ev in new_evs:
            if merged and merged[-1]["keyon"] == 0 and ev["keyon"] == 0:
                # 長さを伸ばす
                merged[-1]["l"] += ev["l"]
                merged[-1]["tick_end"] = ev["tick_end"]
            else:
                merged.append(ev)

        # 置き換え
        events_by_ch[ch] = merged

    # 4) 元の events に戻す
    final = []
    for ev in events:
        ch = int(ev.get("ch", -1))
        if ch in rhythm_chs:
            continue
        final.append(ev)

    for ch in rhythm_chs:
        final.extend(events_by_ch[ch])

    return final


def pass2_filter_l0(events):
    """
    PASS 2:
      l=0 の event を削除する。
      ただし、l=0 かつ keyon の立ち上がり (prev keyon=0, cur keyon=1) の場合は、
      その event 自体は削除しつつ、
      「次に残る同じ ch の event」に ioi=1 / onset=1 を引き継ぐ。
    """

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # ch ごとにまとめて ticks でソート
    events_by_ch = {}
    for ev in events:
        ch = _ival(ev.get("ch"), -999)
        events_by_ch.setdefault(ch, []).append(ev)

    for ch, lst in events_by_ch.items():
        lst.sort(key=lambda e: _ival(e.get("ticks")))

    filtered_all = []

    for ch, lst in events_by_ch.items():
        prev_keyon = 0
        # 「直前に削除した l=0 posedge のぶん、次の event に ioi=1/onset=1 を付ける」フラグ
        pending_ioi_onset = False

        for ev in lst:
            l      = _ival(ev.get("l"))
            keyon  = _ival(ev.get("keyon"))
            ioi    = _ival(ev.get("ioi"))
            onset  = _ival(ev.get("onset"))

            is_posedge = (prev_keyon == 0 and keyon == 1)

            if l == 0:
                # 削除対象。ただし keyon posedge の場合は「次の event に ioi=1/onset=1 を付ける」予約をする
                if is_posedge:
                    pending_ioi_onset = True
                # この ev 自体は残さない
                prev_keyon = keyon
                continue

            # ここに来るのは l>0 で残す event
            if pending_ioi_onset:
                # すでに ioi/onset が計算されている場合は壊さない前提だが、
                # 今回のケースでは 0 なので上書きで問題ない
                ev["ioi"]   = 1
                ev["onset"] = 1
                pending_ioi_onset = False

            filtered_all.append(ev)
            prev_keyon = keyon

    # ch の順序は元のままでもよいが、全体を time/ticks で並べたいならここでソートしてもよい
    filtered_all.sort(key=lambda e: (_ival(e.get("ch")), _ival(e.get("ticks"))))

    return filtered_all

def pass2_compute_fl_kl_vl(events):
    """
    PASS2（レガート完全対応版）

      - keyon=0 または keyon=1 の最初のイベントは fl/kl/vl = l にセット
      - keyon=1 が続き、かつ fnum/block/inst/vol が同じならレガート継続として fl/kl/vl += l
      - ピッチ/音色/音量が変わったら fl/kl/vl = l（新しい音符）
      - keyon=1 かつ l==0 の行は「長さを持たない制御イベント」として fl/kl/vl を変えない
    """

    from collections import defaultdict

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    ch_events = defaultdict(list)
    for ev in events:
        ch_events[_ival(ev.get("ch"), -1)].append(ev)

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: _ival(e.get("ticks")))

        fnum_prev  = None
        block_prev = None
        inst_prev  = None
        vol_prev   = None
        keyon_prev = 0

        fl_acc = 0
        kl_acc = 0
        vl_acc = 0

        for ev in buf:
            l     = _ival(ev.get("l"))
            fnum  = _ival(ev.get("fnum"))
            block = _ival(ev.get("block"))
            inst  = _ival(ev.get("inst"))
            vol   = _ival(ev.get("vol"))
            keyon = _ival(ev.get("keyon"))

            # --- keyon=0: 音符が終わった区間 ---
            if keyon == 0:
                fl_acc = l
                kl_acc = l
                vl_acc = l

                ev["fl"] = fl_acc
                ev["kl"] = kl_acc
                ev["vl"] = vl_acc

                keyon_prev = 0
                fnum_prev  = fnum
                block_prev = block
                inst_prev  = inst
                vol_prev   = vol
                continue

            # --- keyon=1 かつ l==0: 制御イベント（長さなし） ---
            if keyon == 1 and l == 0:
                ev["fl"] = fl_acc
                ev["kl"] = kl_acc
                ev["vl"] = vl_acc

                keyon_prev = 1
                fnum_prev  = fnum
                block_prev = block
                inst_prev  = inst
                vol_prev   = vol
                continue

            # --- keyon=1 かつ l>0: 実際の音価 ---
            if keyon_prev == 0:
                # 新しい音符
                fl_acc = l
                kl_acc = l
                vl_acc = l
            else:
                # keyon_prev == 1 → 音が続いている
                same_pitch = (fnum == fnum_prev and block == block_prev)
                same_tone  = (inst == inst_prev)
                same_vol   = (vol == vol_prev)

                if same_pitch and same_tone and same_vol:
                    # ★ レガート継続
                    fl_acc += l
                    kl_acc += l
                    vl_acc += l
                else:
                    # ピッチ/音色/音量が変わった → 新しい音符
                    fl_acc = l
                    kl_acc = l
                    vl_acc = l

            ev["fl"] = fl_acc
            ev["kl"] = kl_acc
            ev["vl"] = vl_acc

            keyon_prev = 1
            fnum_prev  = fnum
            block_prev = block
            inst_prev  = inst
            vol_prev   = vol

    return events

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

def pass3_merge_silent_rests(events):
    """
    PASS3:
      - retrigger を除外しつつ休符をマージ
      - ただし rhythm(ch=-1) は絶対にマージしない（そのまま残す）
    """

    from collections import defaultdict

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # tick_start / tick_end を準備
    for ev in events:
        ev["ticks"] = _ival(ev.get("ticks"))
        ev["l"]     = _ival(ev.get("l"))
        ev["tick_start"] = ev["ticks"]
        ev["tick_end"]   = ev["ticks"] + ev["l"]

    # --- rhythm は絶対にマージしない ---
    def is_rhythm(ev):
        return ev.get("#type") == "rhythm" or _ival(ev.get("ch")) == -1

    # --- 休符判定（melody のみ） ---
    def is_rest(ev):
        if is_rhythm(ev):
            return False  # rhythm は休符扱いしない
        return (
            _ival(ev["vol"]) == 15 or
            _ival(ev["keyon"]) == 0 or
            _ival(ev["fnum"]) == 0
        )

    # --- retrigger 判定（melody のみ） ---
    def is_retrigger(ev):
        if is_rhythm(ev):
            return False
        return (_ival(ev["keyon"]) == 1 and _ival(ev["vol"]) != 15)

    # チャンネルごとに処理
    from collections import defaultdict
    ch_events = defaultdict(list)
    for ev in events:
        ch_events[_ival(ev.get("ch"), -1)].append(ev)

    merged_all = []

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: e["tick_start"])

        # -------------------------
        # 第1段階：休符マージ（rhythm は除外）
        # -------------------------
        stage1 = []
        for ev in buf:

            if is_rhythm(ev):
                stage1.append(ev)
                continue

            if stage1:
                prev = stage1[-1]

                if is_rhythm(prev):
                    stage1.append(ev)
                    continue

                if is_retrigger(prev) or is_retrigger(ev):
                    stage1.append(ev)
                    continue

                if is_rest(prev) and is_rest(ev):
                    prev["tick_end"] = ev["tick_end"]
                    prev["l"] = prev["tick_end"] - prev["tick_start"]
                    continue

            stage1.append(ev)

        # -------------------------
        # 第2段階：vol=15 マージ（rhythm は除外）
        # -------------------------
        stage2 = []
        for ev in stage1:

            if is_rhythm(ev):
                stage2.append(ev)
                continue

            if stage2:
                prev = stage2[-1]

                if is_rhythm(prev):
                    stage2.append(ev)
                    continue

                if _ival(prev["vol"]) == 15 and _ival(ev["vol"]) == 15:
                    prev["tick_end"] = ev["tick_end"]
                    prev["l"] = prev["tick_end"] - prev["tick_start"]
                    continue

            stage2.append(ev)

        merged_all.extend(stage2)

    return merged_all

def pass3_merge_same_note(events):
    """
    PASS3(3):
      同じ NOTE_ON（keyon=1, vol!=15, fnum/block/inst/vol/sus が同じ）の
      連続イベントを 1 つにマージする。
    """

    from collections import defaultdict

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # tick_start / tick_end は pass3_merge_silent_rests で付与済み
    # 念のため再計算
    for ev in events:
        ev["ticks"] = _ival(ev.get("ticks"))
        ev["l"]     = _ival(ev.get("l"))
        ev["tick_start"] = ev["ticks"]
        ev["tick_end"]   = ev["ticks"] + ev["l"]

    # マージ条件
    def can_merge(a, b):
        return (
            _ival(a["keyon"]) == 1 and _ival(b["keyon"]) == 1 and
            _ival(a["vol"]) != 15 and _ival(b["vol"]) != 15 and
            _ival(a["fnum"])  == _ival(b["fnum"])  and
            _ival(a["block"]) == _ival(b["block"]) and
            _ival(a["inst"])  == _ival(b["inst"])  and
            _ival(a["vol"])   == _ival(b["vol"])   and
            _ival(a["sus"])   == _ival(b["sus"])   and
            a["tick_end"] == b["tick_start"]
        )

    ch_events = defaultdict(list)
    for ev in events:
        ch_events[_ival(ev.get("ch"), -1)].append(ev)

    merged_all = []

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: e["tick_start"])

        merged = []
        for ev in buf:
            if merged:
                prev = merged[-1]

                if can_merge(prev, ev):
                    # マージ
                    prev["tick_end"] = ev["tick_end"]
                    prev["l"] = prev["tick_end"] - prev["tick_start"]
                    continue

            merged.append(ev)

        merged_all.extend(merged)

    return merged_all

def pass3_merge_retrigger_note(events):
    """
    PASS3(4):
      keyoff → keyon（同じ音）→ keyoff の retrigger を
      1 つの NOTE_ON にまとめる。
    """

    from collections import defaultdict

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # tick_start / tick_end は pass3 で付与済み
    for ev in events:
        ev["ticks"] = _ival(ev.get("ticks"))
        ev["l"]     = _ival(ev.get("l"))
        ev["tick_start"] = ev["ticks"]
        ev["tick_end"]   = ev["ticks"] + ev["l"]

    def same_note(a, b):
        return (
            _ival(a["fnum"])  == _ival(b["fnum"])  and
            _ival(a["block"]) == _ival(b["block"]) and
            _ival(a["inst"])  == _ival(b["inst"])  and
            _ival(a["vol"])   == _ival(b["vol"])   and
            _ival(a["sus"])   == _ival(b["sus"])
        )

    ch_events = defaultdict(list)
    for ev in events:
        ch_events[_ival(ev.get("ch"), -1)].append(ev)

    merged_all = []

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: e["tick_start"])

        merged = []
        i = 0
        while i < len(buf):
            ev = buf[i]

            # retrigger パターンを検出
            if (
                i + 2 < len(buf) and
                _ival(ev["keyon"]) == 1 and
                _ival(buf[i+1]["keyon"]) == 0 and
                _ival(buf[i+2]["keyon"]) == 1 and
                same_note(ev, buf[i+2]) and
                ev["tick_end"] == buf[i+1]["tick_start"] and
                buf[i+1]["tick_end"] == buf[i+2]["tick_start"]
            ):
                # マージ：ev の終端を buf[i+2] に伸ばす
                ev["tick_end"] = buf[i+2]["tick_end"]
                ev["l"] = ev["tick_end"] - ev["tick_start"]

                merged.append(ev)
                i += 3
                continue

            merged.append(ev)
            i += 1

        merged_all.extend(merged)

    return merged_all

def pass3_merge_pitch_volume_wobble(events):
    """
    PASS3(5):
      block + scale が同じ範囲での fnum/vol の微揺れをマージする。
      （ソフトウェアエンベロープや微ピッチ揺れの除去）
    """

    from collections import defaultdict

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # tick_start / tick_end は PASS3 で付与済み
    for ev in events:
        ev["tick_start"] = _ival(ev["ticks"])
        ev["tick_end"]   = ev["tick_start"] + _ival(ev["l"])

    def can_merge(a, b):
        return (
            _ival(a["keyon"]) == 1 and _ival(b["keyon"]) == 1 and
            _ival(a["vol"]) != 15 and _ival(b["vol"]) != 15 and
            _ival(a["block"]) == _ival(b["block"]) and
            get_scale(_ival(a["fnum"])) == get_scale(_ival(b["fnum"])) and
            _ival(a["inst"]) == _ival(b["inst"]) and
            _ival(a["sus"])  == _ival(b["sus"]) and
            a["tick_end"] == b["tick_start"]
        )

    ch_events = defaultdict(list)
    for ev in events:
        ch_events[_ival(ev.get("ch"), -1)].append(ev)

    merged_all = []

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: e["tick_start"])

        merged = []
        for ev in buf:
            if merged:
                prev = merged[-1]

                if can_merge(prev, ev):
                    # マージ：prev の終端を ev に伸ばす
                    prev["tick_end"] = ev["tick_end"]
                    prev["l"] = prev["tick_end"] - prev["tick_start"]
                    continue

            merged.append(ev)

        merged_all.extend(merged)

    return merged_all

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


def compute_min_l(events):
    # ------------------------------------------------------------
    # 1. [EXP1] 最小音価クラスタ(min_l)の推定（全チャンネル共通）
    # ------------------------------------------------------------
    def _ival(v, default=0):
        try:
            return int(v)
        except:
            return default
    l_vals = []
    for ev in events:
        ch     = _ival(ev.get("ch"))
        l      = _ival(ev.get("l"))
        keyon  = _ival(ev.get("keyon"))
        vol    = _ival(ev.get("vol"))
        is_ryt = _ival(ev.get("is_ryt"))
        ev_type = ev.get("#type")

        # --- 無音(vol=15)は除外 ---
        if vol == 15:
            continue


        # --- instVol(keyon=1) と keyBlk(keyon=0) の両方を対象にする ---
        if l > 0:
            l_vals.append(l)

    if not l_vals:
        min_l_base = 1.0
        base  = 1
        cluster = []
    else:
        # 全チャンネルの最小値 base
        base = min(l_vals)

        # b = base + 1 : 1 tick分の揺らぎを抑える
        b = base + 1
        cluster_base = [lv for lv in l_vals if lv == base]
        cluster_b =    [lv for lv in l_vals if lv == b]
        if cluster_b:
            cluster = cluster_base + cluster_b
            min_l_base = int(sum(cluster) / len(cluster)) 
        else:
            cluster = []
            min_l_base = float(base)
    # min_l補正 + 0.4
    min_l = int(min_l_base) + 0.4
    print(f"       min_l={min_l}, min_l_base={min_l_base}, base={base}, cluster={cluster[:40]}")

    return base, min_l


def compute_min_l_from_one_l_cluster(events):
    # ------------------------------------------------------------
    # [EXP2] min_l を「最小クラスタ」から決定
    # ------------------------------------------------------------

    def _ival(v, default=0):
        try:
            return int(v)
        except:
            return default

    # --- l 値を収集 ---
    l_vals = []
    for ev in events:
        ch      = _ival(ev.get("ch"))
        l       = _ival(ev.get("l"))
        vol     = _ival(ev.get("vol"))
        ev_type = ev.get("#type")

        # 無音は除外
        if vol == 15:
            continue

        # rhythm は除外
        if ev_type == "rhythm":
            continue

        # l>0 のみ対象
        if l > 0:
            l_vals.append(l)

    if not l_vals:
        return None, None, None, None

    ls_sorted = sorted(l_vals)

    ls = [ _ival(ev.get("l")) for ev in events
           if _ival(ev.get("keyon")) == 1 and _ival(ev.get("l")) > 0 ]

    if not ls:
        return events, None, None

    ls_sorted = sorted(ls)

    # base は 2 以上の最小値（l=1 の誤差を除外）
    for v in ls_sorted:
        if v >= 2:
            base = v
            break

    cluster = [l for l in ls_sorted if l == base or l == base + 1]
    min_l_base_exp2 = sum(cluster) / len(cluster)
    # min_lを小数切り捨てにして、+0.4 補正
    min_l = int(min_l_base_exp2) + 0.4

    print(f"       min_l={min_l}, min_l_base={min_l_base_exp2},base={base}, cluster={cluster[:40]}")
    return base, min_l


def compute_min_l_from_two_l_clusters(events):
    # ------------------------------------------------------------
    # [EXP3]  最小音価クラスタ(min_l)及び最小次点音価クラスタから推定
    # ------------------------------------------------------------
    def _ival(v, default=0):
        try:
            return int(v)
        except:
            return default

    # --- l 値を収集 ---
    l_vals = []
    for ev in events:
        ch      = _ival(ev.get("ch"))
        l       = _ival(ev.get("l"))
        vol     = _ival(ev.get("vol"))
        ev_type = ev.get("#type")

        # 無音は除外
        if vol == 15:
            continue

        # rhythm は除外
        if ev_type == "rhythm":
            continue

        # l>0 のみ対象
        if l > 0:
            l_vals.append(l)

    if not l_vals:
        return None, None, None, None

    ls_sorted = sorted(l_vals)

    # --- base（最小クラスタの基準） ---
    base = None
    for v in ls_sorted:
        if v >= 2:
            base = v
            break

    if base is None:
        return None, None, None, None

    # --- クラスタ1（base/base+1） ---
    cluster1 = [l for l in ls_sorted if l == base or l == base + 1]
    avg1_base = sum(cluster1) / len(cluster1) if cluster1 else float(base)

    # --- base2（次点クラスタの基準） ---
    base2 = None
    for v in ls_sorted:
        if v > base:
            base2 = v
            break

    if base2 is None:
        return base, avg1, None, None

    # --- クラスタ2（base2/base2+1） ---
    cluster2 = [l for l in ls_sorted if l == base2 or l == base2 + 1]
    avg2_base = sum(cluster2) / len(cluster2) if cluster2 else float(base2)


    avg1 = int(avg1_base) + 0.4
    avg2 = int(avg2_base) + 0.4

    min_l_base = (avg1_base + avg2_base) /2
    min_l = int(min_l_base) + 0.4
    print(f"       avg1={avg1}, avg1_base={avg1_base},base={base}, cluster={cluster1[:40]}")
    print(f"       avg2={avg2}, avg2_base={avg2_base},base={base2}, cluster={cluster2[:40]}")
    print(f"       min_l={min_l}, min_l_base={min_l_base},avg1={avg1},avg2={avg2}")

    return base, min_l


def pass4_compute_beats(events):
    """
    PASS4:
      1. min_l（beat 正規化用）を決定
      2. beat / beat_pos を計算（min_l ベース）
      3. fb/kb/vb を計算
      4. min_ioi（音符長の最小単位）を計算
      5. tempo（BPM）を IOI ベースで計算し ev["tempo"] に格納
    """

    def _ival(v, default=0):
        if v is None or v == "":
            return default
        try:
            return int(v)
        except:
            return default

    # ------------------------------------------------------------
    # 1. min_l を 最小値又は「最小クラスタ」から決定
    # ------------------------------------------------------------
    base_exp1, min_l_exp1 = compute_min_l(events)

    min_l = min_l_exp1
    base  = base_exp1
    print(f"[PASS4]        min_l={min_l} base={base}")

    # ------------------------------------------------------------
    # 2. beat（音価）を min_l から計算（切り捨て）
    # ------------------------------------------------------------
    for ev in events:
        l = _ival(ev.get("l"))

        if l > 0:
            ev["beat"] = max(1, int(l / min_l))
        else:
            ev["beat"] = 0  # retrigger or 無音

    # ------------------------------------------------------------
    # 3. beat_pos（チャンネル独立）
    # ------------------------------------------------------------
    from collections import defaultdict
    ch_events = defaultdict(list)

    for ev in events:
        ch_events[_ival(ev.get("ch"), -1)].append(ev)

    for ch, buf in ch_events.items():
        buf.sort(key=lambda e: _ival(e.get("ticks")))
        beat_pos = 0
        for ev in buf:
            ev["beat_pos"] = beat_pos
            beat_pos += ev["beat"]

    # ------------------------------------------------------------
    # 4. fb/kb/vb（レガート対応）
    # ------------------------------------------------------------
    ch_events_fb = defaultdict(list)
    for ev in events:
        ch_events_fb[_ival(ev.get("ch"), -1)].append(ev)

    for ch, buf in ch_events_fb.items():
        buf.sort(key=lambda e: _ival(e.get("ticks")))

        fnum_prev  = None
        block_prev = None
        inst_prev  = None
        vol_prev   = None
        keyon_prev = 0

        fb_acc = kb_acc = vb_acc = 0

        for ev in buf:
            beat  = _ival(ev.get("beat"))
            fnum  = _ival(ev.get("fnum"))
            block = _ival(ev.get("block"))
            inst  = _ival(ev.get("inst"))
            vol   = _ival(ev.get("vol"))
            keyon = _ival(ev.get("keyon"))

            if keyon == 0:
                fb_acc = kb_acc = vb_acc = beat
            else:
                if keyon_prev == 1 and fnum == fnum_prev and block == block_prev and inst == inst_prev and vol == vol_prev:
                    fb_acc += beat
                    kb_acc += beat
                    vb_acc += beat
                else:
                    fb_acc = kb_acc = vb_acc = beat

            ev["fb"] = fb_acc
            ev["kb"] = kb_acc
            ev["vb"] = vb_acc

            keyon_prev = keyon
            fnum_prev  = fnum
            block_prev = block
            inst_prev  = inst
            vol_prev   = vol

    # ------------------------------------------------------------
    # 5. min_ioi（音符長の最小単位）を計算
    # ------------------------------------------------------------
    ioi_list = [ _ival(ev.get("ioi")) for ev in events if _ival(ev.get("ioi")) > 0 ]

    if not ioi_list:
        print("[PASS4] No IOI found")
        return events, min_l, None

    # mode が最も安定（最頻値）
    from statistics import mode, median
    try:
        min_ioi = mode(ioi_list)
    except:
        min_ioi = median(ioi_list)

    print(f"[PASS4] min_ioi={min_ioi}")

    # ------------------------------------------------------------
    # 6. tempo（BPM）を IOI ベースで計算
    # ------------------------------------------------------------
    # tick=1/60sec → dt = ioi / 60
    # BPM = 60 / dt = 3600 / ioi
    bpm_per_grid = 3600.0 / min_ioi

    # grid_per_beat は min_l ベースの beat_pos から推定
    beat_pos_all = sorted(set(ev["beat_pos"] for ev in events if ev["beat"] > 0))
    intervals = [b - a for a, b in zip(beat_pos_all, beat_pos_all[1:]) if (b - a) > 0]

    if intervals:
        try:
            grid_per_beat = mode(intervals)
        except:
            grid_per_beat = int(round(median(intervals)))
    else:
        grid_per_beat = 4  # fallback

    bpm_raw = bpm_per_grid / grid_per_beat
    bpm_clamped = max(30.0, min(225.0, bpm_raw))

    print(f"[PASS4 BPM] min_ioi={min_ioi}, bpm_raw={bpm_raw:.3f}, bpm={bpm_clamped:.3f}")

    # ------------------------------------------------------------
    # 7. tempo を ev に書き込む
    # ------------------------------------------------------------
    for ev in events:
        ev["tempo"] = bpm_clamped

    # ------------------------------------------------------------
    # rhythm の beat_pos を melody に同期させる
    # ------------------------------------------------------------
    events_sorted = sorted(events, key=lambda e: _ival(e.get("ticks")))
    last_melody_beat_pos = 0

    for ev in events_sorted:
        is_ryt = ev.get("is_ryt") == 1 or ev.get("#type") == "rhythm"

        if not is_ryt:
            # melody
            last_melody_beat_pos = _ival(ev.get("beat_pos"))
        else:
            # rhythm
            ev["beat_pos"] = last_melody_beat_pos

    return events, min_l, bpm_clamped

def pass4_dump_csv(events, trace_csv_path):

    if not events:
        return

    # 元ファイル名: <stem>_trace.opll.csv
    base, ext = os.path.splitext(trace_csv_path)
    # 出力ファイル名: <stem>_trace.opll.pass4.csv
    out_csv_path = base + ".pass4.csv"

    fieldnames = list(events[0].keys())

    with open(out_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)

    print(f"[PASS4] Debug CSV written: {out_csv_path}")

def pass5_dump_csv(events, trace_csv_path):

    if not events:
        return

    # 元ファイル名: <stem>_trace.opll.csv
    base, ext = os.path.splitext(trace_csv_path)
    # 出力ファイル名: <stem>_trace.opll.pass4.csv
    out_csv_path = base + ".pass5.csv"

    fieldnames = list(events[0].keys())

    with open(out_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)

    print(f"[PASS5] Debug CSV written: {out_csv_path}")


def _int(ev, key, default=0):
    """空文字列や欠損値を安全に int に変換するヘルパー"""
    v = ev.get(key, default)
    if v == '' or v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _float(ev, key, default=0.0):
    """空文字列や欠損値を安全に float に変換するヘルパー"""
    v = ev.get(key, default)
    if v == '' or v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _opll_vol_to_ms2(vol_opll: int) -> int:
   """
    OPLL vol: 0 (max) ... 15 (mute)
    MS2 file VOL: 0 (max) ... 63 (mute)  ← OPL4 TL 値そのまま
    """
   v = int(round(max(0, min(15, vol_opll)) * 63 / 15))
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


def _build_segments(trace_csv_path,
                    include_rhythm=True,
                    rhythm_reg_timeline=None):
    # ------------------------------------------------------
    # vgmの情報 (trace_csv_path)からevent情報を作成
    # ------------------------------------------------------
    # pass0 : <stem>_trace.opll.csvをeventsに読み込む
    events = pass0_read_csv(trace_csv_path)

    # pass1 : ticksの差分から各eventのlを算出
    # Next eventのticks = ticks + l
    events = pass1_compute_l(events)
    pass1_dump_csv(events, trace_csv_path)

    # pass2 (1): fl, kl, vlの算出
    # fl : 同じfnumが継続する期間[ticks]
    # kl : 同じkeyonが継続する期間[ticks]
    # vl : 同じvolが継続する期間[ticks]
    # 但しこれらはkeyon 0->1でlと同じ値にリセットされる
    events = pass2_compute_fl_kl_vl(events)

    # onsetの検出とIOI(Inter‑Onset Interval:IOIの間隔)の算出, tempoの導出
    events = pass2_compute_onsets_and_ioi(events)

    # type="rhythm", ch=-1のeventをRHYTHM_CH_MAP, RHYTHM_VOICE_ID_MAPをベースに展開
    events = pass2_expand_rhythm(events,RHYTHM_CH_MAP,RHYTHM_VOICE_ID_MAP)
    # 展開後のchについてlの再計算
    events = pass2_recompute_rhythm_lengths(events)

    # pass2 (2): fl,kl,vlを計算した後にl=0のeventを削除
    # ここで l=0 の keyon 0->1 (retrigger)のeventは削除される。(fl,kl,vlにはリセットとして反映済み)
    events = pass2_filter_l0(events)

    # l=0を削除した後、fl,kl,blからlegato, vibrate, volume envelopeを判定
    events = pass2_mark_legato_vibrato_envelope(events)
    pass2_dump_csv(events, trace_csv_path)

    # 無音状態(v=15 又は keyon=0) の連続したeventをマージ
    events = pass3_merge_silent_rests(events)
    # fnum, vol, keyon状態が続くNOTEをマージ
    #events = pass3_merge_same_note(events)
    # retriggerのためのkeyoffを一つのeventにマージ
    events = pass3_merge_retrigger_note(events) 
    # fnumの揺れ(vibrato / pitch envelope ), software envelopをマージ
    #events = pass3_merge_pitch_volume_wobble(events)
    pass3_dump_csv(events, trace_csv_path)

    # beat, beat_posを算出
    # beatは最小の音価 (min_l)で正規化した単位。grid幅を示す
    # beat_pos は前のeventのbeatの累計。そのeventのスタート地点を表す
    events, min_l, bpm = pass4_compute_beats(events)
    pass4_dump_csv(events, trace_csv_path)

    # ------------------------------------------------------
    # pass4まで処理されたeventsをベースに_Segmentを生成する
    # ------------------------------------------------------
    events_by_ch = defaultdict(list)

    for ev in events:
        ev_type = ev.get("#type", "")
        ch = int(ev["ch"])
        is_ryt = _int(ev, "is_ryt", 0)

        # まず ch 範囲チェック（安全のため最初に）
        if ch < -1 or ch >= NUM_CH:
            continue

        # ------------------------------------------------------
        # 1. リズムイベント（0x0E の行）だけ特別扱い
        # ------------------------------------------------------
        if ev_type == "rhythm":
            if is_ryt != 1:
                continue

            if _int(ev, "bd", 0) == 1:
                ch = 6
            elif _int(ev, "sd", 0) == 1:
                ch = 7
            elif (_int(ev, "tom", 0) == 1 or
                  _int(ev, "tc", 0) == 1 or
                  _int(ev, "hh", 0) == 1):
                ch = 8
            else:
                # NOTE_ON ではない rhythm 行は無視
                continue

            events_by_ch[ch].append(ev)
            continue

        # ------------------------------------------------------
        # 2. メロディイベント（#type != rhythm）
        # ------------------------------------------------------
        # ch0〜5 → 常にメロディ
        if 0 <= ch <= 5:
            events_by_ch[ch].append(ev)
            continue

        # ------------------------------------------------------
        # 3. ch6〜8 の通常レジスタ書き込み
        # ------------------------------------------------------
        # rhythm_mode=1 のときは「イベントではない」
        if is_ryt == 1:
            continue

        # rhythm_mode=0 のときはメロディ扱い（VGM によっては使われる）
        if 6 <= ch <= 8:
            events_by_ch[ch].append(ev)
            continue

        # それ以外は無視
        continue

    # ticks でソート
    for ch in events_by_ch:
        events_by_ch[ch].sort(key=lambda e: int(e["ticks"]))

    segments_by_ch = {ch: [] for ch in range(NUM_CH)}

    for ch, evs in events_by_ch.items():
        prev_seg = None

        for ev in evs:
            ev_type = ev.get("#type", "")
            vol_opll = _int(ev, "vol", 15)
            is_ryt   = _int(ev, "is_ryt", 0)

            # --- イベント種別判定 ---
            is_rhythm_on = (ev_type == "rhythm") and (is_ryt == 1)
            is_melody_on = (ev_type != "rhythm") and (0 <= ch < NUM_CH)

            if not (is_melody_on or (include_rhythm and is_rhythm_on)):
                continue

            ms2_vol = _opll_vol_to_ms2(vol_opll)

            seg = _Segment(
                ev_type       = ev["#type"],
                time          = float(ev["time"]),
                ch            = _int(ev, "ch", 0),
                ticks         = _int(ev, "ticks", 0),
                onset         = _int(ev, "onset", 0),
                tempo         = _int(ev, "tempo", 0),
                ioi           = _int(ev, "ioi", 0),
                min_ioi       = _int(ev, "min_ioi", 0),
                l             = _int(ev, "l", 0),
                fl            = _int(ev, "fl", 0),
                kl            = _int(ev, "kl", 0),
                vl            = _int(ev, "vl", 0),
                beat_pos      = _int(ev, "beat_pos", 0),
                beat          = _int(ev, "beat", 0),
                fb            = _int(ev, "fb", 0),
                kb            = _int(ev, "kb", 0),
                vb            = _int(ev, "vb", 0),
                keyon         = _int(ev, "keyon", 0),
                is_legate     = _int(ev, "is_legate", 0),
                is_vibrato    = _int(ev, "is_vibrato", 0),
                is_envelope   = _int(ev, "is_envelope", 0),
                fnum          = _int(ev, "fnum", 0),
                block         = _int(ev, "block", 0),
                inst          = _int(ev, "inst", 0),
                vol           = _int(ev, "vol", 0),
                sus           = _int(ev, "sus", 0),
                scale         = ev["scale"],
                is_ryt        = _int(ev, "is_ryt", 0),
                r_tempo       =  _int(ev, "r_tempo", 0),
                r_ioi         = _int(ev, "r_ioi", 0),
                r_mode_ioi    = _int(ev, "r_mode_ioi", 0),
                r_hh_ioi      = _int(ev, "r_hh_ioi", 0),
                r_hh_mode_ioi = _int(ev, "r_hh_mode_ioi", 0),
                bd            = _int(ev, "bd", 0),
                sd            = _int(ev, "sd", 0),
                tom           = _int(ev, "tom", 0),
                tc            = _int(ev, "tc", 0),
                hh            = _int(ev, "hh", 0),
                fnum_ch6      = _int(ev, "fnum_ch6", 0),
                vol_ch6       = _int(ev, "vol_ch6", 0),
                sus_ch6       = _int(ev, "sus_ch6", 0),
                block_ch6     = _int(ev, "block_ch6", 0),
                fnum_ch7      = _int(ev, "fnum_ch7", 0),
                vol_ch7       = _int(ev, "vol_ch7", 0),
                sus_ch7       = _int(ev, "sus_ch7", 0),
                block_ch7     = _int(ev, "block_ch7", 0),
                fnum_ch8      = _int(ev, "fnum_ch8", 0),
                vol_ch8       = _int(ev, "vol_ch8", 0),
                sus_ch8       = _int(ev, "sus_ch8", 0),
                block_ch8     = _int(ev, "block_ch8", 0),
            )

            seg.tick_start = _int(ev, "ticks", 0)
            seg.tick_end   = _int(ev, "ticks", 0) + _int(ev, "l", 0) 
            seg.beat_start = _int(ev, "beat_pos", 0)
            seg.beat_end   = _int(ev, "beat_pos", 0) + _int(ev, "beat", 0)
            seg.grid_sz    = 1
            seg.ms2_vol    = ms2_vol
            seg.ms2_len    = _int(ev, "l", 0)
            seg.min_l      = min_l
            seg.beat_sz    = min_l
            seg.grid_sz    = 1

            if prev_seg:
                seg.odiff = seg.block  - prev_seg.block
                seg.vdiff = seg.vol - prev_seg.vol
            else:
                seg.odiff = 0
                seg.vdiff = 0

            prev_seg = seg
            segments_by_ch[ch].append(seg)

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