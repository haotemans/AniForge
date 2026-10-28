"""Render exactly front/side/back from a saved character; never a full batch."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from tools.blender_source import load_addon
addon = load_addon()
from mmd_lora_studio.core import three_view_recipe, write_json

parser = argparse.ArgumentParser()
parser.add_argument('--game', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--resolution', type=int, default=1024)
parser.add_argument('--pose')
parser.add_argument('--expression')
parser.add_argument('--projection', choices=('ORTHO', 'PERSP'))
parser.add_argument('--aspect', choices=('SQUARE', 'PORTRAIT', 'LANDSCAPE'))
parser.add_argument('--group', choices=('portrait', 'half_body', 'full_body', 'outfit_detail'), default='portrait')
parser.add_argument('--plan-index', type=int, choices=range(1, 13), help='Use one linked 42-shot plan entry; still render only front/side/back')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
addon.register()
settings = bpy.context.scene.ml_settings
settings.armature = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
settings.resolution = args.resolution
settings.output = str(Path(args.output) / args.game)
settings.preview_group = args.group
if args.pose:
    settings.pose = args.pose
if args.expression:
    settings.expression = args.expression
if args.projection:
    settings.camera_projection = args.projection
if args.aspect:
    settings.camera_aspect = args.aspect
settings.material_profiles = True
bpy.ops.ml.materials()
shots = three_view_recipe(settings.preview_group, settings.pose, settings.expression)
if args.plan_index:
    base = next(s for s in addon.dataset_plan(settings) if s['group'] == args.group and s['index'] == args.plan_index)
    shots = [dict(base, index=i+1, azimuth=angle, view=view) for i, (angle, view) in enumerate(((0, 'front'), (90, 'side'), (180, 'back')))]
session = addon.RenderSession(bpy.context, shots)
try:
    session.setup(bpy.context)
    for shot in shots:
        session.render(bpy.context, shot)
except Exception as exc:
    session.close('failed', str(exc))
    raise
else:
    session.close('three_views')
materials = {slot.material for obj in addon.model_meshes(settings) for slot in obj.material_slots if slot.material}
report = {'status': 'complete', 'game': args.game, 'render_folder': str(session.folder),
          'version': addon.bl_info['version'], 'shots': 3, 'resolution': args.resolution,
          'selected_plan_index': args.plan_index,
          'materials': [{'name': mat.name, 'source': mat.get('mmd_lora_source'),
                         'role': mat.get('mmd_lora_role'), 'surface': mat.surface_render_method,
                         'shader_to_rgb_nodes': sum(n.type == 'SHADERTORGB' for n in mat.node_tree.nodes)}
                        for mat in materials]}
write_json(Path(args.output) / args.game / 'three_views.json', report)
destination = (Path(args.output) / args.game / 'optimized.blend').resolve()
if bpy.data.filepath and destination == Path(bpy.data.filepath).resolve():
    destination = destination.with_name('three_views_copy.blend')
bpy.ops.wm.save_as_mainfile(filepath=str(destination), copy=True)
print('THREE_VIEWS_OK', args.game, session.folder)
