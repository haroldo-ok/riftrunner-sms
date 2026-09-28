/*
 * RIFT RUNNER — a pre-rendered 3D rail shooter for Sega Master System.
 *
 * The 256x80 chase-camera reels are offline-rendered low-poly scenes streamed
 * from banked ROM with smsvideo. Gameplay (ship, enemies, shots, collisions,
 * score, shields, boss HP) is native Z80-side logic. Normal enemies and all
 * projectiles are prescaled hardware sprites; bosses exist only in the 3D reel.
 */
#include "SMSlib.h"
#include "PSGlib.h"
#include "art_data.h"
#include "smsvideo.h"

#define STATE_TITLE     0
#define STATE_PLAY      1
#define STATE_GAMEOVER  2
#define STATE_VICTORY   3

#define SCENE_RUN       0
#define SCENE_INTRO     1
#define SCENE_BOSS      2
#define SCENE_DEFEAT    3
#define SCENE_END       4

#define LEVEL_COUNT       4
#define VIDEO_LEVEL_STRIDE 162
#define VIDEO_ENTRY_OFFSET 33
#define VIDEO_BOSS_OFFSET  65
#define VIDEO_DEFEAT_OFFSET 130
#define VIDEO_LOOP_END   32
#define VIDEO_BOSS_END   64
#define VIDEO_TRANS_END  31
#define VIDEO_INTERVAL_RUN  3
#define VIDEO_INTERVAL_CINE 2
#define LEVEL_RUN_FRAMES 600
#define BOSS_HP_BASE     20
#define BOSS_HP_STEP     6

#define MAX_PLAYER_SHOTS 10
#define MAX_ENEMY_SHOTS  6
#define MAX_ENEMIES      4
#define MAX_PICKUPS      3
#define MAX_EXPLOSIONS   2

#define WEAPON_PULSE  0
#define WEAPON_SPREAD 1
#define WEAPON_LANCE  2
#define WEAPON_COUNT  3
#define PICKUP_WEAPON 0
#define PICKUP_BOMB   1
#define PICKUP_REPAIR 2
#define PICKUP_COUNT  3
#define BOMB_MAX      5
#define LIVES_MAX     5

#define SFX_SHOT      1
#define SFX_SPREAD    2
#define SFX_LANCE     3
#define SFX_PICKUP    4
#define SFX_BOMB      5
#define SFX_HURT      6
#define SFX_EXPLODE   7
#define SFX_BOSS      8
#define SFX_CLEAR     9

#define HUD_TEXT_FLAGS TILE_USE_SPRITE_PALETTE

/* Globals intentionally stay visible in the .noi file for smstest. */
unsigned char game_state = STATE_TITLE;
unsigned char scene_state = SCENE_RUN;
unsigned char cur_level = 0;
unsigned char lives = 3;
unsigned char boss_hp = 0;
unsigned char boss_hp_max = BOSS_HP_BASE;
unsigned char player_x = 128;
unsigned char player_y = 164;
unsigned int score = 0;
unsigned int level_timer = 0;
unsigned int active_video_frame = 0;
unsigned int game_frame = 0;
unsigned int last_keys = 0;
unsigned char video_phase = 0;
unsigned char invulnerable = 0;
unsigned char boss_dead_pending = 0;
unsigned char enemy_count = 0;
unsigned char weapon_level = WEAPON_PULSE;
unsigned char bombs = 1;
unsigned char pickup_spawned = 0;
unsigned char pickup_collected = 0;
unsigned char enemy_type_mask = 0;
unsigned char sfx_events = 0;
unsigned char last_sfx = 0;

struct Shot {
    signed int x;
    signed int y;
    signed char vx;
    signed char vy;
    unsigned char active;
    unsigned char kind;
    unsigned char damage;
};
struct Enemy {
    signed int x;
    signed int y;
    signed char sway;
    unsigned char type;
    unsigned char active;
    unsigned char fire_timer;
    unsigned char wiggle;
    unsigned char hp;
};
struct Pickup {
    signed int x;
    signed int y;
    unsigned char type;
    unsigned char active;
    unsigned char wiggle;
};
struct Blast {
    signed int x;
    signed int y;
    unsigned char active;
    unsigned char frame;
    unsigned char timer;
};

static struct Shot player_shots[MAX_PLAYER_SHOTS];
static struct Shot enemy_shots[MAX_ENEMY_SHOTS];
static struct Enemy enemies[MAX_ENEMIES];
static struct Pickup pickups[MAX_PICKUPS];
static struct Blast blasts[MAX_EXPLOSIONS];

static unsigned char hud_dirty = 1;
static unsigned char video_pending = 0;
static unsigned char video_clock = 0;
static unsigned int video_request = 0;
static unsigned int random_seed = 0x53a9;
static unsigned char fire_cooldown = 0;
static unsigned char spawn_timer = 45;
static unsigned char drop_cycle = 0;
static unsigned char kill_count = 0;
static unsigned char bomb_button_latched = 0;
static unsigned char restart_pending = 0;
static unsigned char title_blink = 0;
static unsigned char title_blink_timer = 0;
static unsigned char intro_phase = 0;
static unsigned char boss_phase = 0;
static unsigned char defeat_phase = 0;

static const unsigned char blank_tile[32] = {0};

