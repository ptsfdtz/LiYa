"""Build and replace all 15 Liya expressions from the approved single-tail art.

Artwork comes from ImageGen, one identity-preserving source per expression.
Animation is continuous deformation of that source, without pose crossfades.
Run: .venv-artwork/Scripts/python.exe scripts/build_single_tail_set.py
"""
from pathlib import Path
import hashlib
import json
import math
import shutil
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageSequence

from build_animations import save_gif

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'src/liya_companion'))
from state import EXPRESSIONS

SOURCE = ROOT / 'assets/single-tail-v3'
OUT = ROOT / 'assets/expressions'
RUNTIME = ROOT / 'src/liya_companion/assets'
ARCHIVE = ROOT / 'assets/animation-sources/previous-expression-set'
SIZE = (384,416)
DELIVERY_SIZE = (192,208)
COUNT = 60

# All oscillation frequencies are whole cycles in the three-second loop.
# Tail amplitudes are preview pixels, independent of original image resolution.
MOTIONS = {
    'idle': dict(tail=11,cycles=1,breath=.003,tilt=0,label='轻呼吸，尾尖缓慢摆动'),
    'happy': dict(tail=15,cycles=2,breath=.004,tilt=.7,bounce=2,label='轻轻晃身，尾巴欢快轻摆'),
    'curious': dict(tail=9,cycles=1,breath=.003,tilt=1.0,lift=-2,label='微微歪身，尾尖轻抬'),
    'thinking': dict(tail=6,cycles=1,breath=.0025,tilt=.4,label='托腮沉思，小幅缓慢摆尾'),
    'sulky': dict(tail=8,cycles=1,breath=.003,tilt=.8,label='抱臂轻晃，尾尖短幅摆动'),
    'caring': dict(tail=7,cycles=1,breath=.004,tilt=.3,nod=.004,label='温柔点头，尾巴轻缓跟随'),
    'greeting': dict(tail=12,cycles=2,breath=.003,tilt=.5,wave=True,label='轻挥手，尾巴同步欢迎'),
    'sleepy': dict(tail=4,cycles=1,breath=.005,tilt=.6,nod=.006,label='缓缓低头，尾巴放松轻摆'),
    'excited': dict(tail=17,cycles=2,breath=.004,tilt=.8,jump=12,label='小幅跳起，尾尖稍后跟随回落'),
    'shy': dict(tail=7,cycles=1,breath=.003,tilt=.5,nod=.005,label='含羞轻点头，尾尖含蓄轻摆'),
    'surprised': dict(tail=15,cycles=1,breath=.003,tilt=.4,startle=3,label='小幅一惊后恢复，尾巴随动'),
    'worried': dict(tail=7,cycles=1,breath=.003,tilt=.6,label='担心轻晃，尾巴低幅摆动'),
    'sad': dict(tail=4,cycles=1,breath=.002,tilt=.3,lift=4,label='安静低头，尾尖低垂轻摆'),
    'relieved': dict(tail=7,cycles=1,breath=.005,tilt=.3,label='放松呼吸，尾巴舒缓摆动'),
    'determined': dict(tail=10,cycles=1,breath=.003,tilt=.4,nod=.004,label='认真点头，尾巴稳稳跟随'),
}


def smoothstep(value):
    v = np.clip(value,0,1)
    return v*v*(3-2*v)


def load_pose(name):
    with Image.open(SOURCE / f'{name}-reference.png') as image:
        source = image.convert('RGBA')
    data = np.array(source)
    count,labels,stats,_ = cv2.connectedComponentsWithStats((data[:,:,3]>160).astype(np.uint8))
    if count < 2:
        raise ValueError(f'{name}: no opaque character')
    x,y,w,h,area = stats[1+np.argmax(stats[1:,cv2.CC_STAT_AREA])]
    assert area > 10000 and x>0 and y>0 and x+w<source.width and y+h<source.height, (name,stats)
    # Use the actual opaque character, not the image canvas or an alpha veil.
    # Keep only components large enough to belong to the source drawing.
    keep = np.isin(labels,np.flatnonzero(stats[:,cv2.CC_STAT_AREA]>=80)) & (labels!=0)
    keep = cv2.dilate(keep.astype(np.uint8),np.ones((5,5),np.uint8))
    data[:,:,3] *= keep
    source = Image.fromarray(data)
    bounds = (int(x)-4,int(y)-4,int(x+w)+4,int(y+h)+4)
    crop = source.crop(bounds)
    scale = min((SIZE[0]-56)/crop.width,(SIZE[1]-40)/crop.height)
    resized = crop.resize((round(crop.width*scale),round(crop.height*scale)),Image.Resampling.LANCZOS)
    left,top = (SIZE[0]-resized.width)//2,SIZE[1]-20-resized.height
    frame = Image.new('RGBA',SIZE)
    frame.alpha_composite(resized,(left,top))
    mask = np.array(frame)[:,:,3]>160
    ys,xs = np.where(mask)
    top_y,bottom_y = int(ys.min()),int(ys.max())+1
    height = bottom_y-top_y
    # All approved source poses are standing. The lowest 3% contains shoes,
    # giving a body axis independent of the right tail and raised hands.
    shoe_y,shoe_x = np.where(mask & (np.indices(mask.shape)[0]>top_y+.97*height))
    body_x = float((shoe_x.min()+shoe_x.max())/2)
    root_x = body_x+.105*height
    lower_y,lower_x = np.where(mask & (np.indices(mask.shape)[0]>top_y+.70*height))
    outer_x = float(lower_x.max())
    assert outer_x-root_x > 22, (name,body_x,root_x,outer_x)
    return frame,dict(body_x=body_x,feet_y=bottom_y,top_y=top_y,height=height,
                      root_x=root_x,outer_x=outer_x,gate_y=top_y+.70*height,
                      source_bounds=list(map(int,bounds)))


