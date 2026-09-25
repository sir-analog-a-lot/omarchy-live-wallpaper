#!/usr/bin/env python3
"""Generate an ORIGINAL Picasso-inspired (cubist) background.  No artwork is copied:
every shape is procedurally generated from primitives with a fixed random seed."""
import math, random, sys
import cairo
import numpy as np

W, H = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (3440, 1440)
OUT = sys.argv[3] if len(sys.argv) > 3 else "background.png"
rnd = random.Random(1907)          # year of Les Demoiselles -> seed only
U = H / 1440.0                      # unit scale

def hexc(h, a=1.0):
    h = h.lstrip('#'); return (int(h[0:2],16)/255, int(h[2:4],16)/255, int(h[4:6],16)/255, a)

PAL = dict(ochre='#c79a5b', sienna='#9a5a2c', umber='#4e3320', slate='#51708f', prussian='#27384d',
           sage='#7b8a70', cream='#e6d8b8', black='#1b1714', red='#963a2c', grey='#8d8778',
           sand='#b9a077', teal='#3f6b6b', rose='#c2826a', blue='#3a5a86')

surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
cr = cairo.Context(surf)

def poly(pts):
    cr.move_to(*pts[0])
    for p in pts[1:]: cr.line_to(*p)
    cr.close_path()

def lin_fill(pts, c1, c2, a=1.0):
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    ang = rnd.uniform(0, math.pi)
    cx, cy = sum(xs)/len(xs), sum(ys)/len(ys); r = max(max(xs)-min(xs), max(ys)-min(ys))/2+1
    g = cairo.LinearGradient(cx-math.cos(ang)*r, cy-math.sin(ang)*r, cx+math.cos(ang)*r, cy+math.sin(ang)*r)
    g.add_color_stop_rgba(0, *hexc(c1)[:3], a); g.add_color_stop_rgba(1, *hexc(c2)[:3], a)
    poly(pts); cr.set_source(g); cr.fill()

def stroke(c='black', w=6, a=0.9):
    cr.set_source_rgba(*hexc(PAL[c])[:3], a); cr.set_line_width(w*U); cr.set_line_cap(cairo.LINE_CAP_ROUND); cr.set_line_join(cairo.LINE_JOIN_ROUND); cr.stroke()

# ---------- 1. ground ----------
g = cairo.LinearGradient(0, 0, W, H)
g.add_color_stop_rgb(0, *hexc('#8a6a45')[:3]); g.add_color_stop_rgb(.5, *hexc('#a88657')[:3]); g.add_color_stop_rgb(1, *hexc('#5d4a3a')[:3])
cr.set_source(g); cr.paint()

# ---------- 2. faceted planes (analytic cubism) ----------
planes = ['ochre','sienna','umber','slate','sage','grey','sand','cream','prussian','teal']
for i in range(170):
    cx, cy = rnd.uniform(-.05,1.05)*W, rnd.uniform(-.05,1.05)*H
    n = rnd.choice([3,4,4,5])
    rad = rnd.uniform(80,380)*U
    a0 = rnd.uniform(0, 2*math.pi)
    pts = [(cx+math.cos(a0+k*2*math.pi/n+rnd.uniform(-.35,.35))*rad*rnd.uniform(.6,1.3),
            cy+math.sin(a0+k*2*math.pi/n+rnd.uniform(-.35,.35))*rad*rnd.uniform(.6,1.3)) for k in range(n)]
    c1 = PAL[rnd.choice(planes)]; c2 = PAL[rnd.choice(planes)]
    lin_fill(pts, c1, c2, rnd.uniform(.35,.75))
    if rnd.random() < .45:
        poly(pts); stroke('black', rnd.uniform(1.5,4), rnd.uniform(.25,.6))