/* PSGlib PSG-stream SFX. Each 0x38 ends a frame; 0x00 ends the effect. */
static const unsigned char sfx_shot[] = {
    0xcf,0x46,0xd0,0x38, 0xd5,0x38, 0xda,0x38, 0xdf,0x00
};
static const unsigned char sfx_spread[] = {
    0xc8,0x48,0xd1,0x38, 0xce,0x44,0xd5,0x38, 0xda,0x38, 0xdf,0x00
};
static const unsigned char sfx_lance[] = {
    0xc5,0x44,0xd0,0x38, 0xca,0x46,0xd3,0x38, 0xd8,0x38, 0xdf,0x00
};
static const unsigned char sfx_pickup[] = {
    0xc0,0x4f,0xd0,0x38, 0xc0,0x4a,0xd1,0x38,
    0xc0,0x47,0xd2,0x38, 0xc0,0x44,0xd4,0x38, 0xda,0x38, 0xdf,0x00
};
static const unsigned char sfx_bomb[] = {
    0xe4,0xf0,0x38, 0xe7,0xf2,0x38, 0xe5,0xf4,0x38,
    0xe7,0xf6,0x38, 0xfa,0x38, 0xff,0x00
};
static const unsigned char sfx_hurt[] = {
    0xc0,0x44,0xd0,0x38, 0xc0,0x49,0xd2,0x38,
    0xc0,0x4e,0xd5,0x38, 0xda,0x38, 0xdf,0x00
};
static const unsigned char sfx_explode[] = {
    0xe7,0xf1,0x38, 0xe5,0xf3,0x38, 0xe7,0xf5,0x38, 0xf9,0x38, 0xff,0x00
};
static const unsigned char sfx_boss[] = {
    0xc0,0x61,0xd2,0x38, 0xc0,0x5a,0xd2,0x38,
    0xc0,0x51,0xd3,0x38, 0xc0,0x48,0xd4,0x38, 0xdc,0x38, 0xdf,0x00
};
static const unsigned char sfx_clear[] = {
    0xc0,0x4d,0xd1,0x38, 0xc0,0x49,0xd1,0x38,
    0xc0,0x45,0xd1,0x38, 0xc0,0x41,0xd0,0x38, 0xd7,0x38, 0xdf,0x00
};

static void play_sfx(unsigned char id) {
    const unsigned char *data = sfx_shot;
    switch (id) {
        case SFX_SPREAD: data = sfx_spread; break;
        case SFX_LANCE: data = sfx_lance; break;
        case SFX_PICKUP: data = sfx_pickup; break;
        case SFX_BOMB: data = sfx_bomb; break;
        case SFX_HURT: data = sfx_hurt; break;
        case SFX_EXPLODE: data = sfx_explode; break;
        case SFX_BOSS: data = sfx_boss; break;
        case SFX_CLEAR: data = sfx_clear; break;
        default: id = SFX_SHOT; data = sfx_shot; break;
    }
    last_sfx = id;
    ++sfx_events;
    PSGSFXPlay((void *)data, SFX_CHANNELS2AND3);
}

static unsigned char random8(void) {
    random_seed = (unsigned int)(random_seed * 109u + 89u);
    return (unsigned char)(random_seed >> 8);
}

static unsigned char dist_u8(signed int a, signed int b) {
    signed int d = a - b;
    if (d < 0) d = -d;
    return (unsigned char)d;
}

static unsigned int sector_base(void) {
    return (unsigned int)cur_level * VIDEO_LEVEL_STRIDE;
}
static unsigned int entry_base(void) {
    return (unsigned int)cur_level * VIDEO_LEVEL_STRIDE + VIDEO_ENTRY_OFFSET;
}
static unsigned int boss_base(void) {
    return (unsigned int)cur_level * VIDEO_LEVEL_STRIDE + VIDEO_BOSS_OFFSET;
}
static unsigned int defeat_base(void) {
    return (unsigned int)cur_level * VIDEO_LEVEL_STRIDE + VIDEO_DEFEAT_OFFSET;
}

static void request_video(unsigned int frame) {
    video_request = frame;
    video_pending = 1;
    active_video_frame = frame;
}

static void reset_actor_pools(void) {
    unsigned char i;
    for (i = 0; i < MAX_PLAYER_SHOTS; ++i) player_shots[i].active = 0;
    for (i = 0; i < MAX_ENEMY_SHOTS; ++i) enemy_shots[i].active = 0;
    for (i = 0; i < MAX_ENEMIES; ++i) enemies[i].active = 0;
    for (i = 0; i < MAX_PICKUPS; ++i) pickups[i].active = 0;
    for (i = 0; i < MAX_EXPLOSIONS; ++i) blasts[i].active = 0;
    enemy_count = 0;
}

static void start_game(void) {
    game_state = STATE_PLAY;
    scene_state = SCENE_RUN;
    cur_level = 0;
    lives = 3;
    boss_hp = 0;
    boss_hp_max = BOSS_HP_BASE;
    player_x = 128;
    player_y = 164;
    score = 0;
    level_timer = 0;
    boss_dead_pending = 0;
    invulnerable = 0;
    weapon_level = WEAPON_PULSE;
    bombs = 1;
    pickup_spawned = 0;
    pickup_collected = 0;
    enemy_type_mask = 0;
    sfx_events = 0;
    last_sfx = 0;
    fire_cooldown = 0;
    bomb_button_latched = 0;
    drop_cycle = 0;
    kill_count = 0;
    spawn_timer = 35;
    reset_actor_pools();
    hud_dirty = 1;
}

