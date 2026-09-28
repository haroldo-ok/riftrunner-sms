#!/usr/bin/env python3
"""smsvideo — encode a sequence of pre-rendered frames into an SMS tile stream
(a shared tile dictionary + per-position deltas + periodic snapshots), plus a
verifier that replays the *emitted* bytes and reports the error.

This is the reusable core of the "pre-rendered, simulation-indexed video"
technique (see references/prerendered-video.md). It is deliberately
game-agnostic: it knows nothing about physics, sprites, or where `pos` comes
from. You render frames offline, run this to get `video_data.{c,h}` +
`bank_data.bin`, and drive playback at run time with smsvideo.c by feeding it a
position produced by your own simulation.

INPUT (game-agnostic): a raw file of N frames, each WIN_W x WIN_H bytes of
palette indices 0..15 (row-major), WIN_W and WIN_H multiples of 8. Quantising
your art down to <=16 colours is your job (it's content-specific); a --palette
of 16 SMS colour bytes is passed through to the header if you supply one.

STREAM FORMAT  (decoded by smsvideo.c)
    delta, per run:  <skip u8> <count u8> <count x u16 tile_id LE>
                     <255> <0>  = long-skip escape: advance 254, keep going
                     <0>  <0>   = end of this position's delta
    snapshot:        VID_CELLS x u16 tile_id LE   (full window state)
Tiles, snapshots and deltas are packed into 16 KiB banks (512 tiles/bank);
each snapshot and each delta lies wholly within one bank. Bank N of the blob is
paged in with SMS_mapROMBank(DATA_BANK0 + N).

Usage:
  smsvideo.py encode --frames F.bin --w 256 --h 96 --nframes N \\
        [--tile-x 0 --tile-y 6] [--cell-tile0 0] [--data-bank0 2] \\
        [--snapivl 8] [--thresh 8] [--maxupd 22] [--blank-tile N] \\
        [--palette pal.bin] [--out gen]
  smsvideo.py verify --frames F.bin --w 256 --h 96 --nframes N --out gen
"""
import argparse, os, struct, sys

BANK = 16384

def planar(tile64):
    """8x8 list/bytes of indices 0..15 -> 32 bytes SMS 4bpp planar."""
    out = bytearray()
    for y in range(8):
        b = [0, 0, 0, 0]
        for x in range(8):
            v = tile64[y * 8 + x] & 0xF
            for p in range(4):
                if v & (1 << p):
                    b[p] |= 0x80 >> x
        out += bytes(b)
    return bytes(out)

def load_frames(path, w, h, n):
    data = open(path, 'rb').read()
    need = w * h * n
    if len(data) < need:
        sys.exit(f'frames file has {len(data)} bytes, need {need} '
                 f'({n} x {w} x {h})')
    return data, need

def cellize(data, w, h, n):
    """-> (ids[n][TW*TH], tilepix list id->bytes(64), TW, TH)."""
    TW, TH = w // 8, h // 8
    NC = TW * TH
    dic = {}          # 64-byte tuple -> id
    tilepix = []      # id -> bytes(64)
    ids = []
    for f in range(n):
        base = f * w * h
        row = [0] * NC
        for ty in range(TH):
            for tx in range(TW):
                cell = bytearray(64)
                for yy in range(8):
                    src = base + (ty * 8 + yy) * w + tx * 8
                    cell[yy * 8:yy * 8 + 8] = data[src:src + 8]
                key = bytes(cell)
                tid = dic.get(key)
                if tid is None:
                    tid = len(tilepix)
                    dic[key] = tid
                    tilepix.append(key)
                row[ty * TW + tx] = tid
        ids.append(row)
    return ids, tilepix, TW, TH

def pixdiff(a, b):
    return sum(1 for i in range(64) if a[i] != b[i])

