/* smsvideo — runtime player. See smsvideo.h. */
#include "SMSlib.h"
#include "smsvideo.h"

unsigned int smsvideo_shown_pos = 0xffff;

/* Deltas are small; stage one in RAM so the decode loop pages the frame bank
 * in once, then only switches banks when a tile's own bank changes (roughly
 * once per delta instead of twice per tile). */
static unsigned char dbuf[VID_MAXDELTA + 4];

void smsvideo_load_palette(void)
{
    SMS_loadBGPalette(bg_palette);
}

void smsvideo_build_tilemap(void)
{
    unsigned char x, y;
    unsigned int tile = CELL_TILE0;
    for (y = 0; y < 24; y++) {
        SMS_setNextTileatXY(0, y);
        if (y >= WIN_TILE_Y && y < WIN_TILE_Y + VID_TH) {
#if WIN_TILE_X > 0
            for (x = 0; x < WIN_TILE_X; x++) SMS_setTile(BLANK_TILE);
#endif
            for (x = 0; x < VID_TW; x++) SMS_setTile(tile++);
#if (WIN_TILE_X + VID_TW) < 32
            for (x = WIN_TILE_X + VID_TW; x < 32; x++) SMS_setTile(BLANK_TILE);
#endif
        } else {
            for (x = 0; x < 32; x++) SMS_setTile(BLANK_TILE);
        }
    }
}

void smsvideo_apply_delta(unsigned int pos)
{
    const unsigned char *p;
    unsigned int cell = 0;
    unsigned char cur_bank = 0xff;
    unsigned char n;
    unsigned int i, len;

    SMS_mapROMBank(frame_bank[pos]);
    {   /* copy the delta out of the paged window before we touch other banks */
        const unsigned char *src = (const unsigned char *)frame_ofs[pos];
        len = (unsigned int)(frame_ofs[pos + 1] - frame_ofs[pos]);
        if (pos + 1 >= VID_FRAMES || frame_bank[pos + 1] != frame_bank[pos] ||
            len > sizeof dbuf)
            len = sizeof dbuf;
        for (i = 0; i < len; i++) dbuf[i] = src[i];
    }
    p = dbuf;
    for (;;) {
        unsigned char skip = *p++;
        unsigned char count = *p++;
        if (skip == 255 && count == 0) { cell += 254; continue; }
        if (skip == 0 && count == 0) break;
        cell += skip;
        for (n = count; n; n--) {
            unsigned int tid = (unsigned int)p[0] | ((unsigned int)p[1] << 8);
            unsigned char tbank = (unsigned char)(DATA_BANK0 + (tid >> 9));
            p += 2;
            if (tbank != cur_bank) { cur_bank = tbank; SMS_mapROMBank(tbank); }
            SMS_loadTiles((const unsigned char *)(0x8000 + ((tid & 511) << 5)),
                          CELL_TILE0 + cell, 32);
            cell++;
        }
    }
    smsvideo_shown_pos = pos;
}

/* Full repaint of a snapshot position — writes every cell, far more than a
 * VBlank allows, so the display must be OFF. */
void smsvideo_apply_snapshot(unsigned int snap)
{
    unsigned char bank = snap_bank[snap];
    unsigned int off = snap_ofs[snap];
    unsigned int cell;
    for (cell = 0; cell < VID_CELLS; cell++) {
        unsigned int tid;
        unsigned char tbank;
        const unsigned char *p;
        SMS_mapROMBank(bank);
        p = (const unsigned char *)off;
        tid = (unsigned int)p[cell * 2] | ((unsigned int)p[cell * 2 + 1] << 8);
        tbank = (unsigned char)(DATA_BANK0 + (tid >> 9));
        SMS_mapROMBank(tbank);
        SMS_loadTiles((const unsigned char *)(0x8000 + ((tid & 511) << 5)),
                      CELL_TILE0 + cell, 32);
    }
}

void smsvideo_seek(unsigned int pos)
{
    unsigned int snap = pos / SNAPIVL;
    unsigned int p = snap * SNAPIVL;
    smsvideo_apply_snapshot(snap);
    while (p < pos) smsvideo_apply_delta(++p);
    smsvideo_shown_pos = pos;
}