static void restart_to_title(void) {
    restart_pending = 1;
}

/* Tile text is a compact 5x7 font stored at BG tiles 64..106. The sprite
 * palette flag makes the same glyphs white/cyan without consuming video tiles. */
static unsigned char font_index(unsigned char c) {
    if (c >= 'A' && c <= 'Z') return (unsigned char)(FONT_TILE_BASE + c - 'A');
    if (c >= '0' && c <= '9') return (unsigned char)(FONT_TILE_BASE + 26 + c - '0');
    switch (c) {
        case ':': return FONT_TILE_BASE + 36;
        case '/': return FONT_TILE_BASE + 37;
        case '-': return FONT_TILE_BASE + 38;
        case '.': return FONT_TILE_BASE + 39;
        case '!': return FONT_TILE_BASE + 40;
        case '+': return FONT_TILE_BASE + 41;
        case '?': return FONT_TILE_BASE + 42;
        default: return BLANK_TILE_ID;
    }
}

static void put_char(unsigned char x, unsigned char y, unsigned char c) {
    unsigned int tile;
    if (x >= 32 || y >= 24) return;
    tile = font_index(c);
    if (tile != BLANK_TILE_ID) tile |= HUD_TEXT_FLAGS;
    SMS_setTileatXY(x, y, tile);
}

static void put_text(unsigned char x, unsigned char y, const char *s) {
    while (*s && x < 32) {
        unsigned char c = (unsigned char)*s++;
        if (c >= 'a' && c <= 'z') c = (unsigned char)(c - 'a' + 'A');
        put_char(x++, y, c);
    }
}

static const char *sector_name(void) {
    switch (cur_level) {
        case 0: return "ION HIGHWAY";
        case 1: return "ASH CATHEDRAL";
        case 2: return "CRYSTAL REACH";
        default: return "VOID THRONE";
    }
}
static const char *boss_name(void) {
    switch (cur_level) {
        case 0: return "DREAD MANTA";
        case 1: return "SOLAR CROWN";
        case 2: return "PRISM WYRM";
        default: return "VOID COLOSSUS";
    }
}
static const char *weapon_name(void) {
    switch (weapon_level) {
        case WEAPON_SPREAD: return "SPREAD";
        case WEAPON_LANCE: return "LANCE";
        default: return "PULSE";
    }
}
static unsigned char boss_health_for_level(void) {
    return (unsigned char)(BOSS_HP_BASE + BOSS_HP_STEP * cur_level);
}

static void restore_star_row(unsigned char row) {
    unsigned char x;
    for (x = 0; x < 32; ++x)
        SMS_setTileatXY(x, row, STAR_TILE_BASE + ((x * 7u + row * 11u) & 15u));
}

static void draw_static_starfield(void) {
    unsigned char x, y;
    for (y = 0; y < 24; ++y) {
        if (y >= 7 && y < 17) continue; /* tile-streamed 3D view */
        for (x = 0; x < 32; ++x)
            SMS_setTileatXY(x, y, STAR_TILE_BASE + ((x * 7u + y * 11u) & 15u));
    }
}

static void draw_title_text(void) {
    put_text(10, 1, "RIFT RUNNER");
    put_text(3, 3, "4-SECTOR FMV RAIL SHOOTER");
    put_text(4, 5, "1/2 START  ARROWS STEER");
    put_text(5, 20, "1 FIRE  2 NOVA BOMB");
    put_text(6, 22, "COLLECT POWER ORBS");
}

static void draw_number6(unsigned char x, unsigned char y, unsigned int value) {
    unsigned char digits[6];
    signed char i;
    for (i = 5; i >= 0; --i) {
        digits[i] = (unsigned char)('0' + (value % 10u));
        value /= 10u;
    }
    for (i = 0; i < 6; ++i) put_char((unsigned char)(x + i), y, digits[i]);
}

