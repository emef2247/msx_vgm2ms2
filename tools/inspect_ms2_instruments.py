#!/usr/bin/env python3
# usage: python tools/inspect_ms2_instruments.py out_file.ms2
import sys, struct
fn = sys.argv[1] if len(sys.argv) > 1 else 'out_test.ms2'
data = open(fn,'rb').read()
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
            rec = payload[off:off+recsize]
            name = rec[14:30].decode('ascii','replace').rstrip()
            print(f'  {i:3d}: {name}')
        break
    pos += 4 + size
if not found:
    print('No instruments chunk (type 3) found in', fn)

