/* smsvideo — runtime player for smsvideo.py's tile stream.
 *
 * Pair with the generated video_data.{c,h} and bank_data.bin spliced into the
 * ROM at bank DATA_BANK0 (see mkrom.py). You drive `pos` from YOUR simulation;
 * this just paints it. See references/prerendered-video.md.
 *
 *   normal play (pos advances by small steps, display ON):
 *       for each new position p:  smsvideo_apply_delta(p);
 *   boot / restart / jump (display OFF around it):
 *       SMS_displayOff(); smsvideo_seek(target_pos); SMS_displayOn();
 *
 * build_tilemap() writes the nametable once (window of CELL_TILE0.. cells,
 * rest = BLANK_TILE) and must be called before the first paint. Point sprites
 * at the first half of tile memory (SMS_useFirstHalfTilesforSprites(1)) so
 * they can't collide with the streamed cells.
 */
#ifndef SMSVIDEO_H
#define SMSVIDEO_H
#include "video_data.h"

extern unsigned int smsvideo_shown_pos;   /* position currently in VRAM */

void smsvideo_load_palette(void);          /* loads bg_palette */
void smsvideo_build_tilemap(void);         /* paint the fixed cell->slot map */
void smsvideo_apply_delta(unsigned int pos);      /* one position forward */
void smsvideo_apply_snapshot(unsigned int snap);  /* full repaint (display OFF) */
void smsvideo_seek(unsigned int pos);             /* snapshot + replay (OFF) */

#endif
