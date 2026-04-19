#!/usr/bin/env python3
"""
Quick test: call ms2_writer.build_ms2_file with rhythm_instruments and dump
the instruments chunk so we can verify appended R:BD / R:SD / ... entries.
Run this from the repository root.
"""
import os
import sys
import struct

# Ensure repo root is on sys.path so `from py import ms2_writer` works.
repo_root = os.path.dirname(os.path.dirname(__file__)) if __file__.startswith('tools') else os.getcwd()
sys.path.insert(0, repo_root)

from py import ms2_writer

# minimal empty segments
segments = {ch: [] for ch in range(ms2_writer.NUM_CH)}
rhythm = {
    'BD':  '20200900FAF6FFFF000000',
    'SD':  '20000000F1F700B80000000E',
    'TOM': '020200003333737300000003',
    'TCT': '43614100F9F6FCFC00000000',
    'HH':  '2F2F0000F1A900480000000E',
}
out = sys.argv[1] if len(sys.argv) > 1 else 'out_test.ms2'
b = ms2_writer.build_ms2_file(
    segments=segments,
    voice_csv_path=None,
    stem='test_rhythm',
    bpm=120,
    timer=0,
    base_ms2=None,
    video='ntsc',
    debug=True,
    rhythm_instruments=rhythm,
)
open(out,'wb').write(b)
print('Wrote', out)

# Inspect instruments chunk (type 3)
data = b
pos = 8
found = False
while pos + 4 <= len(data):
    typ, ver, size = struct.unpack_from('<BBH', data, pos)
    if typ == 0:
        break
    if typ == 3:
        found = True
        payload = data[pos+4:pos+4+size]
        n_inst = struct.unpack_from('<H', payload, 0)[0]
        print('2OP instrument count:', n_inst)
        recsize = 30
        for i in range(n_inst):
            off = 2 + i*recsize
            name = payload[off+14:off+30].decode('ascii','replace').rstrip()
            print(f'  {i:3d}: {name}')
        break
    pos += 4 + size
if not found:
    print('No instruments chunk (type 3) found in', out)