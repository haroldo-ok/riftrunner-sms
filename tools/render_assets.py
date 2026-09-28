#!/usr/bin/env python3
"""Render RIFT RUNNER's 3D-style FMV loops and prescaled SMS sprites.

The console never renders 3D. This host-side software renderer rasterizes a
small flat-shaded polygon world into SMS palette frames; smsvideo.py later
encodes those frames as banked tile deltas. Enemy sizes are also baked here as
8x8, 16x16, and 24x24 sprite art so the Z80 only selects a pre-scaled frame.
Requires Pillow (python3 -m pip install Pillow).
"""
from __future__ import annotations
import json, math, os, random, struct
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
GEN = ROOT / "gen"
W, H = 256, 80

# SMS colour channels are 2-bit. The order is shared by both level reels.
BG_RGB = [
    (0, 0, 0), (0, 0, 1), (0, 0, 2), (0, 1, 3),
    (1, 0, 2), (2, 0, 3), (3, 0, 1), (3, 1, 0),
    (3, 2, 0), (3, 3, 0), (0, 2, 3), (1, 3, 3),
    (2, 2, 3), (3, 1, 3), (2, 2, 2), (3, 3, 3),
]
SPR_RGB = [
    (0, 0, 0), (0, 3, 3), (0, 1, 3), (3, 3, 3),
    (3, 1, 0), (3, 0, 0), (3, 0, 3), (3, 3, 0),
    (1, 0, 3), (2, 3, 3), (3, 2, 0), (0, 0, 2),
    (2, 2, 2), (0, 3, 0), (3, 3, 3), (3, 1, 1),
]

def sms_color(rgb):
    r, g, b = rgb
    return (r & 3) | ((g & 3) << 2) | ((b & 3) << 4)

def color_table(rgb):
    vals = []
    for r, g, b in rgb:
        vals.extend((r * 85, g * 85, b * 85))
    vals.extend([0] * (768 - len(vals)))
    im = Image.new("P", (16, 1))
    im.putpalette(vals)
    return im

PAL_TABLE = color_table(BG_RGB)
PAL_RGB = [tuple(c * 85 for c in rgb) for rgb in BG_RGB]

# Fixed point-free host rendering only; no floats or trig are used in the SMS ROM.
HORIZON = 18.0
CAMERA_Y = 1.4
FOCAL = 50.0

