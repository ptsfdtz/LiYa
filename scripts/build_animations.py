"""Assemble Liya's generated key poses into motion-compensated, 20 FPS GIFs.

Run with the separate artwork environment, not the MaiBot runtime:
  python scripts/build_animations.py
Dependencies: Pillow, numpy, opencv-python-headless (requirements-artwork.txt).
Image generation supplies artwork; this script only extracts, aligns, interpolates
and encodes animation frames. It never redraws facial features.
"""
from pathlib import Path
import hashlib
import json
import shutil
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'liya_companion'))
from state import EXPRESSIONS  # noqa: E402

OUT = ROOT / 'assets' / 'expressions'
RUNTIME = ROOT / 'src' / 'liya_companion' / 'assets'
SIZE = (192, 208)
COUNT = 48
DURATION = 50  # GIF centiseconds represent 20 FPS exactly.
OLD = list(EXPRESSIONS)[:9]
NEW = list(EXPRESSIONS)[9:]
# Number of samples between each of the four cyclic key poses. Blink closes
# and reopens quickly; breathing and the return to rest take longer.
TIMING = {
    'idle': [20, 3, 3, 22], 'happy': [12, 10, 10, 16],
    'curious': [14, 8, 10, 16], 'thinking': [16, 4, 6, 22],
    'sulky': [16, 4, 6, 22], 'caring': [16, 4, 6, 22],
    'greeting': [10, 10, 12, 16], 'sleepy': [16, 8, 10, 14],
    'excited': [10, 10, 12, 16],
}