static void draw_hud(void) {
    unsigned char filled, i;
    if (game_state == STATE_TITLE) {
        restore_star_row(1); restore_star_row(3); restore_star_row(5); restore_star_row(20); restore_star_row(22);
        draw_title_text();
    } else if (game_state == STATE_PLAY) {
        restore_star_row(1); restore_star_row(3); restore_star_row(5);
        restore_star_row(18); restore_star_row(20); restore_star_row(22);
        put_text(1, 1, "SCORE");
        draw_number6(7, 1, score);
        put_text(21, 1, "SECTOR");
        put_char(28, 1, (unsigned char)('0' + cur_level + 1));
        put_text(1, 3, "LIVES"); put_char(7, 3, (unsigned char)('0' + lives));
        put_text(11, 3, "WPN"); put_text(15, 3, weapon_name());
        put_text(24, 3, "B"); put_char(26, 3, (unsigned char)('0' + bombs));
        if (scene_state == SCENE_INTRO || scene_state == SCENE_BOSS ||
            scene_state == SCENE_DEFEAT) {
            put_text(1, 5, "BOSS");
            filled = (unsigned char)((boss_hp * 24u) / (boss_hp_max ? boss_hp_max : 1));
            for (i = 0; i < 24; ++i) {
                unsigned char tile = (i < filled) ? BAR_FULL_TILE : BAR_EMPTY_TILE;
                if (boss_hp <= (boss_hp_max >> 2) && i < filled) tile = BAR_WARN_TILE;
                SMS_setTileatXY((unsigned char)(6 + i), 5, tile);
            }
        } else if (boss_dead_pending) {
            put_text(1, 5, "TARGET LOCKED");
        } else {
            put_text(1, 5, "CLEAR THE SECTOR");
            for (i = 0; i < 8; ++i) SMS_setTileatXY((unsigned char)(20 + i), 5, BAR_CYAN_TILE);
        }
        put_text(1, 18, "ARROWS STEER"); put_text(17, 18, "1 FIRE 2 NOVA");
        put_text(7, 20, sector_name());
        if (scene_state == SCENE_INTRO || scene_state == SCENE_BOSS || scene_state == SCENE_DEFEAT)
            put_text(10, 22, boss_name());
        else
            put_text(4, 22, "ORBS: WEAPON BOMB REPAIR");
    } else if (game_state == STATE_GAMEOVER) {
        restore_star_row(1); restore_star_row(3); restore_star_row(5); restore_star_row(20); restore_star_row(22);
        put_text(10, 1, "FLIGHT LOST");
        put_text(8, 3, "SCORE"); draw_number6(14, 3, score);
        put_text(5, 5, "PRESS 1 TO REDEPLOY");
        put_text(2, 20, "THE RIFT IS STILL OPEN");
    } else {
        restore_star_row(1); restore_star_row(3); restore_star_row(5); restore_star_row(20); restore_star_row(22);
        put_text(9, 1, "RIFT SEALED");
        put_text(8, 3, "SCORE"); draw_number6(14, 3, score);
        put_text(5, 5, "PRESS 1 TO FLY AGAIN");
        put_text(2, 20, "HERO OF THE OUTER RIM");
    }
    hud_dirty = 0;
}

static void spawn_player_shot(signed int x, signed char vx, signed char vy,
                              unsigned char kind, unsigned char damage) {
    unsigned char i;
    for (i = 0; i < MAX_PLAYER_SHOTS; ++i) {
        if (!player_shots[i].active) {
            player_shots[i].active = 1;
            player_shots[i].x = x;
            player_shots[i].y = (signed int)player_y - 11;
            player_shots[i].vx = vx;
            player_shots[i].vy = vy;
            player_shots[i].kind = kind;
            player_shots[i].damage = damage;
            return;
        }
    }
}

static void fire_weapon(void) {
    if (weapon_level == WEAPON_SPREAD) {
        spawn_player_shot((signed int)player_x - 8,-1,-4,WEAPON_SPREAD,1);
        spawn_player_shot((signed int)player_x,0,-5,WEAPON_SPREAD,1);
        spawn_player_shot((signed int)player_x + 8,1,-4,WEAPON_SPREAD,1);
        fire_cooldown = 11;
        play_sfx(SFX_SPREAD);
    } else if (weapon_level == WEAPON_LANCE) {
        spawn_player_shot((signed int)player_x,0,-7,WEAPON_LANCE,2);
        fire_cooldown = 9;
        play_sfx(SFX_LANCE);
    } else {
        spawn_player_shot((signed int)player_x,0,-5,WEAPON_PULSE,1);
        fire_cooldown = 7;
        play_sfx(SFX_SHOT);
    }
}

static void spawn_enemy_shot(signed int x, signed int y, unsigned char aimed) {
    unsigned char i;
    for (i = 0; i < MAX_ENEMY_SHOTS; ++i) {
        if (!enemy_shots[i].active) {
            enemy_shots[i].active = 1;
            enemy_shots[i].x = x;
            enemy_shots[i].y = y + 5;
            enemy_shots[i].vx = 0;
            enemy_shots[i].vy = aimed ? 3 : 2;
            enemy_shots[i].kind = aimed;
            enemy_shots[i].damage = 1;
            if (aimed) {
                if ((signed int)player_x > x + 8) enemy_shots[i].vx = 1;
                else if ((signed int)player_x < x - 8) enemy_shots[i].vx = -1;
            }
            return;
        }
    }
}

static void spawn_blast(signed int x, signed int y) {
    unsigned char i;
    for (i = 0; i < MAX_EXPLOSIONS; ++i) {
        if (!blasts[i].active) {
            blasts[i].active = 1;
            blasts[i].x = x;
            blasts[i].y = y;
            blasts[i].frame = 0;
            blasts[i].timer = 0;
            return;
        }
    }
}

static void spawn_pickup(signed int x, signed int y, unsigned char type) {
    unsigned char i;
    for (i = 0; i < MAX_PICKUPS; ++i) {
        if (!pickups[i].active) {
            pickups[i].active = 1;
            pickups[i].x = x;
            pickups[i].y = y;
            pickups[i].type = type;
            pickups[i].wiggle = (unsigned char)(random8() & 7u);
            ++pickup_spawned;
            return;
        }
    }
}