# 3D low-poly background geometry: small cubes / shards used by both sectors.
CUBE_V = [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
          (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]
CUBE_F = [(0,1,2),(0,2,3),(4,6,5),(4,7,6),
          (0,4,5),(0,5,1),(3,2,6),(3,6,7),
          (1,5,6),(1,6,2),(0,3,7),(0,7,4)]

def img_p():
    im = Image.new("P", (W, H), 0)
    im.putpalette(PAL_TABLE.getpalette())
    return im

def proj(x, y, z, focal=FOCAL, horizon=HORIZON, camera_y=CAMERA_Y):
    if z < 0.35:
        return None
    return (W * 0.5 + x * focal / z,
            horizon + (camera_y - y) * focal / z)

def rotate3(v, ay=0.0, ax=0.0, az=0.0):
    x, y, z = v
    c, s = math.cos(ay), math.sin(ay)
    x, z = x*c + z*s, -x*s + z*c
    c, s = math.cos(ax), math.sin(ax)
    y, z = y*c - z*s, y*s + z*c
    c, s = math.cos(az), math.sin(az)
    x, y = x*c - y*s, x*s + y*c
    return x, y, z

def draw_mesh(draw, vertices, faces, angle=0.0, pos=(0,0,5), scale=1.0,
              palette=(2,3,4,5,6), outline=1, focal=FOCAL,
              horizon=HORIZON, camera_y=CAMERA_Y, ax=0.0, az=0.0):
    """Flat-shaded triangle rasterizer. A face is filled, then edged in ink."""
    wx, wy, wz = pos
    transformed = []
    for v in vertices:
        rx, ry, rz = rotate3(v, angle, ax, az)
        transformed.append((wx + rx*scale, wy + ry*scale, wz + rz*scale))
    light = (-0.45, 0.7, -0.55)
    ll = math.sqrt(sum(q*q for q in light))
    light = tuple(q/ll for q in light)
    polys = []
    for face in faces:
        pts3 = [transformed[k] for k in face]
        p2 = [proj(*p, focal=focal, horizon=horizon, camera_y=camera_y) for p in pts3]
        if any(p is None for p in p2):
            continue
        zavg = sum(p[2] for p in pts3) / len(pts3)
        a, b, c = pts3[:3]
        u = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
        v = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
        n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        nl = math.sqrt(n[0]*n[0]+n[1]*n[1]+n[2]*n[2]) or 1.0
        nd = abs((n[0]*light[0]+n[1]*light[1]+n[2]*light[2])/nl)
        shade = min(len(palette)-1, int(nd * len(palette)))
        polys.append((zavg, [tuple(map(round, p)) for p in p2], palette[shade]))
    # Far faces first, near faces last.
    for _, points, col in sorted(polys, key=lambda item: -item[0]):
        draw.polygon(points, fill=col)
        if outline is not None:
            draw.line(points + [points[0]], fill=outline, width=1, joint="curve")

def draw_gate(draw, z, angle, level):
    # A luminous, low-poly octagonal hoop seen from a chase camera.
    color = (10, 8, 11, 13)[level % 4]
    edge = (11, 9, 15, 12)[level % 4]
    pts = []
    inner = []
    for i in range(8):
        a = 2*math.pi*i/8 + angle
        x = math.cos(a) * 2.15
        y = 1.08 + math.sin(a) * 1.08
        p = proj(x, y, z)
        q = proj(x*0.72, 1.08 + (y-1.08)*0.72, z-0.05)
        pts.append(tuple(round(v) for v in p))
        inner.append(tuple(round(v) for v in q))
    draw.line(pts + [pts[0]], fill=color, width=2, joint="curve")
    draw.line(inner + [inner[0]], fill=edge, width=1, joint="curve")
    for i in range(0,8,2):
        draw.line([pts[i], inner[i]], fill=edge, width=1)

def make_stars(level):
    rng = random.Random(900 + level)
    stars = []
    palettes = ((1,2,3,10,11),(1,2,7,8,9),(1,3,10,11,12),(1,4,5,12,13,15))
    for i in range(58):
        x = rng.randrange(4, 252)
        y = rng.randrange(2, 31)
        depth = rng.choice((1,1,2,3))
        color = rng.choice(palettes[level % 4])
        stars.append((x, y, depth, color, rng.random()*math.tau))
    return stars

STARS = [make_stars(i) for i in range(4)]

def draw_sector_backdrop(draw, level, phase):
    bands = (
        [(0,5,1),(6,12,2),(13,20,4),(21,25,5),(26,30,2)],
        [(0,5,1),(6,12,2),(13,19,5),(20,24,7),(25,30,2)],
        [(0,5,1),(6,12,3),(13,20,2),(21,25,10),(26,30,3)],
        [(0,5,1),(6,12,4),(13,20,5),(21,25,13),(26,30,4)],
    )[level % 4]
    for y0,y1,col in bands:
        draw.rectangle((0,y0,W-1,y1), fill=col)
    if level == 0:
        draw.ellipse((182,-9,233,31), fill=4, outline=5)
        draw.arc((178,-11,238,35),170,342,fill=10,width=1)
        draw.polygon([(0,24),(23,18),(45,23),(67,17),(88,24),(0,28)],fill=2)
        draw.polygon([(256,26),(236,20),(218,24),(199,19),(177,25),(256,29)],fill=4)
    elif level == 1:
        draw.ellipse((29,-23,73,23), fill=5, outline=7)
        draw.arc((26,-25,77,25),5,180,fill=9,width=1)
        draw.polygon([(0,25),(18,19),(35,25),(50,20),(72,25),(0,29)],fill=4)
        draw.polygon([(256,25),(238,19),(221,25),(203,20),(182,25),(256,29)],fill=5)
    elif level == 2:
        draw.ellipse((166,-13,221,31), fill=3, outline=11)
        draw.arc((162,-15,225,34),170,345,fill=15,width=1)
        draw.polygon([(0,24),(18,20),(36,25),(55,18),(73,24),(0,29)],fill=2)
        draw.polygon([(256,27),(236,19),(215,25),(198,19),(178,25),(256,30)],fill=3)
        for x in (54,61,68,75): draw.line((x,22,x+5,17),fill=10,width=1)
    else:
        draw.ellipse((25,-19,83,31), fill=4, outline=13)
        draw.arc((20,-22,88,34),5,184,fill=15,width=1)
        draw.polygon([(0,25),(20,19),(40,25),(58,18),(78,25),(0,30)],fill=1)
        draw.polygon([(256,27),(238,18),(218,25),(199,18),(178,25),(256,30)],fill=4)
        draw.line((48,25,74,17),fill=13,width=1)
    for i,(sx,sy,depth,col,seed) in enumerate(STARS[level % 4]):
        drift=math.sin(phase+seed)*(0.85 if i<6 else 0.0)
        lift=math.cos(phase*2+seed)*(0.5 if i<6 else 0.0)
        x=int(round((sx+drift+256)%256)); y=int(round(sy+lift))
        if i%13==0: draw.rectangle((x,y,x+1,y),fill=col)
        else: draw.point((x,y),fill=col)


def render_background(level, phase):
    im = img_p()
    d = ImageDraw.Draw(im)
    draw_sector_backdrop(d, level, phase)
    horizon = 26
    level %= 4
    floor_col = (2,4,2,1)[level]
    deep = (1,2,1,4)[level]
    horizon_glow = (10,8,11,13)[level]
    # Wide dark runway, with a hard-edged perspective grid.
    d.polygon([(0,horizon),(256,horizon),(256,79),(0,79)], fill=floor_col)
    road_color = (3,7,10,13)[level]
    road_edge = (10,8,11,13)[level]
    d.polygon([(126,horizon),(130,horizon),(220,79),(36,79)], fill=deep)
    d.line([(126,horizon),(36,79)], fill=road_edge, width=1)
    d.line([(130,horizon),(220,79)], fill=road_edge, width=1)
    # Static vanishing lines provide depth; the moving 3D hoops carry the FMV.
    for lane in (-0.65, 0.0, 0.65):
        p = proj(lane*3, 0.0, 9.0)
        q = proj(lane*1.6, 0.0, 1.45)
        d.line([tuple(map(round,p)), tuple(map(round,q))], fill=road_color, width=1)
    # Perspective crossbars on the road, at uneven spacing as in a 3D projection.
    for j, y in enumerate((31,34,38,43,50,59,70,78)):
        half = int(3 + (y-horizon)*2.7)
        c = 3 if (j % 2 == 0) else road_color
        d.line((max(0,128-half),y,min(255,128+half),y), fill=c, width=1)
    # Side rails and small fixed pylons.
    d.line((126,horizon,6,79), fill=road_edge, width=1)
    d.line((130,horizon,250,79), fill=road_edge, width=1)
    for side in (-1,1):
        for z in (2.0, 4.0, 7.0):
            x = side * (2.5 if z < 3 else 2.2)
            p0 = proj(x,0.0,z)
            p1 = proj(x,1.1,z)
            if p0 and p1:
                col = (10, 9, 11, 13)[level % 4]
                d.line([tuple(map(round,p0)),tuple(map(round,p1))],fill=col,width=1)
    # Three gates move down the flight corridor and wrap in depth. The camera
    # phase is cyclic, so the final encoded frame is exactly the first again.
    for idx in range(3):
        z = 1.25 + ((1.4 + idx*2.55 - (phase / math.tau) * 7.8) % 7.8)
        draw_gate(d, z, phase + idx*0.7, level)
    # Low-poly debris tumbling beside the runway.
    for idx in range(4):
        turn = phase + idx*1.37
        z = 1.3 + ((idx*2.3 - (phase/math.tau)*8.0) % 8.0)
        wx = (-1 if idx%2==0 else 1) * (2.2 + 0.45*math.sin(turn))
        wy = 0.8 + 0.55*math.sin(turn*0.7)
        verts = [(x*0.24,y*0.21,z0*0.22) for x,y,z0 in CUBE_V]
        # Shift the tiny cube to the world position by translating the projected
        # points directly through an equivalent temporary vertex list.
        shifted = [(x+wx,y+wy,z+zz) for x,y,zz in verts]
        debris_palettes=((2,4,10,11),(2,5,8,9),(2,3,10,11),(2,4,12,13))
        debris_edges=(3,7,10,13)
        draw_mesh(d, shifted, CUBE_F, angle=turn, pos=(0,0,0), scale=1,
                  palette=debris_palettes[level],outline=debris_edges[level],focal=FOCAL)
    # Horizon line and a few bright lane beacons.
    d.line((0,horizon,255,horizon), fill=horizon_glow, width=1)
    for x in (83,128,173):
        d.rectangle((x,horizon-1,x+1,horizon),fill=(11,9,15,13)[level])
    return im

# Boss meshes. The bodies are manually modelled as triangulated low-poly forms.
MANTA_V = [
    (0,0,-1.35), (0,0.56,-0.68), (-0.82,0.36,-0.12), (0.82,0.36,-0.12),
    (-2.65,0.08,0.36), (-2.12,-0.22,0.78), (-0.68,-0.52,0.74),
    (0.68,-0.52,0.74), (2.12,-0.22,0.78), (2.65,0.08,0.36),
    (0,0.28,1.18), (0,-0.28,1.18), (-0.38,0.12,0.05),(0.38,0.12,0.05),
    (0,0,-0.2), (-1.92,0.0,0.25), (1.92,0.0,0.25),
    (-0.5,-0.45,1.32),(0.5,-0.45,1.32)
]
MANTA_F = [
    (0,1,2),(0,3,1),(1,2,4),(1,4,5),(1,5,6),(1,6,14),
    (1,14,7),(1,7,8),(1,8,9),(1,9,3),(2,5,4),(2,6,5),
    (3,9,8),(3,8,7),(6,7,14),(6,14,2),(7,14,3),
    (6,11,17),(6,17,5),(7,8,18),(7,18,11),(6,17,11),(7,11,18),
    (10,1,0),(10,0,3),(10,3,9),(10,9,8),(10,8,7),(10,7,6),(10,6,5),(10,5,4),
    (14,12,13),(14,13,3),(14,2,12),(12,2,6),(13,7,3)
]

def sphere_mesh(lat=6, lon=10):
    verts=[]
    for j in range(lat+1):
        phi=-math.pi/2 + math.pi*j/lat
        for i in range(lon):
            th=2*math.pi*i/lon
            verts.append((math.cos(phi)*math.cos(th), math.sin(phi), math.cos(phi)*math.sin(th)))
    faces=[]
    for j in range(lat):
        for i in range(lon):
            a=j*lon+i; b=j*lon+(i+1)%lon; c=(j+1)*lon+(i+1)%lon; e=(j+1)*lon+i
            if j != 0: faces.append((a,b,e))
            if j != lat-1: faces.append((b,c,e))
    return verts,faces

CORE_V, CORE_F = sphere_mesh(5,10)

# A faceted shard-winged wyvern for sector three.
PRISM_V = [
    (0,1.55,-0.20),(0.65,0.55,-0.90),(0.65,-0.55,-0.90),
    (-0.65,-0.55,-0.90),(-0.65,0.55,-0.90),(0,0.0,0.95),
    (-2.35,0.12,0.12),(-1.30,0.62,0.42),(-1.12,-0.45,0.40),
    (2.35,0.12,0.12),(1.30,0.62,0.42),(1.12,-0.45,0.40),
    (0,-0.06,-1.18),(0,-0.42,1.38),
]
PRISM_F = [
    (0,1,12),(0,12,4),(1,2,12),(2,3,12),(3,4,12),
    (1,5,2),(2,5,3),(3,5,4),(4,5,1),
    (4,7,6),(4,6,12),(7,5,6),(5,8,6),(4,5,8),
    (1,12,9),(1,9,10),(1,10,5),(5,10,9),(5,9,11),(5,11,2),
    (2,11,9),(3,5,11),(0,5,10),(0,10,1),(0,4,7),(0,7,5),
    (2,5,13),(3,13,5),(4,5,13),(1,5,13),
]

def ring_points(center, rx, ry, z, angle, focal=FOCAL, horizon=HORIZON):
    out=[]
    for i in range(25):
        t=2*math.pi*i/24
        x=center[0] + rx*math.cos(t)*math.cos(angle) - ry*math.sin(t)*math.sin(angle)
        y=center[1] + rx*math.cos(t)*math.sin(angle) + ry*math.sin(t)*math.cos(angle)
        p=proj(x,y,z,focal=focal,horizon=horizon)
        out.append(tuple(map(round,p)))
    return out

def draw_boss_background(draw, level, phase):
    # A different orbital aperture and palette frames each 3D boss.
    level %= 4
    bands = ((1,2,1),(1,2,1),(1,3,2),(1,4,1))[level]
    for y in range(H):
        col = bands[0] if y < 30 else (bands[1] if y < 56 else bands[2])
        draw.line((0,y,W-1,y), fill=col)
    for i,(x,y,depth,col,seed) in enumerate(STARS[level]):
        drift=math.sin(phase+seed)*(0.8 if i < 5 else 0.0)
        px=int(round((x+drift+256)%256)); py=y+int(round(math.cos(phase+seed)*(0.45 if i < 5 else 0.0)))
        draw.point((px,py),fill=col)
    cx,cy=128,37
    ringc=(5,7,11,13)[level]; ringd=(10,9,15,12)[level]
    halo=ring_points((0.0,0.25),3.25,2.15,5.8,phase*0.25,focal=58,horizon=HORIZON)
    draw.line(halo,fill=ringc,width=2,joint="curve")
    inner=ring_points((0.0,0.25),2.65,1.7,5.7,-phase*0.2,focal=58,horizon=HORIZON)
    draw.line(inner,fill=ringd,width=1,joint="curve")
    for i in range(4):
        a=phase*0.5+i*math.pi/2
        x=round(cx+math.cos(a)*61); y=round(cy+math.sin(a)*25)
        draw.ellipse((x-2,y-2,x+2,y+2),fill=(11,9,15,13)[level],outline=ringc)

def draw_boss(level, phase):
    im=img_p(); d=ImageDraw.Draw(im)
    level %= 4
    draw_boss_background(d,level,phase)
    angle=phase
    if level==0:
        draw_mesh(d,MANTA_V,MANTA_F,angle=angle,ax=math.sin(phase*2)*0.10,
                  pos=(0.0,0.18,4.15),scale=1.0,
                  palette=(2,4,5,10,11),outline=1,focal=58,horizon=HORIZON)
        # Twin engine pods and an emissive heart in front of the hull.
        for side in (-1,1):
            p0=proj(side*1.72,-0.10,3.8,focal=58)
            p1=proj(side*1.72,0.18,3.8,focal=58)
            if p0 and p1: d.line([tuple(map(round,p0)),tuple(map(round,p1))],fill=11,width=3)
        core=proj(0,0.15,2.72,focal=58)
        if core:
            x,y=map(round,core)
            d.ellipse((x-4,y-3,x+4,y+3),fill=6,outline=15)
            d.rectangle((x-1,y-2,x+1,y+2),fill=15)
    elif level==1:
        # Rotating solar core with an independent tilted corona ring.
        draw_mesh(d,CORE_V,CORE_F,angle=angle,ax=0.16*math.sin(phase*2),
                  pos=(0.0,0.25,4.15),scale=1.25,
                  palette=(2,5,7,8,9,11),outline=1,focal=58,horizon=HORIZON)
        orbit=ring_points((0.0,0.25),2.15,0.62,4.15,phase*0.62,focal=58)
        d.line(orbit,fill=8,width=2,joint="curve")
        orbit2=ring_points((0.0,0.25),1.8,0.50,4.2,-phase*0.73,focal=58)
        d.line(orbit2,fill=9,width=1,joint="curve")
        core=proj(0,0.25,3.0,focal=58)
        if core:
            x,y=map(round,core)
            d.ellipse((x-5,y-5,x+5,y+5),fill=8,outline=15)
            d.ellipse((x-2,y-2,x+2,y+2),fill=15)
    elif level==2:
        # PRISM WYRM: faceted wing plates and orbiting crystal fragments.
        draw_mesh(d,PRISM_V,PRISM_F,angle=phase*0.55,ax=0.12*math.sin(phase*2),
                  az=0.05*math.cos(phase),pos=(0.0,0.18,4.0),scale=0.92,
                  palette=(2,3,10,11,15),outline=1,focal=58,horizon=HORIZON)
        for k in range(3):
            a=phase*0.7+k*math.tau/3
            draw_mesh(d,CORE_V,CORE_F,angle=-phase+k,ax=phase*0.3,
                      pos=(math.cos(a)*1.9,0.15+math.sin(a)*0.65,3.6+k*0.18),
                      scale=0.28,palette=(2,3,10,11,15),outline=1,focal=58,horizon=HORIZON)
        core=proj(0,0.15,2.95,focal=58)
        if core:
            x,y=map(round,core)
            d.polygon([(x,y-6),(x+5,y),(x,y+6),(x-5,y)],fill=11,outline=15)
    else:
        # VOID COLOSSUS: a rotating armored cube caught inside three rift rings.
        draw_mesh(d,CUBE_V,CUBE_F,angle=phase*0.42,ax=0.45+0.12*math.sin(phase),
                  az=phase*0.18,pos=(0.0,0.22,4.0),scale=1.32,
                  palette=(2,4,5,13,15),outline=1,focal=58,horizon=HORIZON)
        for k in range(3):
            a=phase*0.32+k*math.tau/3
            draw_mesh(d,CUBE_V,CUBE_F,angle=-phase*0.8+k,ax=0.5,
                      pos=(math.cos(a)*2.0,0.18+math.sin(a)*0.75,4.2),
                      scale=0.32,palette=(4,5,12,13,15),outline=1,focal=58,horizon=HORIZON)
        orbit=ring_points((0.0,0.22),3.0,1.0,4.4,phase*0.31,focal=58)
        d.line(orbit,fill=13,width=2,joint="curve")
        core=proj(0,0.22,2.7,focal=58)
        if core:
            x,y=map(round,core)
            d.ellipse((x-5,y-5,x+5,y+5),fill=5,outline=15)
            d.ellipse((x-2,y-2,x+2,y+2),fill=13)
    # A targeting reticle is part of the host-rendered boss footage.
    col=(10,9,11,13)[level]
    d.arc((92,10,164,70),210,330,fill=col,width=1)
    d.arc((96,13,160,67),30,150,fill=col,width=1)
    return im

def blend_to(a, b, t):
    if t <= 0: return a.copy()
    if t >= 1: return b.copy()
    rgb=Image.blend(a.convert("RGB"),b.convert("RGB"),t)
    return rgb.quantize(palette=PAL_TABLE,dither=Image.Dither.NONE)

def mosaic_transition(source, target, count=32):
    """A streamed 8x8-cell dissolve whose every step fits the VBlank tile cap.

    The innermost cells switch first, like a portal opening or a boss shattering
    into the next sector. At most 11 of the 320 cells change in any one frame;
    the ROM's 18-tile upload budget can therefore reproduce this sequence
    exactly instead of trailing an all-screen crossfade.
    """
    source=source.convert("P"); target=target.convert("P")
    sp=source.load(); tp=target.load()
    cells=[]
    for ty in range(H//8):
        for tx in range(W//8):
            cx=tx+0.5-W/16; cy=(ty+0.5-H/16)*1.15
            jitter=((tx*37+ty*19)%17)/100.0
            cells.append((cx*cx+cy*cy+jitter,ty*(W//8)+tx,tx,ty))
    cells.sort()
    order=[c[1:] for c in cells]
    out=[source.copy()]
    total=len(order)
    for step in range(1,count):
        im=source.copy(); pix=im.load()
        end=(step*total)//(count-1)
        for _,tx,ty in order[:end]:
            x0=tx*8; y0=ty*8
            for yy in range(8):
                for xx in range(8): pix[x0+xx,y0+yy]=tp[x0+xx,y0+yy]
        out.append(im)
    return out

def render_entry(source, target, count=32):
    return mosaic_transition(source,target,count)

def render_boss_defeat(level, next_scene=None, count=32):
    first=draw_boss(level,0.0)
    if next_scene is None:
        next_scene=victory_scene()
    # The boss is a prerendered 3D sequence; this cell-wise outward burst is
    # the SMS-friendly finish that releases the runway for the next sector.
    return mosaic_transition(first,next_scene,count)

def victory_scene():
    im=img_p();d=ImageDraw.Draw(im)
    for y in range(H):
        d.line((0,y,255,y),fill=(1 if y<42 else 2))
    for i,(x,y,dep,col,seed) in enumerate(STARS[3]):
        d.point((x,y),fill=(11 if i%7==0 else col))
    d.ellipse((82,13,174,67),fill=4,outline=9,width=2)
    d.ellipse((93,19,163,61),fill=2,outline=11,width=2)
    d.ellipse((112,25,144,55),fill=15,outline=9)
    for k in range(16):
        a=math.tau*k/16
        x=128+int(math.cos(a)*56); y=40+int(math.sin(a)*29)
        d.line((128,40,x,y),fill=(10 if k%2 else 9),width=1)
    return im

# 5x7 font used by the HUD/title. Its glyphs live outside the video tile range.
FONT = {
'A':("01110","10001","10001","11111","10001","10001","10001"),
'B':("11110","10001","10001","11110","10001","10001","11110"),
'C':("01111","10000","10000","10000","10000","10000","01111"),
'D':("11110","10001","10001","10001","10001","10001","11110"),
'E':("11111","10000","10000","11110","10000","10000","11111"),
'F':("11111","10000","10000","11110","10000","10000","10000"),
'G':("01111","10000","10000","10111","10001","10001","01111"),
'H':("10001","10001","10001","11111","10001","10001","10001"),
'I':("11111","00100","00100","00100","00100","00100","11111"),
'J':("00111","00010","00010","00010","10010","10010","01100"),
'K':("10001","10010","10100","11000","10100","10010","10001"),
'L':("10000","10000","10000","10000","10000","10000","11111"),
'M':("10001","11011","10101","10101","10001","10001","10001"),
'N':("10001","11001","10101","10011","10001","10001","10001"),
'O':("01110","10001","10001","10001","10001","10001","01110"),
'P':("11110","10001","10001","11110","10000","10000","10000"),
'Q':("01110","10001","10001","10001","10101","10010","01101"),
'R':("11110","10001","10001","11110","10100","10010","10001"),
'S':("01111","10000","10000","01110","00001","00001","11110"),
'T':("11111","00100","00100","00100","00100","00100","00100"),
'U':("10001","10001","10001","10001","10001","10001","01110"),
'V':("10001","10001","10001","10001","10001","01010","00100"),
'W':("10001","10001","10001","10101","10101","10101","01010"),
'X':("10001","10001","01010","00100","01010","10001","10001"),
'Y':("10001","10001","01010","00100","00100","00100","00100"),
'Z':("11111","00001","00010","00100","01000","10000","11111"),
'0':("01110","10001","10011","10101","11001","10001","01110"),
'1':("00100","01100","00100","00100","00100","00100","01110"),
'2':("01110","10001","00001","00010","00100","01000","11111"),
'3':("11110","00001","00001","01110","00001","00001","11110"),
'4':("00010","00110","01010","10010","11111","00010","00010"),
'5':("11111","10000","10000","11110","00001","00001","11110"),
'6':("01110","10000","10000","11110","10001","10001","01110"),
'7':("11111","00001","00010","00100","01000","01000","01000"),
'8':("01110","10001","10001","01110","10001","10001","01110"),
'9':("01110","10001","10001","01111","00001","00001","01110"),
':':("00000","00100","00100","00000","00100","00100","00000"),
'/':("00001","00010","00010","00100","01000","01000","10000"),
'-':("00000","00000","00000","11111","00000","00000","00000"),
'.':("00000","00000","00000","00000","00000","00110","00110"),
'!':("00100","00100","00100","00100","00100","00000","00100"),
'+':("00000","00100","00100","11111","00100","00100","00000"),
'?':("01110","10001","00001","00010","00100","00000","00100"),
}
CHAR_ORDER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:/-.!+?"
FONT_BASE=64
BAR_FULL=107
BAR_EMPTY=108
BAR_WARN=109
BAR_CYAN=110
STAR_BASE=111
BLANK_TILE=127

# Sprite pattern map: each generated pre-scaled silhouette is tiled row-major.
SPRITE_BASES = {
    "PLAYER":0, "PULSE_SHOT":4, "SPREAD_SHOT":5, "LANCE_SHOT":6,
    "ENEMY_SHOT":7, "TRACK_SHOT":8,
    "PICKUP_WEAPON":9, "PICKUP_BOMB":10, "PICKUP_REPAIR":11,
    "MITE_8":12, "MITE_16":13,
    "VIPER_8":17, "VIPER_16":18,
    "LANCER_8":22, "LANCER_16":23,
    "BULWARK_8":27, "BULWARK_16":28, "BULWARK_24":32,
    "EXP_0":41, "EXP_1":45, "EXP_2":49, "EXP_3":53,
}
SPRITE_TILE_COUNT=57

def tile_planar(tile):
    out=bytearray()
    for y in range(8):
        for plane in range(4):
            bits=0
            for x in range(8):
                if (tile[y*8+x] >> plane) & 1: bits |= 0x80 >> x
            out.append(bits)
    return out

def image_tiles(im):
    pix=im.load(); out=[]
    for ty in range(0,im.height,8):
        for tx in range(0,im.width,8):
            tile=[]
            for y in range(8):
                for x in range(8): tile.append(pix[tx+x,ty+y] & 15)
            out.append(tile)
    return out

def make_sprite_art():
    # All sprites are authored at final SMS scale; enemy sizes are pre-scaled.
    def pimg(w,h):
        im=Image.new("P",(w,h),0); im.putpalette(color_table(SPR_RGB).getpalette()); return im
    ship=pimg(16,16); d=ImageDraw.Draw(ship)
    d.polygon([(7,1),(8,1),(10,5),(11,9),(15,12),(15,14),(10,13),(9,15),(7,15),(6,13),(1,14),(1,12),(5,9),(6,5)],fill=2,outline=3)
    d.polygon([(7,3),(8,3),(9,10),(8,12),(7,10)],fill=3)
    d.polygon([(2,12),(5,10),(6,12),(4,13)],fill=1)
    d.polygon([(13,12),(10,10),(9,12),(11,13)],fill=1)
    d.rectangle((6,14,6,15),fill=9); d.rectangle((9,14,9,15),fill=9)
    d.point((7,14),fill=15); d.point((8,14),fill=15)

    pulse=pimg(8,8); d=ImageDraw.Draw(pulse)
    d.rectangle((3,0,4,7),fill=9); d.rectangle((2,2,5,6),fill=1); d.rectangle((3,1,4,6),fill=15)
    spread=pimg(8,8); d=ImageDraw.Draw(spread)
    d.polygon([(3,1),(4,1),(5,4),(4,7),(3,7),(2,4)],fill=10,outline=15)
    d.point((1,2),fill=9); d.point((6,2),fill=9); d.point((2,5),fill=15); d.point((5,5),fill=15)
    lance=pimg(8,8); d=ImageDraw.Draw(lance)
    d.rectangle((2,0,5,7),fill=2); d.rectangle((3,0,4,7),fill=9); d.rectangle((3,1,4,6),fill=15)
    enemyshot=pimg(8,8); d=ImageDraw.Draw(enemyshot)
    d.polygon([(3,0),(4,0),(6,3),(4,7),(3,7),(1,3)],fill=6,outline=15)
    d.rectangle((3,2,4,5),fill=13)
    trackshot=pimg(8,8); d=ImageDraw.Draw(trackshot)
    d.ellipse((1,1,6,6),fill=13,outline=15); d.ellipse((3,3,4,4),fill=5)
    pickup_weapon=pimg(8,8); d=ImageDraw.Draw(pickup_weapon)
    d.polygon([(4,0),(7,4),(4,7),(1,4)],fill=10,outline=15); d.rectangle((3,2,4,5),fill=15)
    pickup_bomb=pimg(8,8); d=ImageDraw.Draw(pickup_bomb)
    d.ellipse((1,1,6,6),fill=5,outline=15); d.rectangle((3,0,5,1),fill=9); d.point((4,3),fill=15)
    pickup_repair=pimg(8,8); d=ImageDraw.Draw(pickup_repair)
    d.ellipse((1,1,6,6),fill=13,outline=15); d.rectangle((3,2,4,5),fill=15); d.rectangle((2,3,5,4),fill=15)

    def model(kind):
        im=pimg(24,24); d=ImageDraw.Draw(im)
        if kind==0: # MITE: compact beetle drone
            d.polygon([(11,2),(13,2),(15,7),(21,8),(23,12),(17,13),(15,18),(12,21),(9,18),(7,13),(1,12),(3,8),(9,7)],fill=4,outline=3)
            d.polygon([(11,4),(13,4),(14,11),(12,15),(10,11)],fill=7)
            d.polygon([(3,9),(8,10),(9,12),(5,12)],fill=6); d.polygon([(21,9),(16,10),(15,12),(19,12)],fill=6)
            d.rectangle((10,16,13,19),fill=5); d.point((11,8),fill=15); d.point((13,8),fill=15)
            d.line((6,7,4,4),fill=10); d.line((18,7,20,4),fill=10)
        elif kind==1: # VIPER: swept wings, hot core, long nose
            d.polygon([(11,1),(13,1),(15,8),(23,13),(22,16),(15,14),(14,20),(12,23),(10,20),(9,14),(2,16),(1,13),(9,8)],fill=8,outline=3)
            d.polygon([(11,3),(13,3),(13,14),(12,18),(11,14)],fill=9)
            d.polygon([(3,13),(9,11),(9,13),(5,15)],fill=6); d.polygon([(21,13),(15,11),(15,13),(19,15)],fill=6)
            d.ellipse((10,8,13,11),fill=15,outline=7); d.rectangle((10,19,11,22),fill=7); d.rectangle((13,19,14,22),fill=7)
        elif kind==2: # LANCER: narrow spear fighter with swept fins
            d.polygon([(11,0),(13,0),(15,8),(22,11),(23,14),(15,13),(13,21),(12,23),(11,21),(9,13),(1,14),(2,11),(9,8)],fill=13,outline=3)
            d.polygon([(11,3),(13,3),(13,15),(12,19),(11,15)],fill=9)
            d.polygon([(3,12),(9,10),(9,12),(5,13)],fill=10); d.polygon([(21,12),(15,10),(15,12),(19,13)],fill=10)
            d.ellipse((10,7,13,10),fill=15,outline=11); d.line((7,7,5,3),fill=15); d.line((17,7,19,3),fill=15)
        else: # BULWARK: slow armored gunship with side cannons
            d.polygon([(8,3),(16,3),(20,7),(23,12),(20,17),(16,20),(8,20),(4,17),(1,12),(4,7)],fill=12,outline=7)
            d.polygon([(9,5),(15,5),(18,9),(18,14),(15,18),(9,18),(6,14),(6,9)],fill=4,outline=15)
            d.rectangle((10,7,13,15),fill=5); d.rectangle((8,10,15,12),fill=9)
            d.rectangle((1,10,5,13),fill=7); d.rectangle((19,10,23,13),fill=7)
            d.point((11,9),fill=15); d.point((13,9),fill=15)
        return im

    mite,viper,lancer,bulwark=(model(i) for i in range(4))
    def scaled(im,size):
        return im.resize((size,size),Image.Resampling.NEAREST)

    # Four 16x16 blast frames, each prebuilt as a metasprite.
    explosions=[]
    for f in range(4):
        im=pimg(16,16); d=ImageDraw.Draw(im)
        rng=random.Random(71+f); cx=cy=7.5; rays=[]
        for k in range(10):
            a=math.tau*k/10 + f*0.17
            r0=2+f*1.5; r1=4+f*2.2+rng.random()*1.8
            rays.append((cx+math.cos(a)*r1,cy+math.sin(a)*r1))
            if f<3: d.line((cx+math.cos(a)*r0,cy+math.sin(a)*r0,*rays[-1]),fill=(15,9,7)[f],width=1)
        if f<3:
            d.polygon(rays,fill=(7,6,5)[f],outline=15)
            rr=max(1,4-f); d.ellipse((cx-rr,cy-rr,cx+rr,cy+rr),fill=(15,9,7)[f],outline=10)
        else:
            for x,y in rays: d.point((round(x),round(y)),fill=9)
            d.ellipse((5,5,10,10),fill=10,outline=15)
        explosions.append(im)

    tiles=[]
    tiles += image_tiles(ship)
    tiles += image_tiles(pulse)+image_tiles(spread)+image_tiles(lance)
    tiles += image_tiles(enemyshot)+image_tiles(trackshot)
    tiles += image_tiles(pickup_weapon)+image_tiles(pickup_bomb)+image_tiles(pickup_repair)
    for base in (mite,viper,lancer,bulwark):
        tiles += image_tiles(scaled(base,8)); tiles += image_tiles(scaled(base,16))
    tiles += image_tiles(scaled(bulwark,24))
    for im in explosions: tiles += image_tiles(im)
    assert len(tiles)==SPRITE_TILE_COUNT, (len(tiles),SPRITE_TILE_COUNT)
    packed=b"".join(tile_planar(t) for t in tiles)
    sprites=[ship,pulse,spread,lance,enemyshot,trackshot,pickup_weapon,pickup_bomb,pickup_repair,
             mite,viper,lancer,bulwark]
    return packed,sprites,explosions

def make_font_tiles():
    # 43 high-contrast 5x7 glyphs, four bar patterns and 16 star dither tiles.
    glyphs=[]
    pal=color_table([(0,0,0),(3,3,3)] + [(0,0,0)]*14).getpalette()
    for ch in CHAR_ORDER:
        im=Image.new("P",(8,8),0); im.putpalette(pal); d=ImageDraw.Draw(im)
        for y,row in enumerate(FONT[ch]):
            for x,bit in enumerate(row):
                if bit=='1': d.point((x+1,y),fill=1)
        glyphs += image_tiles(im)
    def solid(value): return [[value]*64]
    bars=[]
    for col in (13,4,8,11): bars += solid(col)
    stars=[]
    for i in range(16):
        im=Image.new("P",(8,8),0); im.putpalette(pal); d=ImageDraw.Draw(im)
        rng=random.Random(800+i)
        for _ in range(2+(i%3)):
            d.point((rng.randrange(8),rng.randrange(8)),fill=(10,11,14)[i%3])
        if i%4==0: d.point((3,3),fill=15)
        stars += image_tiles(im)
    arr=glyphs+bars+stars
    assert len(arr)==63, len(arr)
    return b"".join(tile_planar(t) for t in arr)

def emit_c(path, name, data, per=16):
    lines=[f"/* generated by render_assets.py: {name} */",f"const unsigned char {name}[] = {{"]
    for i in range(0,len(data),per):
        lines.append("  "+", ".join(f"0x{x:02x}" for x in data[i:i+per])+",")
    lines.append("};\n")
    Path(path).write_text("\n".join(lines))

def save_sprite_sheet(sprite_imgs, explosions):
    # Four 80px rows plus a bottom margin for 64px blast tiles at y=262.
    sheet=Image.new("RGB",(6*80,336),(5,8,18))
    d=ImageDraw.Draw(sheet)
    samples=[("PLAYER",sprite_imgs[0]),("PULSE",sprite_imgs[1]),("SPREAD",sprite_imgs[2]),
             ("LANCE",sprite_imgs[3]),("ENEMY SHOT",sprite_imgs[4]),("TRACK SHOT",sprite_imgs[5]),
             ("WEAPON ORB",sprite_imgs[6]),("BOMB ORB",sprite_imgs[7]),("REPAIR ORB",sprite_imgs[8]),
             ("MITE",sprite_imgs[9]),("VIPER",sprite_imgs[10]),("LANCER",sprite_imgs[11]),
             ("BULWARK",sprite_imgs[12])]
    for i,(label,im) in enumerate(samples):
        x=(i%6)*80+8; y=(i//6)*80+22
        rgb=im.convert("RGB").resize((im.width*2,im.height*2),Image.Resampling.NEAREST)
        sheet.paste(rgb,(x,y)); d.text((x,y-15),label,fill=(130,230,255))
    for i,im in enumerate(explosions):
        x=i*80+8; y=262
        sheet.paste(im.convert("RGB").resize((64,64),Image.Resampling.NEAREST),(x,y))
        d.text((x,y-14),f"BLAST {i+1}",fill=(255,190,80))
    sheet.save(ASSETS/"sprite-sheet.png")

def save_montage(frames, segments):
    keys=sorted(set(list(range(0,len(frames),24))+[len(frames)-1]))
    thumb=Image.new("RGB",(4*272,7*106),(7,9,20))
    d=ImageDraw.Draw(thumb)
    for j,index in enumerate(keys):
        if index>=len(frames): continue
        im=frames[index].convert("RGB").resize((256,80),Image.Resampling.NEAREST)
        x=(j%4)*272+8; y=(j//4)*106+3
        thumb.paste(im,(x,y))
        d.text((x,y+82),f"{index:03d}  {segments[index]}",fill=(160,225,255))
    thumb.save(ASSETS/"fmv-storyboard.png")

def render_video():
    frames=[]; labels=[]
    sectors=("ION HIGHWAY","ASH CATHEDRAL","CRYSTAL REACH","VOID THRONE")
    bosses=("DREAD MANTA","SOLAR CROWN","PRISM WYRM","VOID COLOSSUS")
    for level in range(4):
        bg=[render_background(level,math.tau*i/32) for i in range(32)]
        bg.append(bg[0].copy())
        frames.extend(bg); labels += [f"SECTOR {level+1:02d} / {sectors[level]}"]*33
        boss=[draw_boss(level,math.tau*i/64) for i in range(64)]
        boss.append(boss[0].copy())
        frames.extend(render_entry(bg[0],boss[0],32)); labels += [f"{bosses[level]} / INGRESS"]*32
        frames.extend(boss); labels += [f"3D BOSS / {bosses[level]}"]*65
        next_scene=render_background(level+1,0.0) if level<3 else victory_scene()
        frames.extend(render_boss_defeat(level,next_scene,32))
        labels += [f"{bosses[level]} / SHATTER"]*32
    assert len(frames)==648, len(frames)
    return frames,labels

def write_assets():
    ASSETS.mkdir(exist_ok=True); GEN.mkdir(exist_ok=True)
    frames,labels=render_video()
    with open(ASSETS/"frames.bin","wb") as f:
        for im in frames: f.write(bytes(im.getdata()))
    with open(ASSETS/"palette.bin","wb") as f:
        f.write(bytes(sms_color(c) for c in BG_RGB))
    sprite_data,sprite_imgs,explosions=make_sprite_art()
    emit_c(GEN/"art_data.c","sprite_tiles",sprite_data)
    font_data=make_font_tiles()
    emit_c(GEN/"font_data.c","font_tiles",font_data)
    header=f'''/* generated by tools/render_assets.py */
#ifndef RIFT_ART_DATA_H
#define RIFT_ART_DATA_H
#define SPRITE_TILE_COUNT {SPRITE_TILE_COUNT}
#define SPRITE_TILE_BYTES (SPRITE_TILE_COUNT * 32)
#define FONT_TILE_COUNT 63
#define FONT_TILE_BYTES (FONT_TILE_COUNT * 32)
#define PLAYER_TILE {SPRITE_BASES['PLAYER']}
#define PULSE_SHOT_TILE {SPRITE_BASES['PULSE_SHOT']}
#define SPREAD_SHOT_TILE {SPRITE_BASES['SPREAD_SHOT']}
#define LANCE_SHOT_TILE {SPRITE_BASES['LANCE_SHOT']}
#define ENEMY_SHOT_TILE {SPRITE_BASES['ENEMY_SHOT']}
#define TRACK_SHOT_TILE {SPRITE_BASES['TRACK_SHOT']}
#define PICKUP_WEAPON_TILE {SPRITE_BASES['PICKUP_WEAPON']}
#define PICKUP_BOMB_TILE {SPRITE_BASES['PICKUP_BOMB']}
#define PICKUP_REPAIR_TILE {SPRITE_BASES['PICKUP_REPAIR']}
#define MITE_8_TILE {SPRITE_BASES['MITE_8']}
#define MITE_16_TILE {SPRITE_BASES['MITE_16']}
#define VIPER_8_TILE {SPRITE_BASES['VIPER_8']}
#define VIPER_16_TILE {SPRITE_BASES['VIPER_16']}
#define LANCER_8_TILE {SPRITE_BASES['LANCER_8']}
#define LANCER_16_TILE {SPRITE_BASES['LANCER_16']}
#define BULWARK_8_TILE {SPRITE_BASES['BULWARK_8']}
#define BULWARK_16_TILE {SPRITE_BASES['BULWARK_16']}
#define BULWARK_24_TILE {SPRITE_BASES['BULWARK_24']}
#define EXP_0_TILE {SPRITE_BASES['EXP_0']}
#define EXP_1_TILE {SPRITE_BASES['EXP_1']}
#define EXP_2_TILE {SPRITE_BASES['EXP_2']}
#define EXP_3_TILE {SPRITE_BASES['EXP_3']}
#define FONT_TILE_BASE {FONT_BASE}
#define BAR_FULL_TILE {BAR_FULL}
#define BAR_EMPTY_TILE {BAR_EMPTY}
#define BAR_WARN_TILE {BAR_WARN}
#define BAR_CYAN_TILE {BAR_CYAN}
#define STAR_TILE_BASE {STAR_BASE}
#define BLANK_TILE_ID {BLANK_TILE}
extern const unsigned char sprite_tiles[SPRITE_TILE_BYTES];
extern const unsigned char font_tiles[FONT_TILE_BYTES];
extern const unsigned char sprite_palette[16];
#endif
'''
    (GEN/"art_data.h").write_text(header)
    pal_c="/* generated by tools/render_assets.py */\nconst unsigned char sprite_palette[16] = {"+", ".join(f"0x{sms_color(c):02x}" for c in SPR_RGB)+"};\n"
    with open(GEN/"art_data.c","a") as f: f.write("\n"+pal_c)
    with open(GEN/"font_data.c","a") as f: f.write("\n#include \"art_data.h\"\n")
    # Add proper declarations and header includes to generated objects.
    for path in (GEN/"art_data.c",GEN/"font_data.c"):
        s=path.read_text()
        if '#include "art_data.h"' not in s:
            path.write_text('#include "art_data.h"\n'+s)
    save_sprite_sheet(sprite_imgs,explosions)
    save_montage(frames,labels)
    level_names=("ION HIGHWAY","ASH CATHEDRAL","CRYSTAL REACH","VOID THRONE")
    boss_names=("DREAD MANTA","SOLAR CROWN","PRISM WYRM","VOID COLOSSUS")
    segments={}
    for level in range(4):
        base=level*162
        segments[f"sector{level}_loop"]={"start":base,"count":33,"repeat":"32 views and duplicate frame zero"}
        segments[f"boss{level}_entry"]={"start":base+33,"count":32,"name":boss_names[level]}
        segments[f"boss{level}_loop"]={"start":base+65,"count":65,"repeat":"64 angles and duplicate frame zero","name":boss_names[level]}
        segments[f"boss{level}_defeat"]={"start":base+130,"count":32,"name":boss_names[level]}
    manifest={
        "title":"RIFT RUNNER",
        "levels":[{"name":level_names[i],"boss":boss_names[i]} for i in range(4)],
        "video":{"width":W,"height":H,"frames":len(frames),"palette_entries":16,
                 "format":"indexed 4-bit palette values, row-major, one frame after another"},
        "segments":segments,
        "sprite_tiles":SPRITE_TILE_COUNT,
        "prescaled_enemies":["MITE","VIPER","LANCER","BULWARK"],
        "font_glyphs":len(CHAR_ORDER),
        "renderer":"host-side flat-shaded low-poly projection; VDP runs only the tile-delta decoder"
    }
    (ASSETS/"render-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(f"Rendered {len(frames)} FMV frames: {W}x{H} ({W*H*len(frames):,} raw bytes)")
    print(f"Sprite patterns: {SPRITE_TILE_COUNT} (pre-scaled Mite/Viper/Lancer 8/16; Bulwark 8/16/24)")
    print(f"Wrote {ASSETS/'frames.bin'}, {ASSETS/'palette.bin'}, {GEN/'art_data.c'}")

if __name__=="__main__":
    write_assets()
()
