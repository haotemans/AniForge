"""Render exactly front/side/back from a saved character; never a full batch."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import mmd_lora_studio as addon
from mmd_lora_studio.core import three_view_recipe, write_json

parser = argparse.ArgumentParser()
parser.add_argument('--game', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--resolution', type=int, default=1024)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
addon.register()
settings = bpy.context.scene.ml_settings
settings.armature = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
settings.resolution = args.resolution
settings.output = str(Path(args.output) / args.game)
settings.preview_group = 'portrait'
settings.material_profiles = True
bpy.ops.ml.materials()
shots = three_view_recipe(settings.preview_group)
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
          'materials': [{'name': mat.name, 'source': mat.get('mmd_lora_source'),
                         'role': mat.get('mmd_lora_role'), 'surface': mat.surface_render_method,
                         'shader_to_rgb_nodes': sum(n.type == 'SHADERTORGB' for n in mat.node_tree.nodes)}
                        for mat in materials]}
write_json(Path(args.output) / args.game / 'three_views.json', report)
bpy.ops.wm.save_as_mainfile(filepath=str(Path(args.output) / args.game / 'optimized.blend'))
print('THREE_VIEWS_OK', args.game, session.folder)