def animate_frame(frame,rig,motion,index):
    phase = 2*math.pi*index/COUNT
    pulse = max(0,math.sin(phase))**2
    scale_y = 1+motion['breath']*math.sin(phase)-motion.get('nod',0)*pulse
    tilt = math.radians(motion['tilt'])*math.sin(phase)
    jump = -motion.get('jump',0)*pulse
    bounce = -motion.get('bounce',0)*(math.sin(phase)**2)
    startle = -motion.get('startle',0)*pulse*pulse
    cx,cy = rig['body_x'],rig['feet_y']
    c,s = math.cos(tilt),math.sin(tilt)
    linear = np.array([[c,-s*scale_y],[s,c*scale_y]],dtype=np.float32)
    translate = np.array([cx,cy+jump+bounce+startle],dtype=np.float32)-linear@np.array([cx,cy],dtype=np.float32)
    inverse = cv2.invertAffineTransform(np.column_stack((linear,translate)))
    yy,xx = np.mgrid[:SIZE[1],:SIZE[0]].astype(np.float32)
    target_x = inverse[0,0]*xx+inverse[0,1]*yy+inverse[0,2]
    target_y = inverse[1,0]*xx+inverse[1,1]*yy+inverse[1,2]

    def local_displacement(x,y):
        progress = np.clip((x-rig['root_x'])/(rig['outer_x']-rig['root_x']),0,1)
        below = smoothstep((y-rig['gate_y'])/(.045*rig['height']))
        weight = progress**1.7*below
        lag = 1.1*progress
        tail_phase = phase*motion['cycles']
        dx = .32*motion['tail']*weight*np.sin(tail_phase-lag+.6)
        dy = weight*(motion['tail']*np.sin(tail_phase-lag)+motion.get('lift',0))
        if motion.get('wave'):
            # Rotate the existing raised right hand/forearm with a soft local
            # mesh weight; the shoulder is stationary in body coordinates.
            height = rig['height']
            sx,sy = rig['body_x']+.08*height,rig['top_y']+.54*height
            hx,hy = rig['body_x']+.20*height,rig['top_y']+.43*height
            radius = ((x-hx)/(.14*height))**2+((y-hy)/(.16*height))**2
            wave_weight = np.exp(-radius*2.5)
            angle = .075*math.sin(phase*2)
            rx,ry = x-sx,y-sy
            dx += wave_weight*((math.cos(angle)-1)*rx-math.sin(angle)*ry)
            dy += wave_weight*(math.sin(angle)*rx+(math.cos(angle)-1)*ry)
        return dx,dy

    mapx,mapy = target_x.copy(),target_y.copy()
    for _ in range(6):
        dx,dy = local_displacement(mapx,mapy)
        mapx,mapy = (target_x-dx).astype(np.float32),(target_y-dy).astype(np.float32)
    rgba = np.array(frame).astype(np.float32)/255
    rgba[:,:,:3] *= rgba[:,:,3:]
    moved = np.clip(cv2.remap(rgba,mapx,mapy,cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT),0,1)
    moved[:,:,:3] /= np.maximum(moved[:,:,3:],1/255)
    return Image.fromarray(np.clip(moved*255,0,255).astype(np.uint8))


