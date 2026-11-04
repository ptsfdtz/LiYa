"""Assemble approved drawn strips with the bundled pet scripts, then install QQ assets."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageSequence

from qq_pet_frames import ROOT,RUN,COUNTS,DURATIONS,bundled
from shared_gif_palette import master_palette,indexed_frame,save_preview
from pose_inbetweens import loop_timing,smooth_loop
import sys
sys.path.insert(0,str(ROOT/'src/liya_companion'))
from state import EXPRESSIONS

OUT = ROOT/'assets/expressions'
RUNTIME = ROOT/'src/liya_companion/assets'
FINAL = RUN/'final'
MOTION_STRETCH = 1.5
STATIC_FRAME = {state:2 for state in EXPRESSIONS}
STATIC_FRAME.update(idle=0,sleepy=0,happy=3,greeting=3,excited=3,relieved=3)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_atomic(source,target):
    staged = target.with_name(target.stem+'.building'+target.suffix)
    shutil.copy2(source,staged)
    staged.replace(target)
    assert sha(source)==sha(target)


def save_png_atomic(image,target):
    staged = target.with_suffix('.building.png')
    image.save(staged)
    staged.replace(target)


def contact_sheet(atlas,names,offset,title):
    # The bundled contact renderer supports its 9/11-row templates. Use two
    # nine-row presentation pages for QQ's 15 states, with unused slots blank.
    page = Image.new('RGBA',(1536,1872))
    count = min(9,len(names)-offset)
    for row in range(count):
        page.alpha_composite(atlas.crop((0,(offset+row)*208,1536,(offset+row+1)*208)),(0,row*208))
    source = FINAL/f'contact-source-{title}.png'
    page.save(source)
    bundled('make_contact_sheet',[source,'--output',FINAL/f'contact-{title}.png','--scale','0.5'],
            {'ROW_NAMES':names[offset:offset+count]+['unused']*(9-count),'USED_COUNTS':[8]*count+[0]*(9-count)})


def montage(name,states,animations,columns=5,labels=True):
    folder = FINAL/'gallery-frames'/name
    folder.mkdir(parents=True,exist_ok=True)
    width,height = (160,192) if labels else (192,208)
    rows = (len(states)+columns-1)//columns
    try:
        font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',13)
    except OSError:
        font=ImageFont.load_default()
    gallery_timing = [40] * round(26 * MOTION_STRETCH)
    for index in range(len(gallery_timing)):
        image=Image.new('RGBA',(width*columns,height*rows),'#f5f2ea' if labels else (0,0,0,0))
        draw=ImageDraw.Draw(image)
        for i,state in enumerate(states):
            frames, delays = animations[state]
            timestamp = (index + 0.5) * sum(delays) / len(gallery_timing)
            end = 0
            for sprite, delay in zip(frames, delays, strict=True):
                end += delay
                if timestamp < end:
                    break
            if labels:
                sprite=sprite.resize((160,173),Image.Resampling.LANCZOS)
            x,y=(i%columns)*width,(i//columns)*height
            image.alpha_composite(sprite,(x,y))
            if labels:
                draw.text((x+width/2,y+175),EXPRESSIONS[state],font=font,fill='#335d56',anchor='mt')
        save_png_atomic(image,folder/f'{index:02d}.png')
    bundled('render_animation_previews',['--frames-root',folder.parent,'--output-dir',FINAL/'previews'],
            {'ROW_DURATIONS':{name:gallery_timing}})
    return folder


def preview_html():
    cards=''.join(f'<figure><picture><source media="(prefers-reduced-motion: reduce)" srcset="{s}.png">'
                  f'<img src="{s}.gif" width="192" height="208" alt="{label}"></picture>'
                  f'<figcaption>{label}<small>8个原画动作帧与过渡补帧</small></figcaption></figure>' for s,label in EXPRESSIONS.items())
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>璃芽 · 原版画风逐帧表情</title><style>body{font:16px system-ui;background:#f5f2ea;color:#335d56;max-width:1100px;margin:28px auto;padding:0 20px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(192px,1fr));gap:16px}figure{margin:0;text-align:center;background:#fffaf2;border-radius:16px;padding:12px 0}
img{max-width:100%;height:auto}p{line-height:1.7}small{display:block;color:#68817b;margin-top:6px;font-size:12px}
button{font:inherit;padding:10px 18px;margin-bottom:20px;background:white;color:inherit;border:1px solid #68817b;border-radius:8px}
</style><h1>璃芽 · 原版画风的15种逐帧表情</h1><p>统一采用原版抱尾图的配色，保留原版圆润小体型。各状态使用实际动作帧与自然停顿。</p>
<button id="toggle" type="button" aria-pressed="false">暂停全部动画</button><main>'''+cards+'''</main>
<script>const m=matchMedia('(prefers-reduced-motion: reduce)');let p=m.matches;const b=document.querySelector('button');
function update(){document.querySelectorAll('source').forEach(s=>s.media=p?'all':'not all');b.textContent=p?'播放全部动画':'暂停全部动画';b.setAttribute('aria-pressed',String(p))}
b.onclick=()=>{p=!p;update()};m.addEventListener('change',e=>{p=e.matches;update()});update();</script></html>'''
    (OUT/'preview.html').write_text(html,encoding='utf-8')
    (RUN/'preview.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=../expressions/preview.html">
<a href="../expressions/preview.html">打开全部15种逐帧表情</a></html>''',encoding='utf-8')


def main():
    names=list(EXPRESSIONS)
    missing=[s for s in names if s!='sleepy' and not (RUN/'prompts'/f'{s}.txt').is_file()]
    if missing:
        raise SystemExit('Missing selected color-correction prompts: '+', '.join(missing))
    FINAL.mkdir(parents=True,exist_ok=True)
    frames_root=FINAL/'frames'
    manifest_rows=[]
    for state in names:
        qa=RUN/'qa/rows'/state
        result=json.loads((qa/'result.json').read_text(encoding='utf-8'))
        review=json.loads((qa/'review.json').read_text(encoding='utf-8'))
        assert result['ok'] and review['ok'] and not review['warnings'],state
        assert result['unique_frames']>=6,state
        target=frames_root/state
        target.mkdir(parents=True,exist_ok=True)
        for i in range(8):
            shutil.copy2(qa/'frames'/state/f'{i:02d}.png',target/f'{i:02d}.png')
        manifest_rows.append({'state':state,'method':'components','frames':[str(target/f'{i:02d}.png') for i in range(8)]})
    (frames_root/'frames-manifest.json').write_text(json.dumps({'ok':True,'chroma_key':{'hex':'#FF00FF'},'rows':manifest_rows},indent=2)+'\n',encoding='utf-8')
    bundled('inspect_frames',['--frames-root',frames_root,'--json-out',FINAL/'frame-review.json','--states','all','--require-components'],
            {'ROW_FRAME_COUNTS':COUNTS})
    atlas_path=FINAL/'spritesheet-qq.png'
    bundled('compose_atlas',['--frames-root',frames_root,'--output',atlas_path],
            {'ROW_SPECS':[(s,i,8) for i,s in enumerate(names)],'ROWS':15,'ATLAS_HEIGHT':3120,'ATLAS_ASPECT_RATIO':1536/3120})
    # Quantize the exact atlas to the approved master colors, then derive both
    # static PNGs and GIFs from that same encoded color table.
    with Image.open(atlas_path) as image:
        indexed_atlas=indexed_frame(image,master_palette(RUN))
    indexed_atlas.save(atlas_path,transparency=0)
    # Final previews are rendered from the exact encoded atlas, so they cannot
    # silently use an unrelated source or an earlier strip revision.
    frames={}
    with Image.open(atlas_path) as opened:
        atlas=opened.convert('RGBA')
    for row,state in enumerate(names):
        frames[state]=[]
        for i in range(8):
            cell=atlas.crop((i*192,row*208,(i+1)*192,(row+1)*208))
            save_png_atomic(cell,frames_root/state/f'{i:02d}.png')
            frames[state].append(cell)
    timings = {state: list(loop_timing(DURATIONS[state], MOTION_STRETCH)) for state in names}
    animations = {state: smooth_loop(frames[state], timings[state]) for state in names}
    for state, (poses, delays) in animations.items():
        save_preview(poses, delays, FINAL/'previews'/f'{state}.gif', RUN)
    contact_sheet(atlas,names,0,'first-nine')
    contact_sheet(atlas,names,9,'last-six')
    gallery=montage('liya-fifteen-states',names,animations)
    montage('liya-nine-states',names[:9],animations,columns=3,labels=False)
    montage('liya-new-emotions',names[9:],animations,columns=3,labels=False)
    transition_frames = animations['idle'][0] + animations['excited'][0] + animations['idle'][0]
    transition_delays = animations['idle'][1] + animations['excited'][1] + animations['idle'][1]
    save_preview(transition_frames, transition_delays, FINAL/'previews/idle-excited-idle.gif', RUN)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-i',str(FINAL/'previews/liya-fifteen-states.gif'),
                    '-c:v','libx264','-pix_fmt','yuv420p','-crf','18','-movflags','+faststart',
                    str(FINAL/'previews/liya-fifteen-states.mp4')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    archive=ROOT/'assets/animation-sources/before-color-unification-v4'
    archive.mkdir(parents=True,exist_ok=True)
    for path in OUT.iterdir():
        if path.suffix in ('.gif','.png','.html') and '.building.' not in path.name and not (archive/path.name).exists():
            shutil.copy2(path,archive/path.name)
    checks=[]
    for state in names:
        gif_path=FINAL/'previews'/f'{state}.gif'
        with Image.open(gif_path) as gif:
            decoded = []
            delays = []
            for frame in ImageSequence.Iterator(gif):
                decoded.append(frame.convert('RGBA'))
                delays.append(frame.info['duration'])
        timing = timings[state]
        assert sum(delays) == 10 * sum(duration for duration, _, _ in timing), state
        original_count = sum(count for _, _, count in loop_timing(DURATIONS[state]))
        original_fps = 1000 * original_count / sum(DURATIONS[state])
        output_fps = 1000 * len(decoded) / sum(delays)
        assert abs(output_fps / original_fps - 1) < 0.005, state
        assert len({f.tobytes() for f in decoded}) > COUNTS[state], state
        elapsed = 0
        pose_start = 0
        frame_index = 0
        palette = master_palette(RUN)
        for pose, (duration, _, _) in zip(frames[state], timing, strict=True):
            while elapsed + delays[frame_index] <= pose_start:
                elapsed += delays[frame_index]
                frame_index += 1
            assert decoded[frame_index].tobytes() == indexed_frame(pose, palette).convert('RGBA').tobytes(), state
            pose_start += 10 * duration
        qa=RUN/'qa/rows'/state
        result=json.loads((qa/'result.json').read_text(encoding='utf-8'))
        checks.append({**result,'output_frames':len(decoded),'output_durations_ms':delays,
                       'original_pose_pixels_and_order_preserved':True,
                       'original_average_fps':round(original_fps, 3),'output_average_fps':round(output_fps, 3),
                       'sha256':sha(gif_path),'source_sha256':sha(RUN/'decoded'/f'{state}.png'),
                       'design_and_runtime_identical':True})
    for state in names:
        for suffix, source in [('.gif', FINAL/'previews'/f'{state}.gif'),
                               ('.png', frames_root/state/f'{STATIC_FRAME[state]:02d}.png')]:
            copy_atomic(source, OUT/(state+suffix))
            copy_atomic(source, RUNTIME/(state+suffix))
    for name in ('liya-fifteen-states','liya-nine-states','liya-new-emotions'):
        copy_atomic(FINAL/'previews'/(name+'.gif'),OUT/(name+'.gif'))
        copy_atomic(FINAL/'previews'/(name+'.gif'),RUNTIME/(name+'.gif'))
    copy_atomic(gallery/'00.png',OUT/'liya-fifteen-states.png')
    copy_atomic(FINAL/'previews/liya-fifteen-states.mp4',OUT/'liya-fifteen-states.mp4')
    copy_atomic(atlas_path,OUT/'liya-animation-atlas.png')
    copy_atomic(atlas_path,OUT/'animation-contact.png')
    preview_html()
    jump_bottoms=[f.getbbox()[3] for f in frames['excited']]
    assert max(jump_bottoms)-min(jump_bottoms)>=8,jump_bottoms
    palette_record=json.loads((RUN/'shared-palette.json').read_text(encoding='utf-8'))
    report=dict(revision=5,status='all_15_drawn_frame_expressions_with_unified_colors',count=15,
                reference='assets/pet-style-v4/reference-sleepy.png',
                method='color-only image_gen corrections; original drawn poses retained; bundled sprite pipeline plus one common approved-reference GIF palette',
                shared_palette_sha256=palette_record['palette_sha256'],
                export_interpolation='Motion-compensated inbetweens stretch the original action by about 50% while retaining the drawings, palette, and average frame cadence.',
                source_drawings_modified=False,atlas_size=list(atlas.size),atlas_sha256=sha(atlas_path),
                qq_custom_format=True,chatgpt_pet_created=False,deployed=False,
                jump_bottoms=jump_bottoms,checks=checks,
                validation_scope='actual bundled row reports plus final encoded QQ atlas/GIF decoding; no Pets-v2 validation claimed')
    (ROOT/'validation/pet-style-v5-color-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'states':len(checks),'atlas':str(atlas_path),'deployed':False},indent=2))


if __name__=='__main__':
    main()