static void award_enemy_kill(struct Enemy *e) {
    if (enemy_count) --enemy_count;
    score += (unsigned int)(100u + (unsigned int)e->type * 50u);
    spawn_blast(e->x,e->y);
    play_sfx(SFX_EXPLODE);
    if (++kill_count >= 3) {
        kill_count = 0;
        spawn_pickup(e->x,e->y,(unsigned char)(drop_cycle % PICKUP_COUNT));
        ++drop_cycle;
    }
    hud_dirty = 1;
}

static void collect_pickup(unsigned char type) {
    if (type == PICKUP_WEAPON) {
        if (weapon_level < WEAPON_COUNT - 1) ++weapon_level;
        else score += 500;
    } else if (type == PICKUP_BOMB) {
        if (bombs < BOMB_MAX) ++bombs;
        else score += 250;
    } else if (type == PICKUP_REPAIR) {
        if (lives < LIVES_MAX) ++lives;
        else { invulnerable = 120; score += 250; }
    }
    ++pickup_collected;
    hud_dirty = 1;
    play_sfx(SFX_PICKUP);
}

static void update_pickups(void) {
    unsigned char i;
    for (i = 0; i < MAX_PICKUPS; ++i) {
        struct Pickup *p = &pickups[i];
        if (!p->active) continue;
        ++p->y;
        if (++p->wiggle >= 8) {
            p->wiggle = 0;
            if (p->x < player_x) ++p->x;
            else if (p->x > player_x) --p->x;
        }
        if (p->y > 190) { p->active = 0; continue; }
        if (dist_u8(p->x,player_x) < 10 && dist_u8(p->y,player_y) < 12) {
            p->active = 0;
            collect_pickup(p->type);
            spawn_blast(p->x,p->y);
        }
    }
}

static void damage_player(void) {
    if (invulnerable) return;
    if (lives) --lives;
    invulnerable = 72;
    hud_dirty = 1;
    play_sfx(SFX_HURT);
    if (!lives) {
        game_state = STATE_GAMEOVER;
        scene_state = SCENE_END;
    }
}

static unsigned char choose_enemy_type(unsigned char r) {
    if (cur_level == 0) return (unsigned char)(r & 1u); /* Mite / Viper */
    if (cur_level == 1) return (unsigned char)(1u + (r % 3u)); /* Viper / Lancer / Bulwark */
    if (cur_level == 2) return (unsigned char)((r % 3u) ? (1u + (r % 3u)) : 0u);
    return (unsigned char)(r & 3u);
}

static void spawn_enemy(void) {
    unsigned char i, r, type;
    for (i = 0; i < MAX_ENEMIES; ++i) {
        if (!enemies[i].active) {
            r = random8();
            type = choose_enemy_type(r);
            enemies[i].active = 1;
            enemies[i].type = type;
            enemies[i].x = 28 + (signed int)(r % 200u);
            enemies[i].y = (type == 3) ? 62 : 69;
            enemies[i].sway = (r & 2u) ? 1 : -1;
            enemies[i].fire_timer = (unsigned char)(type == 3 ? 76 + (r & 31u) :
                                                  (type == 2 ? 40 + (r & 31u) : 54 + (r & 31u)));
            enemies[i].wiggle = r;
            enemies[i].hp = (unsigned char)(type == 3 ? 2u : 1u);
            enemy_type_mask |= (unsigned char)(1u << type);
            ++enemy_count;
            return;
        }
    }
}

static void update_enemies(void) {
    unsigned char i, speed, aimed;
    for (i = 0; i < MAX_ENEMIES; ++i) {
        struct Enemy *e = &enemies[i];
        if (!e->active) continue;
        speed = (e->type == 0 || e->type == 2) ? 2 : 1;
        e->y += speed;
        if ((++e->wiggle & (e->type == 2 ? 3u : 7u)) == 0) {
            e->x += (e->type == 2) ? (signed int)(e->sway * 3) : e->sway;
            if (e->x < 14) { e->x = 14; e->sway = 1; }
            if (e->x > 242) { e->x = 242; e->sway = -1; }
        }
        if (e->fire_timer) --e->fire_timer;
        else {
            aimed = (unsigned char)(e->type >= 2);
            spawn_enemy_shot(e->x,e->y,aimed);
            e->fire_timer = (unsigned char)(e->type == 3 ? 78 + (random8() & 31u) :
                                          (e->type == 2 ? 60 + (random8() & 31u) : 65 + (random8() & 31u)));
        }
        if (e->y > 190) {
            e->active = 0;
            if (enemy_count) --enemy_count;
            continue;
        }
        if (!invulnerable && dist_u8(e->x,player_x) < (e->type == 3 ? 16 : 12) &&
            dist_u8(e->y,player_y) < (e->type == 3 ? 17 : 13)) {
            e->active = 0;
            if (enemy_count) --enemy_count;
            spawn_blast(e->x,e->y);
            damage_player();
        }
    }
}

