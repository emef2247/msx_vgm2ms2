# tools/check_songsettings.py
import struct, sys
fn = sys.argv[1]
b = open(fn,'rb').read()
pos=8
while pos+4 <= len(b):
    typ,ver,size = struct.unpack_from('<BBH', b, pos)
    if typ==0: break
    if typ==1:
        data = b[pos+4:pos+4+size]
        n_channels = data[71]
        twoop_ch = data[72]
        print('SongSettings n_channels:', n_channels, '2OP channels field:', twoop_ch)
        break
    pos += 4+size

