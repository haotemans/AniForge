"""A/B render saved real characters using identical pose, light and framing."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import mmd_lora_studio as addon
from mmd_lora_studio.core import write_json

parser = argparse.ArgumentParser()
parser.add_argument('--game', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--resolution', type=int, default=1024)
parser.add_argument('--full-comparison', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
if not args.full_comparison:
    parser.error('Use test_three_views.py for ordinary tests; the A/B suite requires --full-comparison.')
addon.register()
scene = bpy.context.scene
settings = scene.ml_settings
settings.armature = next(o for o in scene.objects if o.type == 'ARMATURE')
settings.resolution = args.resolution
output = Path(args.output) / args.game
output.mkdir(parents=True, exist_ok=True)
report = {'game': args.game, 'resolution': args.resolution, 'status': 'running'}
shots = addon.three_view_recipe('portrait') + addon.three_view_recipe('full_body')

for enabled, name in [(False, 'before'), (True, 'after')]:
    settings.material_profiles = enabled
    settings.output = str(output / name)
    bpy.ops.ml.materials()
    report[name + '_materials'] = {slot.material.name: dict(role=slot.material.get('mmd_lora_role'),
                                                         source=slot.material.get('mmd_lora_source'),
                                                         preserved=bool(slot.material.get('mmd_lora_preserved_layer')))
                                   for o in addon.model_meshes(settings) for slot in o.material_slots if slot.material}
    session = addon.RenderSession(bpy.context)
    try:
        session.setup(bpy.context)
        report[name] = str(session.folder)
        write_json(output / 'comparison.json', report)
        for shot in shots:
            session.render(bpy.context, shot)
    except Exception as exc:
        session.close('failed', str(exc))
        raise
    else:
        session.close('comparison')

report['alpha_recovered'] = [{'name': img.name, 'source': img.get('mmd_lora_alpha_source'), 'mode': img.alpha_mode}
                             for img in bpy.data.images if img.get('mmd_lora_alpha_source')]
report['status'] = 'complete'
write_json(output / 'comparison.json', report)
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'optimized.blend'))
print('MATERIAL_PROFILE_QA_OK', args.game, flush=True)