def build_deltas(ids, tilepix, NC, snapivl, thresh, maxupd):
    """Replay the encoder/decoder contract; return (deltas[bytes], snaps[list],
    snap_index_of_frame, upd_counts) with the same lossy caps the verifier and
    runtime will see."""
    deltas, snaps, upd = [], [], []
    shown = [-1] * NC
    for f in range(len(ids)):
        cur = ids[f]
        if f == 0:
            # The first frame is the decoder's known starting state.
            shown = list(cur)
            changed = []
        else:
            # Keyframes are seek checkpoints, not holes in the forward stream.
            # Continue the same bounded delta path on every frame, including a
            # checkpoint, so playback never silently skips one position.
            cand = [i for i in range(NC) if cur[i] != shown[i]]
            changed = [i for i in cand
                       if pixdiff(tilepix[cur[i]], tilepix[shown[i]]) > thresh]
            if len(changed) > maxupd:               # keep the most-different
                changed.sort(key=lambda i: -pixdiff(tilepix[cur[i]],
                                                     tilepix[shown[i]]))
                changed = sorted(changed[:maxupd])
            for i in changed:
                shown[i] = cur[i]
        if f % snapivl == 0:
            # Save exactly the state the sequential decoder reaches here. This
            # keeps arbitrary seeks and forward playback on the same trajectory.
            snaps.append(list(shown))
        upd.append(len(changed))
        # emit RLE runs of consecutive changed cells
        buf = bytearray()
        i = 0
        cursor = 0
        while i < len(changed):
            j = i
            while (j + 1 < len(changed) and changed[j + 1] == changed[j] + 1
                   and (j - i + 1) < 255):
                j += 1
            skip = changed[i] - cursor
            while skip >= 255:
                buf += bytes((255, 0))
                skip -= 254
            buf.append(skip)
            buf.append(j - i + 1)
            for k in range(i, j + 1):
                buf += struct.pack('<H', cur[changed[k]])
            cursor = changed[j] + 1
            i = j + 1
        buf += bytes((0, 0))
        deltas.append(bytes(buf))
    return deltas, snaps, upd

def pack_banks(tilepix, snaps, deltas):
    """Pack tiles (512/bank), then snapshots, then deltas. Snapshots/deltas
    never straddle a bank. Returns (blob, snap_loc, frame_loc, n_tile_banks)
    where *_loc = (bank_index, addr) and addr = 0x8000 + offset_in_bank."""
    tiledata = b''.join(planar(t) for t in tilepix)
    banks = []
    for i in range(0, len(tilepix), 512):
        banks.append(bytearray(tiledata[i * 32:(i + 512) * 32]))
    if not banks:
        banks.append(bytearray())
    n_tile_banks = len(banks)

    def place(blob):
        if len(banks[-1]) + len(blob) > BANK:
            banks.append(bytearray())
        b = len(banks) - 1
        addr = 0x8000 + len(banks[-1])
        banks[-1] += blob
        return b, addr

    snap_loc = [place(struct.pack('<%dH' % len(s), *s)) for s in snaps]
    frame_loc = [place(d) for d in deltas]

    blob = b''.join(bytes(b) + b'\0' * (BANK - len(b)) for b in banks)
    return blob, snap_loc, frame_loc, n_tile_banks

def carr(name, data, typ='unsigned char', per=16, fmt='0x%02x'):
    s = [f'const {typ} {name}[] = {{']
    for i in range(0, len(data), per):
        s.append('  ' + ','.join(fmt % v for v in data[i:i + per]) + ',')
    return '\n'.join(s + ['};', ''])