static void update_shots(void) {
    unsigned char i, j;
    for (i = 0; i < MAX_PLAYER_SHOTS; ++i) {
        struct Shot *s = &player_shots[i];
        if (!s->active) continue;
        s->x += s->vx; s->y += s->vy;
        if (s->y < -8 || s->x < -8 || s->x > 263) { s->active = 0; continue; }
        if (scene_state == SCENE_BOSS && boss_hp &&
            dist_u8(s->x,128) < 50 && dist_u8(s->y,97) < 27) {
            if (s->damage >= boss_hp) boss_hp = 0;
            else boss_hp -= s->damage;
            s->active = 0;
            score += 250;
            spawn_blast(128,97);
            play_sfx(SFX_EXPLODE);
            hud_dirty = 1;
            if (!boss_hp) boss_dead_pending = 1;
            continue;
        }
        for (j = 0; j < MAX_ENEMIES; ++j) {
            struct Enemy *e = &enemies[j];
            unsigned char radius;
            if (!e->active) continue;
            radius = (e->y < 90) ? 7 : ((e->y < 121) ? 11 : (e->type == 3 ? 16 : 13));
            if (dist_u8(s->x,e->x) < radius && dist_u8(s->y,e->y) < radius) {
                s->active = 0;
                if (s->damage >= e->hp) {
                    e->active = 0;
                    award_enemy_kill(e);
                } else {
                    e->hp -= s->damage;
                    spawn_blast(e->x,e->y);
                }
                break;
            }
        }
    }
    for (i = 0; i < MAX_ENEMY_SHOTS; ++i) {
        struct Shot *s = &enemy_shots[i];
        if (!s->active) continue;
        s->x += s->vx; s->y += s->vy;
        if (s->y > 192 || s->x < -8 || s->x > 263) { s->active = 0; continue; }
        if (dist_u8(s->x,player_x) < 9 && dist_u8(s->y,player_y) < 10) {
            s->active = 0;
            damage_player();
        }
    }
    for (i = 0; i < MAX_EXPLOSIONS; ++i) {
        struct Blast *b = &blasts[i];
        if (!b->active) continue;
        if (++b->timer >= 4) {
            b->timer = 0;
            if (++b->frame >= 4) b->active = 0;
        }
    }
}

static void deploy_bomb(void) {
    unsigned char i;
    if (!bombs) return;
    --bombs;
    for (i = 0; i < MAX_ENEMY_SHOTS; ++i) enemy_shots[i].active = 0;
    for (i = 0; i < MAX_ENEMIES; ++i) {
        if (enemies[i].active) {
            enemies[i].active = 0;
            award_enemy_kill(&enemies[i]);
        }
    }
    if (scene_state == SCENE_BOSS && boss_hp) {
        if (boss_hp > 3) boss_hp -= 3;
        else boss_hp = 0;
        score += 250;
        if (!boss_hp) boss_dead_pending = 1;
    }
    spawn_blast(player_x,player_y-24);
    spawn_blast(128,97);
    hud_dirty = 1;
    play_sfx(SFX_BOMB);
}

static void move_player(unsigned int keys) {
    if (keys & PORT_A_KEY_LEFT)  { if (player_x > 10) player_x -= 2; }
    if (keys & PORT_A_KEY_RIGHT) { if (player_x < 246) player_x += 2; }
    if (keys & PORT_A_KEY_UP)    { if (player_y > 145) player_y -= 2; }
    if (keys & PORT_A_KEY_DOWN)  { if (player_y < 179) player_y += 2; }
    if (fire_cooldown) --fire_cooldown;
    if ((keys & PORT_A_KEY_1) && !fire_cooldown) fire_weapon();
    if (keys & PORT_A_KEY_2) {
        if (!bomb_button_latched) {
            bomb_button_latched = 1;
            deploy_bomb();
        }
    } else bomb_button_latched = 0;
    if (invulnerable) --invulnerable;
}

static void start_boss_entry(void) {
    scene_state = SCENE_INTRO;
    intro_phase = 0;
    boss_hp_max = boss_health_for_level();
    boss_hp = boss_hp_max;
    boss_dead_pending = 0;
    reset_actor_pools();
    fire_cooldown = 0;
    hud_dirty = 1;
    play_sfx(SFX_BOSS);
    request_video(entry_base()); /* exact loop frame zero dissolves into this boss */
}

static void finish_level(void) {
    ++cur_level;
    scene_state = SCENE_RUN;
    video_phase = 0;
    level_timer = 0;
    boss_hp = 0;
    boss_hp_max = boss_health_for_level();
    boss_dead_pending = 0;
    spawn_timer = (unsigned char)(50u - cur_level * 3u);
    reset_actor_pools();
    /* Guardians cache a weapon core; the third also leaves a nova refill. */
    spawn_pickup(player_x,112,cur_level == 3 ? PICKUP_BOMB : PICKUP_WEAPON);
    hud_dirty = 1;
    /* Each shatter reel ends on the next sector's exact frame zero. */
    active_video_frame = sector_base();
}