# long cutting lines across the canvas (grid of analytic cubism)
for i in range(26):
    x0 = rnd.uniform(0,W); ang = rnd.uniform(-1.3,1.3)
    cr.move_to(x0 - math.sin(ang)*H*1.2, -H*.1); cr.line_to(x0 + math.sin(ang)*H*1.2, H*1.1)
    stroke('black', rnd.uniform(1.2,3.2), rnd.uniform(.15,.4))

# ---------- helpers for motifs ----------
def harlequin(x, y, w, h, cols, n=6, a=.85):
    cr.save(); cr.rectangle(x,y,w,h); cr.clip()
    dw = w/n; dh = dw*1.5
    j=0
    for yy in np.arange(y-dh, y+h+dh, dh/2):
        off = (j%2)*dw/2
        for xx in np.arange(x-dw+off, x+w+dw, dw):
            poly([(xx,yy-dh/2),(xx+dw/2,yy),(xx,yy+dh/2),(xx-dw/2,yy)])
            cr.set_source_rgba(*hexc(PAL[cols[(int((xx-x)/dw)+j)%len(cols)]])[:3], a); cr.fill_preserve()
            stroke('black', 2, .5)
        j+=1
    cr.restore()

def dots(x, y, w, h, c='cream', step=18, r=4, bg=None, a=.8):
    cr.save(); cr.rectangle(x,y,w,h); cr.clip()
    if bg: cr.set_source_rgba(*hexc(PAL[bg])[:3], a); cr.paint()
    cr.set_source_rgba(*hexc(PAL[c])[:3], a)
    j=0
    for yy in np.arange(y, y+h+step*U, step*U):
        for xx in np.arange(x+(j%2)*step*U/2, x+w+step*U, step*U):
            cr.arc(xx,yy,r*U,0,2*math.pi); cr.fill()
        j+=1
    cr.restore()

def woodgrain(pts, base='sienna', line='umber'):
    cr.save(); poly(pts); cr.clip()
    cr.set_source_rgba(*hexc(PAL[base])[:3], .92); cr.paint()
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    for k in range(40):
        y0 = min(ys) + (max(ys)-min(ys))*k/40
        cr.move_to(min(xs)-20, y0)
        amp = rnd.uniform(4,14)*U; ph=rnd.uniform(0,6)
        for xx in np.arange(min(xs)-20, max(xs)+20, 12*U):
            cr.line_to(xx, y0 + amp*math.sin(xx/(60*U)+ph) + amp*.5*math.sin(xx/(23*U)))
        stroke(line, rnd.uniform(1,3), .55)
    cr.restore()