def do_encode(a):
    data, _ = load_frames(a.frames, a.w, a.h, a.nframes)
    ids, tilepix, TW, TH = cellize(data, a.w, a.h, a.nframes)
    NC = TW * TH
    if len(tilepix) > 65535:
        sys.exit('tile dictionary > 65535 — window too big / too varied')
    deltas, snaps, upd = build_deltas(ids, tilepix, NC,
                                      a.snapivl, a.thresh, a.maxupd)
    blob, snap_loc, frame_loc, ntb = pack_banks(tilepix, snaps, deltas)

    maxdelta = max(len(d) for d in deltas)
    mean = sum(upd) / len(upd)
    print(f'{a.nframes} frames, {NC} cells, {len(tilepix)} tiles '
          f'({len(tilepix)*32/1024:.1f} KB)')
    print(f'tiles/position: mean {mean:.1f} max {max(upd)} (paced display-on budget ~20/frame)')
    print(f'delta bytes: mean {sum(len(d) for d in deltas)//len(deltas)} '
          f'max {maxdelta}')
    print(f'{len(snaps)} snapshots x {NC*2} B; blob {len(blob)//1024} KB '
          f'({len(blob)//BANK} banks, tiles in 0..{ntb-1})')

    pal = list(open(a.palette, 'rb').read()[:16]) if a.palette else \
        [(i | (i << 2) | (i << 4)) for i in range(16)]   # grey ramp fallback
    pal += [0] * (16 - len(pal))

    os.makedirs(a.out, exist_ok=True)
    # bank indices are ROM-absolute: blob bank N lives at ROM bank DATA_BANK0+N
    frame_bank = [a.data_bank0 + b for b, _ in frame_loc]
    frame_ofs = [o for _, o in frame_loc] + [frame_loc[-1][1] +
                                             len(deltas[-1])]  # +1 sentinel
    snap_bank = [a.data_bank0 + b for b, _ in snap_loc]
    snap_ofs = [o for _, o in snap_loc]

    with open(os.path.join(a.out, 'video_data.h'), 'w') as fh:
        fh.write('/* generated by smsvideo.py -- do not edit */\n'
                 '#ifndef VIDEO_DATA_H\n#define VIDEO_DATA_H\n\n')
        for k, v in [('VID_FRAMES', a.nframes), ('VID_CELLS', NC),
                     ('VID_TW', TW), ('VID_TH', TH),
                     ('VID_NTILES', len(tilepix)), ('VID_MAXDELTA', maxdelta),
                     ('CELL_TILE0', a.cell_tile0), ('BLANK_TILE', a.blank_tile),
                     ('WIN_TILE_X', a.tile_x), ('WIN_TILE_Y', a.tile_y),
                     ('DATA_BANK0', a.data_bank0),
                     ('SNAPIVL', a.snapivl), ('NSNAPS', len(snaps))]:
            fh.write(f'#define {k:<14} {v}\n')
        fh.write('\nextern const unsigned char bg_palette[16];\n'
                 'extern const unsigned char frame_bank[VID_FRAMES];\n'
                 'extern const unsigned int  frame_ofs[VID_FRAMES + 1];\n'
                 'extern const unsigned char snap_bank[NSNAPS];\n'
                 'extern const unsigned int  snap_ofs[NSNAPS];\n\n#endif\n')

    with open(os.path.join(a.out, 'video_data.c'), 'w') as fh:
        fh.write('/* generated by smsvideo.py -- do not edit */\n'
                 '#include "video_data.h"\n\n')
        fh.write(carr('bg_palette', pal))
        fh.write(carr('frame_bank', frame_bank))
        fh.write(carr('frame_ofs', frame_ofs, 'unsigned int', 8, '0x%04x'))
        fh.write(carr('snap_bank', snap_bank))
        fh.write(carr('snap_ofs', snap_ofs, 'unsigned int', 8, '0x%04x'))

    open(os.path.join(a.out, 'bank_data.bin'), 'wb').write(blob)
    print(f'wrote {a.out}/video_data.h, video_data.c, bank_data.bin')