static void advance_video(void) {
    unsigned char interval;
    if (game_state != STATE_TITLE && game_state != STATE_PLAY) return;
    interval = (scene_state == SCENE_INTRO || scene_state == SCENE_DEFEAT)
             ? VIDEO_INTERVAL_CINE : VIDEO_INTERVAL_RUN;
    if (++video_clock < interval) return;
    video_clock = 0;

    if (game_state == STATE_TITLE) {
        if (video_phase < VIDEO_LOOP_END) ++video_phase;
        else video_phase = 1; /* frame 32 is a duplicate of frame 0 */
        request_video(sector_base() + video_phase);
        return;
    }

    if (scene_state == SCENE_RUN) {
        if (level_timer >= LEVEL_RUN_FRAMES && video_phase == VIDEO_LOOP_END) {
            start_boss_entry();
            return;
        }
        if (video_phase < VIDEO_LOOP_END) ++video_phase;
        else video_phase = 1;
        request_video(sector_base() + video_phase);
        return;
    }

    if (scene_state == SCENE_INTRO) {
        if (intro_phase < VIDEO_TRANS_END) {
            ++intro_phase;
            request_video(entry_base() + intro_phase);
            if (intro_phase == VIDEO_TRANS_END) {
                scene_state = SCENE_BOSS;
                boss_phase = 0;
                hud_dirty = 1;
            }
        }
        return;
    }

    if (scene_state == SCENE_BOSS) {
        if (boss_dead_pending && boss_phase == VIDEO_BOSS_END) {
            scene_state = SCENE_DEFEAT;
            defeat_phase = 0;
            hud_dirty = 1;
            request_video(defeat_base()); /* same picture as boss reel frame 0 */
            return;
        }
        if (boss_phase < VIDEO_BOSS_END) ++boss_phase;
        else boss_phase = 1; /* last boss frame duplicates its first frame */
        request_video(boss_base() + boss_phase);
        return;
    }

    if (scene_state == SCENE_DEFEAT) {
        if (defeat_phase < VIDEO_TRANS_END) {
            ++defeat_phase;
            request_video(defeat_base() + defeat_phase);
            if (defeat_phase == VIDEO_TRANS_END) {
                if (cur_level + 1u < LEVEL_COUNT) finish_level();
                else {
                    game_state = STATE_VICTORY;
                    scene_state = SCENE_END;
                    hud_dirty = 1;
                    play_sfx(SFX_CLEAR);
                }
            }
        }
    }
}

static void update_world(unsigned int keys) {
    last_keys = keys;
    video_pending = 0;
    ++game_frame;
    if (game_state == STATE_TITLE) {
        if (keys & (PORT_A_KEY_1 | PORT_A_KEY_2)) {
            start_game();
            if (keys & PORT_A_KEY_2) bomb_button_latched = 1;
        }
        if (++title_blink_timer >= 32) { title_blink_timer = 0; title_blink ^= 1; }
    } else if (game_state == STATE_GAMEOVER || game_state == STATE_VICTORY) {
        if (keys & (PORT_A_KEY_1 | PORT_A_KEY_2)) restart_to_title();
    } else if (game_state == STATE_PLAY) {
        move_player(keys);
        if (scene_state == SCENE_RUN) {
            ++level_timer;
            if (spawn_timer) --spawn_timer;
            else {
                unsigned char r = random8();
                spawn_enemy();
                if (cur_level == 0) spawn_timer = (unsigned char)(58 + (r & 39u));
                else if (cur_level == 1) spawn_timer = (unsigned char)(48 + (r & 31u));
                else if (cur_level == 2) spawn_timer = (unsigned char)(42 + (r & 31u));
                else spawn_timer = (unsigned char)(38 + (r & 23u));
            }
            update_enemies();
            update_pickups();
        }
        update_shots();
        if (game_state == STATE_PLAY && scene_state == SCENE_RUN &&
            level_timer >= LEVEL_RUN_FRAMES) boss_dead_pending = 0;
        if (scene_state == SCENE_BOSS && boss_hp == 0 && boss_dead_pending == 0)
            boss_dead_pending = 1;
    }
    advance_video();
}

static unsigned char enemy_size(const struct Enemy *e) {
    if (e->y < 88) return 8;
    if (e->type == 3 && e->y >= 126) return 24;
    return 16;
}

static unsigned char enemy_tile(const struct Enemy *e, unsigned char size) {
    if (e->type == 0) return (size == 8) ? MITE_8_TILE : MITE_16_TILE;
    if (e->type == 1) return (size == 8) ? VIPER_8_TILE : VIPER_16_TILE;
    if (e->type == 2) return (size == 8) ? LANCER_8_TILE : LANCER_16_TILE;
    if (size == 8) return BULWARK_8_TILE;
    if (size == 16) return BULWARK_16_TILE;
    return BULWARK_24_TILE;
}

static unsigned char sprite_line_count[192];
static unsigned char sprite_entry_count;

static void add_single_sprite(signed int x, signed int y, unsigned char tile) {
    unsigned char scan, free_line = 1;
    signed int end;
    if (x < 0 || x >= 248 || y < 0 || y >= 192 || sprite_entry_count >= 64) return;
    end = y + 8;
    if (end > 192) end = 192;
    for (scan = (unsigned char)y; scan < (unsigned char)end; ++scan)
        if (sprite_line_count[scan] >= 8) { free_line = 0; break; }
    if (!free_line) return; /* SMS VDP draws only eight sprites on each scanline. */
    SMS_addSprite((unsigned char)x,(unsigned char)y,tile);
    ++sprite_entry_count;
    for (scan = (unsigned char)y; scan < (unsigned char)end; ++scan)
        ++sprite_line_count[scan];
}

static void add_sprite_grid(signed int x, signed int y, unsigned char base, 

                            unsigned char cols, unsigned char rows) {
    unsigned char cx, cy;
    for (cy = 0; cy < rows; ++cy) {
        for (cx = 0; cx < cols; ++cx) {
            signed int sx = x + ((signed int)cx << 3);
            signed int sy = y + ((signed int)cy << 3);
            add_single_sprite(sx, sy, (unsigned char)(base + cy*cols + cx));
        }
    }
}

