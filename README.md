# RIFT RUNNER

**RIFT RUNNER** is an original Sega Master System rail shooter built in C with SDCC and devkitSMS. The camera follows from behind the player's ship: a looping, host-rendered 3D flight reel fills the centre of the screen while the ship, ordinary enemies, explosions, pickups, and every shot are native SMS sprites.

## Play

- **D-pad:** steer the ship
- **Button 1:** hold to fire
- **Button 2:** use a nova bomb (one bomb per press)
- **Button 1 or 2 on the title/end screen:** start or redeploy

Clear the four-sector campaign:

| Sector | Name | Video boss |
| --- | --- | --- |
| 1 | ION HIGHWAY | DREAD MANTA |
| 2 | ASH CATHEDRAL | SOLAR CROWN |
| 3 | CRYSTAL REACH | PRISM WYRM |
| 4 | VOID THRONE | VOID COLOSSUS |

The starting **Pulse** gun upgrades to a three-projectile **Spread** weapon and then a high-damage **Lance**. Weapon, bomb, and repair orbs drop during combat; the campaign also awards a weapon upgrade after the first two bosses and a bomb after the third. Fight Mite, Viper, Lancer, and Bulwark enemies, then line up on each boss core. Enemy movement, hitboxes, health, score, projectiles, and boss damage are simulated on the Z80; the boss itself is rendered into the video reel, not drawn as a gameplay sprite.

The game has PSG sound effects for firing, weapon changes, pickups, bombs, damage, explosions, boss encounters, and sector clears. They play through PSGlib; there is no background music.

## What “FMV” means on an SMS

A Master System cannot decode a modern movie file or render 3D at runtime. Here, **FMV means offline-generated 3D footage**: a small software renderer projects flat-shaded low-poly scenes into 256×80, 16-colour frames. The build quantises each frame to the SMS palette, makes a shared tile dictionary, and encodes banked per-frame tile deltas. Four looping flight backgrounds, four animated boss reels, and their entry/defeat transitions are streamed during VBlank. Gameplay remains separate from the video pixels.

The video occupies the centre band of the 256×192 SMS display (tile rows 7–16). This VRAM layout is deliberate: video tiles are 128–447, prescaled sprite tiles are 0–56, and font/status tiles are 64–127. Normal enemies have offline-authored 8×8, 16×16, and (for Bulwark) 24×24 variants; shots and the player ship are also pre-scaled, so the Z80 does no runtime sprite scaling.

## Build and verify

Requirements:

- SDCC 4.2 or newer (tested with 4.5.0)
- devkitSMS headers and libraries under `~/.devkitsms` (or pass `DEVKIT=...`)
- Python 3.9+ and Pillow to regenerate art/video assets
- the bundled `smstest` tool for automated gameplay and PSG-log checks

Add the devkit tools to `PATH` if needed, then run:

```sh
make                    # builds RiftRunner.sms
make verify             # checks encoded video frames and seek snapshots
make playtest           # plays through all four sectors and validates PSG writes
```

`make playtest` saves screenshots under `shots/` and the sound-chip write trace at `shots/audio-test.log`. The trace check requires tone, noise, and non-muted volume writes, so an empty or mute-only PSG log cannot pass as working SFX playback. You can run that check separately with `python3 tools/check_audio_log.py shots/audio-test.log`.

The playable ROM is `RiftRunner.sms`. To regenerate the 3D reels and sprite art, run `python3 tools/render_assets.py`, then rebuild. The renderer writes `assets/frames.bin`, `assets/palette.bin`, the prescaled art, `assets/fmv-storyboard.png`, and `assets/sprite-sheet.png`; `tools/smsvideo.py` encodes the video stream and generates its runtime index tables.

## Technical notes

- Display: 256×192, Mode 4; the video window is 256×80.
- Reels: 648 indexed frames total (four 162-frame sector/boss sequences); 2.17% mean and 9.11% worst-frame reconstructed pixel error in the current verification run.
- Video encoding: 22,214 unique 8×8 tile patterns (694.2 KB of raw tile data); mean 15 and maximum 18 tile updates per frame, with 81 seek snapshots. All 81 seek checkpoints verified with no mismatches. The encoded stream occupies 49 data banks (784 KB packed).
- ROM: 1 MiB, 64 banks total; banked Sega mapper, video data starts at ROM bank 2. The fixed code image uses 17,881 of 32,768 bytes in the current build.
- Boss HP targets: 20, 26, 32, and 38 by sector.
- Collision uses RAM-side actors and boss hitboxes; rendered pixels are never consulted for gameplay.
- The tile-delta format preserves sequential updates on snapshot frames as well as snapshots for seeking, so streaming never skips a keyframe.

## Project layout

- `src/main.c` — player/enemy/projectile simulation, HUD, sprites, campaign state machine, PSG SFX
- `src/smsvideo.c/.h` — SMS-side banked tile-delta player
- `tools/render_assets.py` — offline 3D-style reels and prescaled sprite renderer
- `tools/smsvideo.py` — tile dictionary, delta/snapshot encoder, and verifier
- `tools/check_audio_log.py` — asserts PSG tone/noise and active-volume writes from a playtest
- `tools/mkrom.py` — splices video banks into the ROM and fixes the header
- `tests/playtest.txt` — headless end-to-end campaign assertions
- `assets/fmv-storyboard.png` — selected frames from the four sector and boss reels
- `assets/sprite-sheet.png` — prescaled ship, weapons, enemies, pickups, and explosions

## Credits

SMS support and the pre-rendered tile-video player are based on the techniques in [sega-master-system-skill-for-claude](https://github.com/haroldo-ok/sega-master-system-skill-for-claude) and its `assets/smsvideo` toolkit. The 3D scenes, game code, sprites, and names in this project are original generated content.