def newspaper(x, y, w, h, rot, title="LE JOU"):
    cr.save(); cr.translate(x,y); cr.rotate(rot)
    cr.rectangle(0,0,w,h); cr.set_source_rgba(*hexc('#e9dfc6')[:3], .93); cr.fill()
    cr.select_font_face("C059", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(h*.26); cr.set_source_rgba(*hexc(PAL['black'])[:3], .88)
    cr.move_to(w*.06, h*.3); cr.show_text(title)
    cr.set_source_rgba(0,0,0,.45)
    yy = h*.42
    while yy < h*.95:
        xx = w*.06
        while xx < w*.94:
            ln = rnd.uniform(.03,.12)*w
            cr.rectangle(xx, yy, min(ln, w*.94-xx), h*.018); xx += ln + w*.015
        cr.fill(); yy += h*.045
    cr.rectangle(0,0,w,h); stroke('black', 2.5, .6)
    cr.restore()

# ---------- 3. synthetic-cubism collage patches ----------
harlequin(.62*W, .05*H, .13*W, .42*H, ['red','ochre','slate','cream','black'], n=5, a=.55)
dots(.02*W, .62*H, .16*W, .30*H, c='cream', bg='prussian', a=.55)
dots(.80*W, .70*H, .12*W, .26*H, c='ochre', bg='umber', step=22, r=5, a=.5)
newspaper(.30*W, .10*H, .16*W, .22*H, -.08)

woodgrain([(.12*W,.0),(.30*W,.0),(.26*W,.40*H),(.08*W,.46*H)])
woodgrain([(.50*W,.70*H),(.66*W,.62*H),(.70*W,1.0*H),(.46*W,1.0*H)], base='ochre', line='sienna')

# music sheet fragment
cr.save(); cr.translate(.46*W,.02*H); cr.rotate(.06)
cr.rectangle(0,0,.11*W,.2*H); cr.set_source_rgba(*hexc('#ddd0b0')[:3], .9); cr.fill()
for s in range(2):
    for l in range(5):
        yy = .04*H + s*.08*H + l*.011*H
        cr.move_to(.008*W, yy); cr.line_to(.10*W, yy)
    stroke('black', 1.6, .7)
for k in range(9):
    cr.arc(.015*W + k*.0095*W, .045*H + rnd.randint(0,8)*.0055*H + (k>4)*.08*H, 5*U, 0, 2*math.pi)
    cr.set_source_rgba(0,0,0,.8); cr.fill()
cr.restore()

# ---------- 4. guitar (left) ----------
def guitar(cx, cy, s, rot):
    cr.save(); cr.translate(cx,cy); cr.rotate(rot)
    # body: split into two displaced halves (cubist fragmentation)
    def body():
        cr.new_path(); cr.arc(0, s*.95, s*1.05, 0, 2*math.pi); cr.new_sub_path(); cr.arc(0, -s*.25, s*.78, 0, 2*math.pi)
    for side, col, dx in ((-1,'ochre',0), (1,'sienna',-18*U)):
        cr.save()
        cr.rectangle(-4*s if side<0 else 0, -4*s, 4*s, 8*s); cr.clip()
        cr.translate(dx, 0)
        body(); stroke('black', 12, 1.0)
        body(); cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        cr.save(); cr.clip(); cr.set_source_rgba(*hexc(PAL[col])[:3], 1); cr.paint()
        if side > 0:
            for k in range(30):
                yy = -1.2*s + k*s*.12
                cr.move_to(-2*s, yy); cr.curve_to(-s, yy+10*U, s, yy-12*U, 2*s, yy+6*U)
            cr.set_source_rgba(*hexc(PAL['umber'])[:3], .5); cr.set_line_width(2*U); cr.stroke()
        else:
            g2 = cairo.RadialGradient(-s*.3, s*.5, s*.1, 0, s*.6, s*1.4)
            g2.add_color_stop_rgba(0, 1, 1, 1, .18); g2.add_color_stop_rgba(1, 0, 0, 0, .25)
            cr.set_source(g2); cr.paint()
        cr.restore()
        cr.restore()
    cr.move_to(0, -s*1.1); cr.line_to(0, s*2.0); stroke('black', 5)
    # sound hole + rosette
    cr.arc(0, s*.25, s*.32, 0, 2*math.pi); cr.set_source_rgba(*hexc(PAL['black'])[:3], .95); cr.fill()
    cr.arc(0, s*.25, s*.40, 0, 2*math.pi); stroke('cream', 5, .8)
    # neck
    poly([(-s*.16,-s*.2),(s*.16,-s*.2),(s*.13,-s*3.1),(-s*.13,-s*3.1)])
    cr.set_source_rgba(*hexc(PAL['umber'])[:3], .95); cr.fill_preserve(); stroke('black', 4)
    for f in range(1,12):
        yy = -s*.2 - f*s*.26
        cr.move_to(-s*.15, yy); cr.line_to(s*.15, yy)
    stroke('grey', 2, .8)
    # head
    poly([(-s*.2,-s*3.1),(s*.2,-s*3.1),(s*.26,-s*3.7),(-s*.18,-s*3.75)])
    cr.set_source_rgba(*hexc(PAL['black'])[:3], .9); cr.fill()
    # bridge + strings
    cr.rectangle(-s*.3, s*1.35, s*.6, s*.1); cr.set_source_rgba(*hexc(PAL['black'])[:3], .9); cr.fill()
    for k in range(6):
        x = -s*.1 + k*s*.04
        cr.move_to(x, s*1.38); cr.line_to(x*.9, -s*3.1)
    stroke('cream', 1.4, .9)
    cr.restore()

guitar(.14*W, .44*H, 175*U, -.35)

# ---------- 5. still life (bottle, glass, fruit bowl) ----------
def bottle(x, y, s):
    cr.save(); cr.translate(x,y)
    body=[(-s*.35,0),(s*.35,0),(s*.35,-s*1.4),(s*.14,-s*1.75),(s*.12,-s*2.4),(-s*.12,-s*2.4),(-s*.14,-s*1.75),(-s*.35,-s*1.4)]
    cr.save(); poly(body); cr.clip()
    cr.rectangle(-s,-3*s,s,3*s); cr.set_source_rgba(*hexc(PAL['teal'])[:3], .9); cr.fill()
    cr.rectangle(0,-3*s,s,3*s); cr.set_source_rgba(*hexc(PAL['prussian'])[:3], .9); cr.fill()
    cr.rectangle(-s,-s*.9,2*s,s*.45); cr.set_source_rgba(*hexc(PAL['cream'])[:3], .9); cr.fill()
    cr.restore()
    poly(body); stroke('black', 5)
    cr.select_font_face("C059", cairo.FONT_SLANT_ITALIC, cairo.FONT_WEIGHT_BOLD); cr.set_font_size(s*.2)
    cr.set_source_rgba(*hexc(PAL['red'])[:3], .9); cr.move_to(-s*.3, -s*.6); cr.show_text("VIEUX")
    cr.restore()

def glass(x, y, s):
    cr.save(); cr.translate(x,y)
    cr.move_to(-s*.45,-s*1.6); cr.curve_to(-s*.45,-s*.8, s*.45,-s*.8, s*.45,-s*1.6)
    cr.move_to(0,-s*.95); cr.line_to(0,-s*.1)
    cr.move_to(-s*.35,0); cr.line_to(s*.35,0)
    stroke('black', 4.5)
    cr.save(); cr.scale(1,.3); cr.arc(0,-s*1.6/.3, s*.45, 0, 2*math.pi); cr.restore(); stroke('cream', 3.5, .9)
    cr.restore()

def fruit_bowl(x, y, s):
    cr.save(); cr.translate(x,y)
    for (fx,fy,c) in ((-s*.4,-s*.35,'red'),(s*.05,-s*.5,'ochre'),(s*.45,-s*.32,'sage'),(-s*.05,-s*.2,'rose')):
        cr.arc(fx,fy,s*.28,0,2*math.pi); cr.set_source_rgba(*hexc(PAL[c])[:3], .95); cr.fill_preserve(); stroke('black', 3.5)
        cr.arc(fx-s*.08,fy-s*.08,s*.07,0,2*math.pi); cr.set_source_rgba(1,1,1,.35); cr.fill()
    poly([(-s*.9,-s*.25),(s*.9,-s*.25),(s*.55,s*.2),(-s*.55,s*.2)])
    cr.set_source_rgba(*hexc(PAL['grey'])[:3], .95); cr.fill_preserve(); stroke('black', 4.5)
    poly([(-s*.15,s*.2),(s*.15,s*.2),(s*.3,s*.45),(-s*.3,s*.45)])
    cr.set_source_rgba(*hexc(PAL['umber'])[:3], .95); cr.fill_preserve(); stroke('black', 4)
    cr.restore()

# table top plane
lin_fill([(.30*W,.40*H),(.62*W,.34*H),(.66*W,.48*H),(.27*W,.55*H)], PAL['sand'], PAL['ochre'], .75)
poly([(.30*W,.40*H),(.62*W,.34*H),(.66*W,.48*H),(.27*W,.55*H)]); stroke('black', 5, .8)
bottle(.37*W, .46*H, 150*U)
glass(.44*W, .44*H, 120*U)
fruit_bowl(.54*W, .40*H, 150*U)

# ---------- 6. cubist face (right): frontal + profile combined ----------
def face(cx, cy, s):
    cr.save(); cr.translate(cx,cy)
    # hair / background shape
    poly([(-s*1.1,-s*1.3),(s*.2,-s*1.75),(s*1.25,-s*1.1),(s*1.2,s*.3),(-s*1.2,s*.5)])
    cr.set_source_rgba(*hexc(PAL['black'])[:3], .85); cr.fill()
    # neck
    poly([(-s*.35,s*1.0),(s*.35,s*1.0),(s*.45,s*2.2),(-s*.5,s*2.2)])
    cr.set_source_rgba(*hexc(PAL['rose'])[:3], .95); cr.fill_preserve(); stroke('black', 5)
    # head oval split in two tones (frontal left / profile right)
    def head(): cr.save(); cr.scale(.85,1.2); cr.arc(0,0,s,0,2*math.pi); cr.restore()
    cr.save(); head(); cr.clip()
    cr.rectangle(-2*s,-2*s,2*s,4*s); cr.set_source_rgba(*hexc(PAL['blue'])[:3], .97); cr.fill()
    cr.rectangle(0,-2*s,2*s,4*s); cr.set_source_rgba(*hexc(PAL['ochre'])[:3], .97); cr.fill()
    poly([(-s*.2,-s*1.4),(s*.5,-s*.2),(s*.1,s*1.5),(-s*.6,s*.2)])
    cr.set_source_rgba(*hexc(PAL['cream'])[:3], .35); cr.fill()
    cr.restore()
    head(); stroke('black', 7)
    # profile nose (sticking out to the right, over the frontal face)
    poly([(s*.05,-s*.55),(s*.62,s*.25),(s*.08,s*.3)])
    cr.set_source_rgba(*hexc(PAL['sienna'])[:3], .97); cr.fill_preserve(); stroke('black', 5)
    # eyes: frontal almond (left), profile triangle (right), at different heights
    cr.save(); cr.translate(-s*.38,-s*.35); cr.scale(1,.45); cr.arc(0,0,s*.22,0,2*math.pi); cr.restore()
    cr.set_source_rgba(*hexc(PAL['cream'])[:3], 1); cr.fill_preserve(); stroke('black', 4.5)
    cr.arc(-s*.38,-s*.35,s*.07,0,2*math.pi); cr.set_source_rgba(*hexc(PAL['black'])[:3],1); cr.fill()
    poly([(s*.25,-s*.62),(s*.52,-s*.52),(s*.28,-s*.44)])
    cr.set_source_rgba(*hexc(PAL['cream'])[:3], 1); cr.fill_preserve(); stroke('black', 4)
    cr.arc(s*.33,-s*.53,s*.045,0,2*math.pi); cr.set_source_rgba(0,0,0,1); cr.fill()
    # brows
    cr.move_to(-s*.62,-s*.55); cr.curve_to(-s*.45,-s*.72,-s*.25,-s*.7,-s*.12,-s*.55); stroke('black', 5)
    cr.move_to(s*.2,-s*.72); cr.line_to(s*.55,-s*.66); stroke('black', 5)
    # lips (stacked, cubist)
    cr.move_to(-s*.3,s*.62); cr.curve_to(-s*.15,s*.5,s*.05,s*.5,s*.18,s*.6)
    cr.curve_to(s*.05,s*.78,-s*.15,s*.78,-s*.3,s*.62); cr.close_path()
    cr.set_source_rgba(*hexc(PAL['red'])[:3], 1); cr.fill_preserve(); stroke('black', 4)
    # ear (profile side)
    cr.save(); cr.translate(s*.78,-s*.05); cr.scale(.6,1); cr.arc(0,0,s*.2,-math.pi/2,math.pi/2); cr.restore(); stroke('black', 5)
    # a few hatching strokes on the cheek
    for k in range(6):
        cr.move_to(-s*.65+k*s*.05, s*.05); cr.line_to(-s*.55+k*s*.05, s*.35)
    stroke('black', 2.5, .6)
    cr.restore()

face(.86*W, .30*H, 190*U)
newspaper(.895*W, .50*H, .09*W, .14*H, .12, "ATELIER")

# second, smaller fragmented profile (left bottom) in blue-period tones
cr.save(); cr.translate(.30*W, .80*H); cr.rotate(.25)
poly([(-120*U,-160*U),(60*U,-190*U),(130*U,-40*U),(190*U,10*U),(120*U,40*U),(110*U,170*U),(-140*U,160*U)])
cr.set_source_rgba(*hexc(PAL['prussian'])[:3], .8); cr.fill_preserve(); stroke('black', 5)
cr.arc(40*U,-70*U,26*U,0,2*math.pi); cr.set_source_rgba(*hexc(PAL['cream'])[:3], .9); cr.fill_preserve(); stroke('black', 3)
cr.arc(40*U,-70*U,9*U,0,2*math.pi); cr.set_source_rgba(0,0,0,1); cr.fill()
cr.restore()

# sun / moon disc and crescent shapes
cr.arc(.72*W,.16*H,85*U,0,2*math.pi); cr.set_source_rgba(*hexc(PAL['cream'])[:3], .55); cr.fill_preserve(); stroke('black', 4, .7)
cr.arc(.735*W,.15*H,70*U,0,2*math.pi); cr.set_source_rgba(*hexc(PAL['ochre'])[:3], .55); cr.fill()

# ---------- 7. a second pass of translucent shards over everything (shattered-plane look) ----------
for i in range(55):
    cx, cy = rnd.uniform(0,W), rnd.uniform(0,H)
    rad = rnd.uniform(60,260)*U; a0 = rnd.uniform(0,6.3)
    pts = [(cx+math.cos(a0+k*2.1+rnd.uniform(-.3,.3))*rad, cy+math.sin(a0+k*2.1+rnd.uniform(-.3,.3))*rad) for k in range(3)]
    lin_fill(pts, PAL[rnd.choice(planes)], '#000000' if rnd.random()<.3 else PAL['cream'], rnd.uniform(.08,.22))
    if rnd.random()<.5: poly(pts); stroke('black', rnd.uniform(1,2.5), .35)

surf.flush()
# ---------- 8. canvas grain, craquelure-ish noise, vignette (numpy) ----------
buf = np.ndarray((H, surf.get_stride()//4, 4), np.uint8, surf.get_data())[:, :W, :3].astype(np.float32)
rng = np.random.default_rng(7)
# canvas weave
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
weave = (np.sin(xx*1.9)*np.sin(yy*1.9))*4.0
grain = rng.normal(0, 7, (H, W)).astype(np.float32)
# low-frequency mottling
lo = rng.normal(0, 1, (H//40+2, W//40+2)).astype(np.float32)
from PIL import Image
lo = np.asarray(Image.fromarray(((lo-lo.min())/(lo.ptp() if hasattr(lo,'ptp') else (lo.max()-lo.min()))*255).astype(np.uint8)).resize((W,H), Image.BICUBIC), np.float32)/255-.5
noise = weave + grain + lo*22
buf += noise[..., None]
# vignette
nx = (xx/W-.5)*2; ny = (yy/H-.5)*2
v = 1 - 0.45*np.clip((nx**2*0.7 + ny**2), 0, 2)**1.4
buf *= np.clip(v, .35, 1)[..., None]
# slight overall desaturation + warm tone (aged varnish)
lum = buf.mean(axis=2, keepdims=True)
buf = buf*0.82 + lum*0.18
buf[..., 2] *= 1.04   # (BGR order) warm red channel
buf[..., 0] *= 0.93   # reduce blue a bit
out = np.clip(buf, 0, 255).astype(np.uint8)
Image.fromarray(out[..., ::-1]).save(OUT, optimize=True)
print("wrote", OUT, W, H)