def do_verify(a):
    """Replay the EMITTED bank_data.bin against the ideal frames."""
    data, _ = load_frames(a.frames, a.w, a.h, a.nframes)
    ids, tilepix, TW, TH = cellize(data, a.w, a.h, a.nframes)
    NC = TW * TH
    blob = open(os.path.join(a.out, 'bank_data.bin'), 'rb').read()
    hdr = open(os.path.join(a.out, 'video_data.h')).read()
    def d(name):
        import re
        return int(re.search(rf'#define {name}\s+(\d+)', hdr).group(1))
    DATA_BANK0, SNAPIVL = d('DATA_BANK0'), d('SNAPIVL')
    # parse the C tables back
    import re
    ctxt = open(os.path.join(a.out, 'video_data.c')).read()
    def arr(name):
        m = re.search(rf'{name}\[\] = \{{(.*?)\}};', ctxt, re.S)
        return [int(x, 0) for x in re.findall(r'0x[0-9a-fA-F]+', m.group(1))]
    frame_bank, frame_ofs = arr('frame_bank'), arr('frame_ofs')
    snap_bank, snap_ofs = arr('snap_bank'), arr('snap_ofs')

    def bank_byte(bank, addr, i):        # bank is ROM-absolute; blob starts at DATA_BANK0
        return blob[(bank - DATA_BANK0) * BANK + (addr - 0x8000) + i]
    def read_delta(pos):
        bank, off = frame_bank[pos], frame_ofs[pos]
        out = bytearray()
        i = 0
        while True:                       # copy until terminator (0,0)
            skip = bank_byte(bank, off, i); cnt = bank_byte(bank, off, i + 1)
            out += bytes((skip, cnt)); i += 2
            if skip == 255 and cnt == 0:
                continue
            if skip == 0 and cnt == 0:
                break
            for _ in range(cnt):
                out += bytes((bank_byte(bank, off, i), bank_byte(bank, off, i+1)))
                i += 2
        return bytes(out)

    shown = [-1] * NC
    total_err = 0
    worst = 0
    tot_pix = NC * 64
    history = []
    snapshot_mismatches = 0
    for f in range(a.nframes):
        if f == 0:
            sn = 0
            bank, off = snap_bank[sn], snap_ofs[sn]
            for c in range(NC):
                shown[c] = bank_byte(bank, off, c*2) | (bank_byte(bank, off, c*2+1) << 8)
        else:
            # Verify the real forward-playback path: every position, including
            # periodic checkpoint frames, is reconstructed from its delta.
            p = read_delta(f)
            k = 0; cell = 0
            while True:
                skip = p[k]; cnt = p[k+1]; k += 2
                if skip == 255 and cnt == 0: cell += 254; continue
                if skip == 0 and cnt == 0: break
                cell += skip
                for _ in range(cnt):
                    shown[cell] = p[k] | (p[k+1] << 8); k += 2; cell += 1
        history.append(list(shown))
        wrong = sum(pixdiff(tilepix[shown[c]], tilepix[ids[f][c]])
                    for c in range(NC))
        total_err += wrong; worst = max(worst, wrong)
        if f % SNAPIVL == 0:
            sn = f // SNAPIVL
            bank, off = snap_bank[sn], snap_ofs[sn]
            snap = [bank_byte(bank, off, c*2) | (bank_byte(bank, off, c*2+1) << 8)
                    for c in range(NC)]
            if snap != history[f]:
                snapshot_mismatches += 1
    mean = 100.0 * total_err / (a.nframes * tot_pix)
    print(f'verify: {a.nframes} frames, reconstructed from emitted forward deltas')
    print(f'pixel error vs ideal: mean {mean:.2f}%  worst-frame {100.0*worst/tot_pix:.2f}%')
    print(f'seek checkpoints: {len(snap_bank)} checked, mismatches {snapshot_mismatches}')
    if snapshot_mismatches:
        sys.exit('snapshot checkpoints differ from the sequential decoder')
    if mean > 8.0:
        sys.exit('error too high — lower THRESH or raise MAXUPD')
    print('OK')

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('encode', 'verify'):
        p = sub.add_parser(name)
        p.add_argument('--frames', required=True)
        p.add_argument('--w', type=int, required=True)
        p.add_argument('--h', type=int, required=True)
        p.add_argument('--nframes', type=int, required=True)
        p.add_argument('--out', default='gen')
        if name == 'encode':
            p.add_argument('--tile-x', dest='tile_x', type=int, default=0)
            p.add_argument('--tile-y', dest='tile_y', type=int, default=0)
            p.add_argument('--cell-tile0', dest='cell_tile0', type=int, default=0)
            p.add_argument('--blank-tile', dest='blank_tile', type=int, default=0)
            p.add_argument('--data-bank0', dest='data_bank0', type=int, default=2)
            p.add_argument('--snapivl', type=int, default=8)
            p.add_argument('--thresh', type=int, default=8)
            p.add_argument('--maxupd', type=int, default=20)
            p.add_argument('--palette', default=None)
    a = ap.parse_args()
    (do_encode if a.cmd == 'encode' else do_verify)(a)

if __name__ == '__main__':
    main()