def check_gif(path,name,rig):
    with Image.open(path) as image:
        decoded = [im.convert('RGBA') for im in ImageSequence.Iterator(image)]
        intervals = []
        for i in range(image.n_frames):
            image.seek(i)
            intervals.append(image.info['duration'])
    unique = len({im.tobytes() for im in decoded})
    assert len(decoded)==COUNT and unique>=54 and set(intervals)=={50}, (name,len(decoded),unique,intervals)
    for image in decoded:
        x0,y0,x1,y1 = image.getbbox()
        assert x0>0 and y0>0 and x1<DELIVERY_SIZE[0] and y1<DELIVERY_SIZE[1], (name,image.getbbox())
    arrays = [np.array(im).astype(np.float32) for im in decoded]
    steps = [float(np.mean(abs(a-b))) for a,b in zip(arrays,arrays[1:])]
    seam = float(np.mean(abs(arrays[-1]-arrays[0])))
    assert seam<=1.5*max(steps), (name,seam,max(steps))
    roi = (slice(round(rig['gate_y']/2),None),slice(round(rig['root_x']/2)+3,None))
    tail_difference = max(float(np.mean(abs(arrays[0][roi]-a[roi]))) for a in arrays)
    assert tail_difference>.5, (name,tail_difference)
    return decoded,dict(state=name,label=EXPRESSIONS[name],size=list(DELIVERY_SIZE),frames=COUNT,
                        unique_frames=unique,fps=20,frame_duration_ms=50,loop_duration_ms=sum(intervals),
                        max_step_delta=round(max(steps),4),loop_seam_delta=round(seam,4),
                        tail_region_difference=round(tail_difference,4),
                        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        bytes=path.stat().st_size,source= f'assets/single-tail-v3/{name}-reference.png',
                        source_sha256=hashlib.sha256((SOURCE / f'{name}-reference.png').read_bytes()).hexdigest(),
                        motion=MOTIONS[name],ok=True)


def font(size):
    try:
        return ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',size)
    except OSError:
        return ImageFont.load_default()


