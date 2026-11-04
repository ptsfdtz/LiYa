"""Encode every expression with the approved sleepy artwork's exact color table.

This is GIF palette quantization only: no geometric, pose, material or lighting
edits. Color-cast source repairs are separate image_gen edits with saved prompts.
"""
from pathlib import Path
import hashlib
import json

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=ROOT/'assets/pet-style-v4/qa/rows/sleepy/row-clean.png'
_CACHE={}


def master_palette(run_dir):
    run_dir=Path(run_dir)
    if str(run_dir) in _CACHE:
        return _CACHE[str(run_dir)]
    path=run_dir/'shared-palette.json'
    if path.exists():
        data=json.loads(path.read_text(encoding='utf-8'))
        if data['reference_sha256']!=hashlib.sha256(REFERENCE.read_bytes()).hexdigest():
            raise ValueError('Approved palette reference changed; review and rebuild the shared palette explicitly.')
        colors=data['rgb_palette']
    else:
        with Image.open(REFERENCE) as im:
            rgba=np.array(im.convert('RGBA'))
        visible=rgba[:,:,:3][rgba[:,:,3]>=128]
        # Exclude the transparent background from the palette training set.
        width=256
        height=(len(visible)+width-1)//width
        sample=np.full((height*width,3),[245,242,234],dtype=np.uint8)
        sample[:len(visible)]=visible
        image=Image.fromarray(sample.reshape(height,width,3))
        adaptive=image.quantize(colors=253,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
        # 0: transparency, 1..253: approved art, 254..255: gallery background/text.
        colors=[0,0,0]+adaptive.getpalette()[:759]+[245,242,234,51,93,86]
        assert len(colors)==768
        data=dict(reference=str(REFERENCE.relative_to(ROOT)).replace('\\','/'),
                  reference_sha256=hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
                  transparency_index=0,rgb_palette=colors,
                  palette_sha256=hashlib.sha256(bytes(colors)).hexdigest(),
                  quantization='one shared 255-color opaque palette, no dithering')
        run_dir.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    palette=Image.new('P',(1,1))
    palette.putpalette(colors)
    _CACHE[str(run_dir)]=palette
    return palette


def indexed_frame(frame,palette):
    rgba=np.array(frame.convert('RGBA'))
    indexed=Image.fromarray(rgba[:,:,:3]).quantize(palette=palette,dither=Image.Dither.NONE)
    indices=np.array(indexed)
    # Opaque outlines must never accidentally use the transparent black entry.
    reserved=(indices==0)&(rgba[:,:,3]>=128)
    if reserved.any():
        colors=np.array(palette.getpalette()[3:],dtype=np.int32).reshape(255,3)
        pixels=rgba[:,:,:3][reserved].astype(np.int32)
        # Bound temporary memory on large montage frames.
        selected=[]
        for start in range(0,len(pixels),2048):
            delta=pixels[start:start+2048,None,:]-colors[None,:,:]
            selected.append(1+np.argmin(np.sum(delta*delta,axis=2),axis=1))
        indices[reserved]=np.concatenate(selected)
    indices[rgba[:,:,3]<128]=0
    result=Image.fromarray(indices).convert('P')
    result.putpalette(palette.getpalette())
    result.info['transparency']=0
    return result


def save_preview(frames,durations,output,run_dir):
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    palette=master_palette(run_dir)
    indexed=[indexed_frame(frame,palette) for frame in frames]
    staged=output.with_name(output.stem+'.building.gif')
    indexed[0].save(staged,save_all=True,append_images=indexed[1:],duration=durations,
                    loop=0,disposal=2,transparency=0,optimize=False,
                    palette=bytes(palette.getpalette()))
    staged.replace(output)
