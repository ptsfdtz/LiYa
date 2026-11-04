"""Animate the corrected original chibi sprite without changing its tail root.

ImageGen supplies the locally corrected single-tail drawing. This script only
applies continuous animation transforms, with no pose morphing or crossfades.
Run with .venv-artwork/Scripts/python.exe scripts/make_tail_sample.py.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import shutil

import cv2
import numpy as np
from PIL import Image, ImageSequence

from build_animations import save_gif

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/single-tail-v3'
SIZE = (384, 416)
COUNT = 60


def smoothstep(value):
    value = np.clip(value, 0, 1)
    return value*value*(3-2*value)


def source_frame():
    source = Image.open(OUT / 'idle-reference.png').convert('RGBA')
    mask = (np.array(source)[:, :, 3] > 127).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    x,y,w,h,_ = stats[1+np.argmax(stats[1:, cv2.CC_STAT_AREA])]
    bounds = (int(x)-4, int(y)-4, int(x+w)+4, int(y+h)+4)
    crop = source.crop(bounds)
    scale = min((SIZE[0]-32)/crop.width, (SIZE[1]-32)/crop.height)
    resized = crop.resize((round(crop.width*scale), round(crop.height*scale)), Image.Resampling.LANCZOS)
    left,top = (SIZE[0]-resized.width)//2, SIZE[1]-16-resized.height
    frame = Image.new('RGBA', SIZE)
    frame.alpha_composite(resized, (left,top))
    # The retained original right tail is below the hands/skirt, with its
    # root naturally occluded behind the low skirt. Coordinates describe that
    # existing drawing; no new waist/back attachment is introduced.
    def point(px, py):
        return (left+(px-bounds[0])*scale, top+(py-bounds[1])*scale)
    root_x,root_y = point(790,1155)
    tip_x,_ = point(1048,1050)
    _,gate_y = point(0,905)
    return frame, dict(root_x=root_x, root_y=root_y, tip_x=tip_x,
                      gate_y=gate_y, scale=scale, feet_y=SIZE[1]-16)


def render(frame, rig, phase):
    yy,xx = np.mgrid[:SIZE[1], :SIZE[0]].astype(np.float32)
    scale = 1+0.003*math.sin(phase)
    def displacement(x,y):
        progress = np.clip((x-rig['root_x'])/(rig['tip_x']-rig['root_x']),0,1)
        below = smoothstep((y-rig['gate_y'])/(45*rig['scale']))
        # Motion grows along the tail. The root is exactly stationary in
        # body coordinates; the tip follows with a phase delay.
        weight = progress**1.7*below
        lag = 1.1*progress
        dx = 12*rig['scale']*weight*np.sin(phase-lag+0.6)
        dy = 36*rig['scale']*weight*np.sin(phase-lag)
        return dx,dy
    mapx,mapy = xx.copy(),yy.copy()
    for _ in range(6):
        dx,dy = displacement(mapx,mapy)
        mapx = (xx-dx).astype(np.float32)
        mapy = (rig['feet_y']+(yy-dy-rig['feet_y'])/scale).astype(np.float32)
    rgba = np.array(frame).astype(np.float32)/255
    rgba[:, :, :3] *= rgba[:, :, 3:]
    moved = np.clip(cv2.remap(rgba,mapx,mapy,cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT),0,1)
    moved[:, :, :3] /= np.maximum(moved[:, :, 3:],1/255)
    return Image.fromarray(np.clip(moved*255,0,255).astype(np.uint8))


def main():
    raise SystemExit('This single-image deformation sample is retired. Use the pet-style v4 drawn-frame workflow.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace-idle', action='store_true', help='replace the local design and runtime idle assets, retaining the rejected draft')
    args = parser.parse_args()
    frame,rig = source_frame()
    frame.save(OUT / 'idle-single-tail.png')
    frames = [render(frame,rig,2*math.pi*i/COUNT) for i in range(COUNT)]
    gif_path = OUT / 'idle-tail-motion.gif'
    save_gif(frames,gif_path)
    with Image.open(gif_path) as gif:
        decoded = [im.convert('RGBA') for im in ImageSequence.Iterator(gif)]
        intervals = []
        for i in range(gif.n_frames):
            gif.seek(i)
            intervals.append(gif.info['duration'])
    unique = len({im.tobytes() for im in decoded})
    assert len(decoded) == COUNT and unique >= 50
    assert set(intervals) == {50}
    for image in decoded:
        x0,y0,x1,y1 = image.getbbox()
        assert x0 > 0 and y0 > 0 and x1 < SIZE[0] and y1 < SIZE[1]
    arrays = [np.array(im).astype(np.float32) for im in decoded]
    steps = [float(np.mean(abs(a-b))) for a,b in zip(arrays,arrays[1:])]
    seam = float(np.mean(abs(arrays[-1]-arrays[0])))
    assert seam <= 1.5*max(steps)
    # Verify tail-region motion exists independently of the face region.
    roi = (slice(round(rig['gate_y']),None),slice(round(rig['root_x'])+5,None))
    assert max(float(np.mean(abs(arrays[0][roi]-a[roi]))) for a in arrays) > 1
    contact = Image.new('RGBA',(SIZE[0]*4,SIZE[1]),'#f5f2ea')
    for index,frame_index in enumerate((0,15,30,45)):
        contact.alpha_composite(decoded[frame_index],(index*SIZE[0],0))
    contact.convert('RGB').save(OUT / 'tail-motion-contact.png')
    installed = None
    if args.replace_idle:
        design = ROOT / 'assets/expressions'
        runtime = ROOT / 'src/liya_companion/assets'
        archive = ROOT / 'assets/animation-sources'
        for suffix in ('.gif','.png'):
            backup = archive / ('rejected-v2-idle'+suffix)
            if not backup.exists():
                shutil.copy2(design / ('idle'+suffix), backup)
        runtime_frames = [im.resize((192,208),Image.Resampling.LANCZOS) for im in frames]
        save_gif(runtime_frames,design / 'idle.gif')
        temporary_png = design / 'idle.building.png'
        frame_static = Image.open(OUT / 'idle-single-tail.png')
        frame_static.resize((192,208),Image.Resampling.LANCZOS).save(temporary_png)
        frame_static.close()
        temporary_png.replace(design / 'idle.png')
        for suffix in ('.gif','.png'):
            staged = runtime / ('idle.building'+suffix)
            shutil.copy2(design / ('idle'+suffix),staged)
            staged.replace(runtime / ('idle'+suffix))
            assert (design / ('idle'+suffix)).read_bytes() == (runtime / ('idle'+suffix)).read_bytes()
        with Image.open(design / 'idle.gif') as im:
            actual_frames = [f.convert('RGBA') for f in ImageSequence.Iterator(im)]
            assert im.size == (192,208) and len(actual_frames) == COUNT
            assert len({f.tobytes() for f in actual_frames}) >= 50
            for i in range(COUNT):
                im.seek(i)
                assert im.info['duration'] == 50
        installed = dict(paths=['assets/expressions/idle.gif','src/liya_companion/assets/idle.gif'],
                         size=[192,208],frames=COUNT,fps=20,
                         sha256=hashlib.sha256((design / 'idle.gif').read_bytes()).hexdigest(),
                         design_and_runtime_identical=True)
    report = dict(revision=3, status='corrected_original_pose_and_motion_sample',
                  tail_count_visually_checked=1, tail_removed='viewer-left extra appendage',
                  retained_tail='original low right tail behind lower skirt',
                  size=list(SIZE), frames=COUNT, unique_frames=unique, fps=20,
                  frame_duration_ms=50, loop_duration_ms=sum(intervals),
                  method='continuous local tail deformation with anchored root and delayed tip; grounded breathing',
                  max_step_delta=round(max(steps),4), loop_seam_delta=round(seam,4),
                  rig=rig, sha256=hashlib.sha256(gif_path.read_bytes()).hexdigest(),
                  local_idle_replacement=installed,
                  deployed=False, scope='corrected single-tail idle; full expression set is paused')
    (ROOT / 'validation/single-tail-v3-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>璃芽 · 原造型单尾修正</title><style>body{font:16px system-ui;background:#f5f2ea;color:#335d56;text-align:center;margin:24px}
img{max-width:100%;height:auto}button{font:inherit;padding:8px 20px;border:1px solid #67887d;border-radius:8px;background:white;color:inherit}
p{line-height:1.7}</style><h1>璃芽 · 原造型单尾修正</h1><p>删去左侧多余尾巴，保留右侧原低位弯尾。20 FPS，3秒循环。</p>
<picture><source media="(prefers-reduced-motion: reduce)" srcset="idle-single-tail.png"><img src="idle-tail-motion.gif" width="384" height="416" alt="单尾璃芽轻摆尾样片"></picture>
<p><button id="toggle" type="button">暂停动画</button></p><p>当前展示单尾待机动画，其余表情扩展暂停。</p>
<script>const media=matchMedia('(prefers-reduced-motion: reduce)');let paused=media.matches;
const source=document.querySelector('source'),button=document.querySelector('button');
function update(){source.media=paused?'all':'not all';button.textContent=paused?'播放动画':'暂停动画';button.setAttribute('aria-pressed',String(paused))}
button.onclick=()=>{paused=!paused;update()};media.addEventListener('change',e=>{paused=e.matches;update()});update();</script></html>'''
    (OUT / 'preview.html').write_text(html,encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