def make_gallery(animations):
    names = list(EXPRESSIONS)
    cell_w,cell_h = 128,160
    label_font = font(12)
    frames = []
    for index in range(COUNT):
        frame = Image.new('RGBA',(cell_w*5,cell_h*3),'#f5f2ea')
        draw = ImageDraw.Draw(frame)
        for i,name in enumerate(names):
            x,y = (i%5)*cell_w,(i//5)*cell_h
            sprite = animations[name][index].resize((128,139),Image.Resampling.LANCZOS)
            frame.alpha_composite(sprite,(x,y))
            draw.text((x+64,y+141),EXPRESSIONS[name],font=label_font,fill='#335d56',anchor='mt')
        frames.append(frame)
    save_gif(frames,OUT / 'liya-fifteen-states.gif')
    frames[0].convert('RGB').save(OUT / 'liya-fifteen-states.building.png')
    (OUT / 'liya-fifteen-states.building.png').replace(OUT / 'liya-fifteen-states.png')
    for subset,filename in [(names[:9],'liya-nine-states.gif'),(names[9:],'liya-new-emotions.gif')]:
        montage = []
        for index in range(COUNT):
            frame = Image.new('RGBA',(192*3,208*((len(subset)+2)//3)))
            for i,name in enumerate(subset):
                frame.alpha_composite(animations[name][index],((i%3)*192,(i//3)*208))
            montage.append(frame)
        save_gif(montage,OUT / filename)
    # Replace old two-tail contact sheets too, while preserving their sources
    # in the rejected-draft archive for historical reproducibility.
    atlas = Image.new('RGBA',(192*4,208*len(names)))
    for row,name in enumerate(names):
        for col,index in enumerate((0,15,30,45)):
            atlas.alpha_composite(animations[name][index],(col*192,row*208))
    for filename in ('liya-animation-atlas.png','animation-contact.png'):
        staged = OUT / filename.replace('.png','.building.png')
        atlas.save(staged)
        staged.replace(OUT / filename)
    cards = ''.join(f'<figure><picture><source media="(prefers-reduced-motion: reduce)" srcset="{name}.png">'
                    f'<img src="{name}.gif" width="192" height="208" alt="{label}" data-state="{name}"></picture>'
                    f'<figcaption><strong>{label}</strong><small>{MOTIONS[name]["label"]}</small></figcaption></figure>'
                    for name,label in EXPRESSIONS.items())
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>璃芽 · 全部单尾表情动画</title><style>
body{font:16px system-ui,sans-serif;background:#f5f2ea;color:#274c45;max-width:1100px;margin:32px auto;padding:0 20px}
h1{font-size:26px}p{line-height:1.7}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(192px,1fr));gap:16px}
figure{margin:0;border-radius:16px;background:#fffaf2;text-align:center;padding:12px 0}img{max-width:100%;height:auto}
figcaption{padding:8px 12px}small{display:block;line-height:1.6;color:#68817b;margin-top:6px;font-size:13px}
button{font:inherit;padding:10px 20px;margin-bottom:24px;border:1px solid #64877c;border-radius:8px;background:white;color:inherit;cursor:pointer}
</style><h1>璃芽 · 15 种单尾表情</h1><p>全部采用原造型的低位单尾。每种60帧、20 FPS、3秒循环；身体与尾巴随情绪变化一起动。</p>
<button id="toggle" type="button" aria-pressed="false">暂停全部动画</button><main>''' + cards + '''</main>
<script>const media=matchMedia('(prefers-reduced-motion: reduce)');let paused=media.matches;
const button=document.querySelector('#toggle');function update(){document.querySelectorAll('picture').forEach(p=>{p.querySelector('source').media=paused?'all':'not all'});
button.textContent=paused?'播放全部动画':'暂停全部动画';button.setAttribute('aria-pressed',String(paused))}
button.onclick=()=>{paused=!paused;update()};media.addEventListener('change',e=>{paused=e.matches;update()});update();</script></html>'''
    (OUT / 'preview.html').write_text(html,encoding='utf-8')
    (SOURCE / 'preview.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="refresh" content="0;url=../expressions/preview.html"><title>璃芽 · 全部单尾表情</title>
<p><a href="../expressions/preview.html">打开全部15种单尾表情动画</a></p></html>''',encoding='utf-8')
    return frames


def archive_prior_assets():
    ARCHIVE.mkdir(parents=True,exist_ok=True)
    for path in OUT.iterdir():
        if path.suffix not in ('.gif','.png','.html') or '.building.' in path.name:
            continue
        target = ARCHIVE / path.name
        if not target.exists():
            shutil.copy2(path,target)


def main():
    raise SystemExit('This v3 single-image deformation approach is retired by user direction. '
                     'Use the drawn-frame pet-style v4 pipeline; do not replace active assets with v3.')
    cv2.setNumThreads(1)
    missing = [name for name in EXPRESSIONS if not (SOURCE / f'{name}-reference.png').is_file()]
    if missing:
        raise SystemExit('Missing generated references: '+', '.join(missing))
    archive_prior_assets()
    animations,checks = {},[]
    for name in EXPRESSIONS:
        pose,rig = load_pose(name)
        full_frames = [animate_frame(pose,rig,MOTIONS[name],i) for i in range(COUNT)]
        frames = [im.resize(DELIVERY_SIZE,Image.Resampling.LANCZOS) for im in full_frames]
        save_gif(frames,OUT / f'{name}.gif')
        staged = OUT / f'{name}.building.png'
        pose.resize(DELIVERY_SIZE,Image.Resampling.LANCZOS).save(staged)
        staged.replace(OUT / f'{name}.png')
        decoded,check = check_gif(OUT / f'{name}.gif',name,rig)
        check['rig'] = rig
        checks.append(check)
        animations[name] = decoded
        for suffix in ('.gif','.png'):
            staged = RUNTIME / f'{name}.building{suffix}'
            shutil.copy2(OUT / f'{name}{suffix}',staged)
            staged.replace(RUNTIME / f'{name}{suffix}')
            assert (OUT / f'{name}{suffix}').read_bytes()==(RUNTIME / f'{name}{suffix}').read_bytes()
        if name=='idle':
            # Keep the previously shared large sample in sync with the full set.
            save_gif(full_frames,SOURCE / 'idle-tail-motion.gif')
            pose.save(SOURCE / 'idle-single-tail.png')
        print(f'{name}: {check["unique_frames"]} unique frames, tail motion checked, copied to both asset folders',flush=True)
    make_gallery(animations)
    for filename in ('liya-nine-states.gif','liya-new-emotions.gif','liya-fifteen-states.gif'):
        staged = RUNTIME / filename.replace('.gif','.building.gif')
        shutil.copy2(OUT / filename,staged)
        staged.replace(RUNTIME / filename)
        assert (OUT / filename).read_bytes()==(RUNTIME / filename).read_bytes()
    report = dict(revision=3,status='all_15_local_expressions_replaced',fps=20,frames_per_expression=COUNT,
                  count=len(checks),source_art='Built-in ImageGen identity-preserving emotion variants of approved single-tail reference',
                  animation='continuous local tail deformation, grounded body motion and emotion-specific gestures; no optical-flow pose interpolation',
                  tail_anatomy='one low right tail per source, visually checked; original root stays behind lower skirt',
                  source_and_runtime_assets_identical=True,prior_assets_archive='assets/animation-sources/previous-expression-set',
                  deployed=False,checks=checks)
    (ROOT / 'validation/single-tail-v3-full-set-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
