"""Use the bundled pet strip pipeline for QQ's custom expression names.

This is a QQ artwork adapter, not a ChatGPT Pets v2 upload/validation tool.
It runs the bundled extraction, composition, despill, inspection and preview
entry points. Only names/counts/timing are adapted; pixels are not animated,
warped, rotated, interpolated or redrawn by this script.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

from PIL import Image, ImageSequence
from shared_gif_palette import save_preview as save_shared_preview

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'assets/pet-style-v5-colors'
SKILL = Path.home()/'.codex/plugins/cache/openai-curated-remote/work-pets/0.1.6/skills/create-pet/scripts'
sys.path.insert(0,str(ROOT/'src/liya_companion'))
from state import EXPRESSIONS

COUNTS = {state:8 for state in EXPRESSIONS}
DURATIONS = {state:[200,100,100,100,100,100,120,220] for state in EXPRESSIONS}
DURATIONS['sleepy'] = [300,120,100,100,120,140,160,260]
DURATIONS['greeting'] = [140,90,80,80,80,90,120,240]
DURATIONS['excited'] = [140,90,80,80,80,90,120,240]


def bundled(name,arguments,overrides=None):
    path = SKILL/(name+'.py')
    spec = importlib.util.spec_from_file_location('pet_'+name,path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key,value in (overrides or {}).items():
        setattr(module,key,value)
    if name=='render_animation_previews':
        # Retain the bundled renderer's loading, counts, timing and loop logic;
        # use one reference color table for the final GIF encoding stage.
        module.save_preview=lambda frames,durations,output: save_shared_preview(frames,durations,output,RUN)
    before = sys.argv
    sys.argv = [str(path),*map(str,arguments)]
    try:
        module.main()
    except SystemExit as exc:
        if exc.code not in (0,None):
            raise
    finally:
        sys.argv = before


def process(state):
    qa = RUN/'qa/rows'/state
    frames = qa/'frames'
    frames.mkdir(parents=True,exist_ok=True)
    counts = {state:COUNTS[state]}
    bundled('extract_strip_frames',['--decoded-dir',RUN/'decoded','--output-dir',frames,
                                   '--states',state,'--chroma-key','#FF00FF','--method','components'],
            {'ROW_FRAME_COUNTS':counts})
    raw = qa/'row-raw.png'
    clean = qa/'row-clean.png'
    bundled('compose_atlas',['--frames-root',frames,'--output',raw],
            {'ROW_SPECS':[(state,0,COUNTS[state])],'ROWS':1,'ATLAS_HEIGHT':208,'ATLAS_ASPECT_RATIO':1536/208})
    bundled('despill_chroma_edges',[raw,'--output',clean,'--json-out',qa/'despill.json','--chroma-key','#FF00FF'])
    # Exact cells are safe here: the bundled compositor created this geometry.
    with Image.open(clean) as row:
        for index in range(COUNTS[state]):
            row.crop((index*192,0,(index+1)*192,208)).save(frames/state/f'{index:02d}.png')
    bundled('inspect_frames',['--frames-root',frames,'--json-out',qa/'review.json','--states',state,'--require-components'],
            {'ROW_FRAME_COUNTS':counts})
    bundled('render_animation_previews',['--frames-root',frames,'--output-dir',RUN/'previews'],
            {'ROW_DURATIONS':{state:DURATIONS[state]}})
    with Image.open(RUN/'previews'/f'{state}.gif') as gif:
        decoded = [im.convert('RGBA') for im in ImageSequence.Iterator(gif)]
    unique = len({im.tobytes() for im in decoded})
    if unique < 6:
        raise ValueError(f'{state}: only {unique} different drawn poses')
    result = dict(state=state,frames=COUNTS[state],unique_frames=unique,
                  method='bundled pet scripts; generated pose frames only; no raster deformation',
                  durations_ms=DURATIONS[state],loop_duration_ms=sum(DURATIONS[state]),
                  gif=str((RUN/'previews'/f'{state}.gif').relative_to(ROOT)).replace('\\','/'),
                  review=str((qa/'review.json').relative_to(ROOT)).replace('\\','/'),ok=True)
    (qa/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


def main():
    global RUN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state',choices=EXPRESSIONS)
    parser.add_argument('--run-dir',type=Path,default=RUN)
    args = parser.parse_args()
    RUN=args.run_dir.resolve()
    print(json.dumps(process(args.state),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