def keyposes():
    atlas = Image.open(OUT / 'liya-animation-atlas.png').convert('RGBA')
    poses = {name: [atlas.crop((col*192, row*208, (col+1)*192, (row+1)*208))
                    for col in range(4)] for row, name in enumerate(OLD)}
    sheet = Image.open(ROOT / 'assets/animation-sources/emotions-v2-keyframes.png').convert('RGBA')
    # Cell boundaries select a viewport; actual alpha components determine the
    # character bounds. Reject crops with missing or touching-edge characters.
    for row, name in enumerate(NEW):
        crops = []
        for col in range(4):
            box = (round(col*sheet.width/4), round(row*sheet.height/6),
                   round((col+1)*sheet.width/4), round((row+1)*sheet.height/6))
            cell = np.array(sheet.crop(box))
            alpha = cell[:, :, 3].astype(np.float32)
            # Generated transparency includes a soft gray alpha veil outside
            # the opaque character (alpha 252-253). Reject that low-alpha
            # veil and remap the remaining original character edge.
            cell[:, :, 3] = np.clip((alpha-160)*255/80, 0, 255).astype(np.uint8)
            mask = (cell[:, :, 3] > 64).astype(np.uint8)
            components, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
            if components < 2:
                raise ValueError(f'No character in {name}/{col}')
            main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            x, y, w, h, area = stats[main]
            if area < 2000 or x <= 0 or y <= 0 or x+w >= cell.shape[1] or y+h >= cell.shape[0]:
                raise ValueError(f'Missing/clipped character in {name}/{col}: {stats[main]}')
            # Include all foreground parts (e.g. a detached hair tip) in the
            # viewport while discarding small isolated alpha noise.
            keep = np.isin(labels, np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= 32)) & (labels != 0)
            keep = cv2.dilate(keep.astype(np.uint8), np.ones((3, 3), np.uint8))
            cell[:, :, 3] *= keep
            ys, xs = np.where(cell[:, :, 3] > 64)
            bounds = (max(0, xs.min()-2), max(0, ys.min()-2),
                      min(cell.shape[1], xs.max()+3), min(cell.shape[0], ys.max()+3))
            crops.append(Image.fromarray(cell).crop(bounds))
        scale = min(182/max(im.width for im in crops), 196/max(im.height for im in crops))
        aligned = []
        for crop in crops:
            crop = crop.resize((round(crop.width*scale), round(crop.height*scale)), Image.Resampling.LANCZOS)
            frame = Image.new('RGBA', SIZE)
            frame.alpha_composite(crop, ((SIZE[0]-crop.width)//2, 203-crop.height))
            aligned.append(frame)
        poses[name] = aligned
    return poses


def motion_pair(a, b):
    def gray(im):
        rgba = np.array(im).astype(np.float32)/255
        rgb = rgba[:, :, :3]*rgba[:, :, 3:] + (1-rgba[:, :, 3:])*0.95
        return cv2.cvtColor((rgb*255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    ga, gb = gray(a), gray(b)
    estimator = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    estimator.setFinestScale(0)
    estimator.setGradientDescentIterations(30)
    estimator.setVariationalRefinementIterations(8)
    def initial(source, target):
        # Seed large body translations (especially the jump) before local
        # correspondence search, rather than asking it to discover a leap.
        x0,y0,x1,y1 = source.getbbox()
        u0,v0,u1,v1 = target.getbbox()
        scale = np.clip((v1-v0)/(y1-y0), 0.85, 1.15)
        yy,xx = np.mgrid[:SIZE[1], :SIZE[0]].astype(np.float32)
        dx = (xx-(x0+x1)/2)*scale+(u0+u1)/2-xx
        dy = (yy-(y0+y1)/2)*scale+(v0+v1)/2-yy
        return np.stack((dx,dy),axis=-1).astype(np.float32)
    forward = estimator.calc(ga, gb, initial(a,b))
    backward = estimator.calc(gb, ga, initial(b,a))
    return forward, backward


def inbetween(a, b, forward, backward, t):
    # Warp both endpoints toward the intermediate pose, then combine in
    # premultiplied alpha. Plain crossfades produce double contours.
    yy, xx = np.mgrid[:SIZE[1], :SIZE[0]].astype(np.float32)
    def warp(image, flow, amount):
        data = np.array(image).astype(np.float32)/255
        data[:, :, :3] *= data[:, :, 3:]
        mapx, mapy = xx.copy(), yy.copy()
        for _ in range(5):
            displacement = cv2.remap(flow, mapx, mapy, cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
            mapx = (xx-displacement[:, :, 0]*amount).astype(np.float32)
            mapy = (yy-displacement[:, :, 1]*amount).astype(np.float32)
        return cv2.remap(data, mapx, mapy, cv2.INTER_CUBIC,
                         borderMode=cv2.BORDER_CONSTANT)
    aa, bb = warp(a, forward, t), warp(b, backward, 1-t)
    # Newly revealed details cannot be matched reliably between drawings.
    # Use the nearer warped endpoint there, avoiding double eyes/fingers.
    disagreement = np.max(abs(aa-bb), axis=2, keepdims=True)
    uncertain = np.clip((disagreement-0.08)/0.12, 0, 1)
    weight = t*(1-uncertain)+(float(t >= 0.5))*uncertain
    mixed = np.clip((1-weight)*aa+weight*bb, 0, 1)
    mixed[:, :, :3] /= np.maximum(mixed[:, :, 3:], 1/255)
    return Image.fromarray(np.clip(mixed*255, 0, 255).astype(np.uint8))


def animate(name, poses):
    result = []
    timing = TIMING.get(name, [16, 4, 6, 22])
    assert sum(timing) == COUNT
    for index, samples in enumerate(timing):
        a, b = poses[index], poses[(index+1) % len(poses)]
        forward, backward = motion_pair(a, b)
        for sample in range(samples):
            t = sample/samples
            # Cosine ease-in-out: zero velocity at each drawn key pose and
            # at the loop seam. This is character motion, not a UI transition.
            t = (1-np.cos(np.pi*t))/2
            result.append(a.copy() if sample == 0 else inbetween(a, b, forward, backward, t))
    return result


def save_gif(frames, path):
    # A fixed palette for the entire loop prevents per-frame color flicker.
    samples = Image.new('RGB', (frames[0].width*4, frames[0].height*3), '#f7f5f0')
    for i in range(12):
        im = frames[i*len(frames)//12]
        samples.paste(im.convert('RGB'), ((i % 4)*im.width, (i//4)*im.height))
    palette = samples.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    palette.putpalette(palette.getpalette()[:765] + [0, 0, 0])
    indexed = []
    for frame in frames:
        pal = frame.convert('RGB').quantize(palette=palette, dither=Image.Dither.NONE)
        data = np.array(pal)
        # Index 255 is exclusively transparent, including for opaque dark
        # outlines that the quantizer might otherwise map to its black entry.
        reserved = data == 255
        if reserved.any():
            colors = np.array(palette.getpalette()[:765], dtype=np.int32).reshape(255,3)
            pixels = np.array(frame)[:, :, :3][reserved].astype(np.int32)
            data[reserved] = np.argmin(np.sum((pixels[:, None, :]-colors[None, :, :])**2, axis=2), axis=1)
        data[np.array(frame)[:, :, 3] < 128] = 255
        pal = Image.fromarray(data).convert('P')
        pal.putpalette(palette.getpalette())
        indexed.append(pal)
    temporary = path.with_suffix('.building.gif')
    indexed[0].save(temporary, save_all=True, append_images=indexed[1:], duration=DURATION,
                    loop=0, transparency=255, disposal=2, optimize=False)
    temporary.replace(path)


def validate(name, path):
    im = Image.open(path)
    frames = [frame.convert('RGBA') for frame in ImageSequence.Iterator(im)]
    durations = []
    for index in range(im.n_frames):
        im.seek(index)
        durations.append(im.info['duration'])
    # Measure the decoded GIF, not the pre-encoding buffers.
    unique = len({frame.tobytes() for frame in frames})
    bounds = [frame.getbbox() for frame in frames]
    edge_pixels = sum(np.count_nonzero(np.concatenate((np.array(f)[0, :, 3], np.array(f)[-1, :, 3],
                                                       np.array(f)[:, 0, 3], np.array(f)[:, -1, 3]))) for f in frames)
    assert im.size == SIZE and im.n_frames == COUNT, (name, im.size, im.n_frames)
    assert set(durations) == {DURATION} and unique >= 36, (name, durations, unique)
    assert edge_pixels == 0 and all(bounds), (name, edge_pixels)
    # Loop seam should be as gentle as an ordinary interpolation step.
    arrays = [np.array(f).astype(np.float32) for f in frames]
    deltas = [float(np.mean(abs(a-b))) for a,b in zip(arrays, arrays[1:])]
    seam = float(np.mean(abs(arrays[-1]-arrays[0])))
    assert seam <= max(deltas)*1.5, (name, seam, max(deltas))
    return dict(state=name, label=EXPRESSIONS[name], size=list(SIZE), frames=im.n_frames,
                unique_frames=unique, frame_duration_ms=DURATION, fps=1000/DURATION,
                loop_duration_ms=sum(durations), edge_pixels=int(edge_pixels),
                max_step_delta=round(max(deltas), 3), loop_seam_delta=round(seam, 3),
                bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), ok=True)


def gallery(animations):
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    except OSError:
        font = ImageFont.load_default()
    still = Image.new('RGB', (192*5, 240*3), '#f5f2ea')
    draw = ImageDraw.Draw(still)
    for i, name in enumerate(EXPRESSIONS):
        x, y = (i % 5)*192, (i//5)*240
        still.paste(animations[name][0], (x, y), animations[name][0])
        draw.text((x+96, y+212), EXPRESSIONS[name], font=font, fill='#335d56', anchor='mt')
    still.save(OUT / 'liya-fifteen-states.building.png')
    (OUT / 'liya-fifteen-states.building.png').replace(OUT / 'liya-fifteen-states.png')
    for names, columns, filename in [(OLD, 3, 'liya-nine-states.gif'), (NEW, 3, 'liya-new-emotions.gif')]:
        frames = []
        for index in range(COUNT):
            frame = Image.new('RGBA', (192*columns, 208*((len(names)+columns-1)//columns)))
            for i, name in enumerate(names):
                frame.alpha_composite(animations[name][index], ((i % columns)*192, (i//columns)*208))
            frames.append(frame)
        save_gif(frames, OUT / filename)
    cards = ''.join(f'<figure><picture><source media="(prefers-reduced-motion: reduce)" srcset="{name}.png">'
                    f'<img src="{name}.gif" width="192" height="208" alt="{label}" data-state="{name}">'
                    f'</picture><figcaption>{label} <small>{name}</small></figcaption></figure>'
                    for name, label in EXPRESSIONS.items())
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>璃芽 · 表情动画预览</title><style>
body{font-family:system-ui,sans-serif;background:#f5f2ea;color:#274c45;margin:32px auto;max-width:1100px;padding:0 20px}
h1{font-size:26px}p{line-height:1.6}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(192px,1fr));gap:16px}
figure{margin:0;background:#fffaf2;border-radius:16px;text-align:center;padding:12px 0}small{display:block;color:#68817b}
button{padding:10px 20px;margin:0 0 24px;border:1px solid #64877c;border-radius:8px;background:white;color:#274c45;cursor:pointer}
img{max-width:100%;object-fit:contain}figcaption{padding-top:8px}</style>
<h1>璃芽 · 15 种表情</h1><p>每种 48 帧，20 FPS，2.4 秒循环。系统开启减少动态效果时，自动显示静态图。</p>
<button type="button" aria-pressed="false" id="toggle">暂停动画</button><main>''' + cards + '''</main>
<script>let paused=matchMedia('(prefers-reduced-motion: reduce)').matches;
const button=document.querySelector('#toggle');
function update(){document.querySelectorAll('picture').forEach(p=>{p.querySelector('source').srcset=p.querySelector('img').dataset.state+'.png';
p.querySelector('source').media=paused?'all':'not all';});button.textContent=paused?'播放动画':'暂停动画';button.setAttribute('aria-pressed',String(paused));}
button.onclick=()=>{paused=!paused;update()};matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',e=>{paused=e.matches;update()});update();</script></html>'''
    (OUT / 'preview.html').write_text(html, encoding='utf-8')


def main():
    if '--archive-legacy' not in sys.argv:
        raise SystemExit('Legacy artwork is stopped: ambiguous/double tail anatomy. '
                         'Use the single-tail v3 pipeline. '
                         '--archive-legacy is only for reproducing the rejected draft.')
    cv2.setNumThreads(1)
    poses = keyposes()
    animations, checks = {}, []
    for name in EXPRESSIONS:
        frames = animate(name, poses[name])
        animations[name] = frames
        save_gif(frames, OUT / f'{name}.gif')
        staged_png = OUT / f'{name}.building.png'
        frames[0].save(staged_png)
        staged_png.replace(OUT / f'{name}.png')
        checks.append(validate(name, OUT / f'{name}.gif'))
        for suffix in ('.gif', '.png'):
            target = RUNTIME / (name+suffix)
            staged = target.with_suffix('.building'+suffix)
            shutil.copy2(OUT / (name+suffix), staged)
            staged.replace(target)
            assert target.read_bytes() == (OUT / (name+suffix)).read_bytes()
        print(f'{name}: {checks[-1]["unique_frames"]} unique / {COUNT}, {checks[-1]["bytes"]} bytes', flush=True)
    gallery(animations)
    staged = RUNTIME / 'liya-nine-states.building.gif'
    shutil.copy2(OUT / 'liya-nine-states.gif', staged)
    staged.replace(RUNTIME / 'liya-nine-states.gif')
    report = dict(animation_revision=2, method='four generated key poses + bidirectional DIS dense optical-flow interpolation; cosine easing',
                  runtime_assets_match=True, checks=checks,
                  scope='Local decoded GIF validation; QQ client playback cadence requires a client check.')
    (ROOT / 'validation/animation-v2-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