static void draw_sprites(void) {
    unsigned char i;
    SMS_initSprites();
    for (i = 0; i < 192; ++i) sprite_line_count[i] = 0;
    sprite_entry_count = 0;
    /* Protect the player ship from the SMS eight-sprites-per-line limit. */
    if (game_state != STATE_GAMEOVER && (invulnerable == 0 || (game_frame & 3u) < 2u))
        add_sprite_grid((signed int)player_x-8,(signed int)player_y-8,PLAYER_TILE,2,2);
    for (i = 0; i < MAX_PLAYER_SHOTS; ++i) {
        if (player_shots[i].active) {
            unsigned char tile = PULSE_SHOT_TILE;
            if (player_shots[i].kind == WEAPON_SPREAD) tile = SPREAD_SHOT_TILE;
            else if (player_shots[i].kind == WEAPON_LANCE) tile = LANCE_SHOT_TILE;
            add_single_sprite(player_shots[i].x-4,player_shots[i].y-4,tile);
        }
    }
    for (i = 0; i < MAX_ENEMY_SHOTS; ++i) {
        if (enemy_shots[i].active) {
            unsigned char tile = enemy_shots[i].kind ? TRACK_SHOT_TILE : ENEMY_SHOT_TILE;
            add_single_sprite(enemy_shots[i].x-4,enemy_shots[i].y-4,tile);
        }
    }
    for (i = 0; i < MAX_ENEMIES; ++i) {
        struct Enemy *e = &enemies[i];
        unsigned char size, cols, base;
        if (!e->active) continue;
        size = enemy_size(e);
        cols = (unsigned char)(size >> 3);
        base = enemy_tile(e,size);
        add_sprite_grid(e->x - (size >> 1),e->y - (size >> 1),base,cols,cols);
    }
    for (i = 0; i < MAX_PICKUPS; ++i) {
        struct Pickup *p = &pickups[i];
        unsigned char tile;
        if (!p->active || (game_frame & 2u)) continue;
        tile = p->type == PICKUP_WEAPON ? PICKUP_WEAPON_TILE :
               (p->type == PICKUP_BOMB ? PICKUP_BOMB_TILE : PICKUP_REPAIR_TILE);
        add_single_sprite(p->x-4,p->y-4,tile);
    }
    for (i = 0; i < MAX_EXPLOSIONS; ++i) {
        struct Blast *b=&blasts[i];
        if (b->active) {
            unsigned char base=(unsigned char)(EXP_0_TILE + b->frame*4u);
            add_sprite_grid(b->x-8,b->y-8,base,2,2);
        }
    }
    SMS_finalizeSprites();
}

static void reset_title_video(void) {
    SMS_displayOff();
    reset_actor_pools();
    game_state = STATE_TITLE;
    scene_state = SCENE_RUN;
    cur_level = 0;
    lives = 3;
    boss_hp = 0;
    boss_hp_max = BOSS_HP_BASE;
    player_x = 128;
    player_y = 164;
    score = 0;
    level_timer = 0;
    video_phase = 0;
    video_clock = 0;
    boss_dead_pending = 0;
    invulnerable = 0;
    weapon_level = WEAPON_PULSE;
    bombs = 1;
    fire_cooldown = 0;
    bomb_button_latched = 0;
    spawn_timer = 35;
    PSGSFXStop();
    smsvideo_seek(0);
    active_video_frame = 0;
    SMS_displayOn();
    hud_dirty = 1;
}

void main(void) {
    unsigned int keys;
    SMS_init();
    SMS_displayOff();
    SMS_useFirstHalfTilesforSprites(1);
    SMS_setSpriteMode(SPRITEMODE_NORMAL);
    SMS_loadSpritePalette(sprite_palette);
    SMS_loadTiles(sprite_tiles, 0, SPRITE_TILE_BYTES);
    SMS_loadTiles(font_tiles, FONT_TILE_BASE, FONT_TILE_BYTES);
    SMS_loadTiles(blank_tile, BLANK_TILE_ID, 32);
    smsvideo_load_palette();
    smsvideo_build_tilemap();
    draw_static_starfield();
    smsvideo_seek(0);
    SMS_setBackdropColor(0);
    draw_title_text();
    SMS_displayOn();
    hud_dirty = 0;

    for (;;) {
        keys = SMS_getKeysStatus(); /* sample exactly once per frame */
        update_world(keys);
        draw_sprites();
        SMS_waitForVBlank();
        SMS_copySpritestoSAT();
        PSGSFXFrame(); /* one SFX tick per submitted game frame */
        if (restart_pending) {
            restart_pending = 0;
            reset_title_video();
        } else if (video_pending) {
            smsvideo_apply_delta(video_request);
            video_pending = 0;
        }
        if (hud_dirty) draw_hud();
    }
}

SMS_EMBED_SEGA_ROM_HEADER(9999,0);
SMS_EMBED_SDSC_HEADER_AUTO_DATE(1,0,"OpenAI","RIFT RUNNER","Pre-rendered 3D rail shooter for Sega Master System");
