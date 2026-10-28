"""Local real-model sitting/physics QA. No renders unless --render-three-views."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import addon_utils
import mmd_lora_studio as addon
from mmd_lora_studio.physics import physics_info, import_physics
from mmd_lora_studio.core import write_json

parser = argparse.ArgumentParser()
parser.add_argument('--source-pmx', required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--render-three-views', action='store_true')
parser.add_argument('--pose', default='sit_chair')
parser.add_argument('--allow-unsettled', action='store_true', help='Diagnostic only; mark unrested results in the manifest')
parser.add_argument('--backend', choices=('PROXY', 'AUTHORED'), default='PROXY')
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
args.output = args.output.resolve(); args.output.mkdir(parents=True, exist_ok=True)
addon_utils.enable('bl_ext.user_default.mmd_tools', default_set=False)
addon.register()
settings = bpy.context.scene.ml_settings
settings.armature = next(o for o in bpy.context.scene.objects if o.type == 'ARMATURE')
settings.output = str(args.output)
settings.physics_mode = 'MMD'; settings.physics_strict = not args.allow_unsettled
settings.physics_joint_mode = args.backend
settings.resolution = 768
report = {'renders': 0, 'status': 'running', 'physics_import': None, 'poses': {}}
if not physics_info(settings.armature)['bodies']:
    report['physics_import'] = import_physics(bpy.context, settings, args.source_pmx)
settings.pose = args.pose
shot = next(s for s in addon.recipe() if s['group'] == 'full_body' and s['pose'] == args.pose)
settings.material_profiles = True
assert bpy.ops.ml.materials() == {'FINISHED'}
before_count = len(bpy.data.objects)
before_pose = {b.name: b.matrix_basis.copy() for b in settings.armature.pose.bones}
before_mutes = {(b.name, c.name): c.mute for b in settings.armature.pose.bones for c in b.constraints}
before_frame = bpy.context.scene.frame_current
before_filepath = bpy.data.filepath
before_connections = {b.name: b.use_connect for b in settings.armature.data.bones}
session = addon.RenderSession(bpy.context, [shot]); session.setup(bpy.context)
try:
    if args.render_three_views:
        shots = [dict(shot, index=i+1, azimuth=angle, view=view) for i, (angle, view) in enumerate(((0, 'front'), (90, 'side'), (180, 'back')))]
        for item in shots:
            session.render(bpy.context, item)
            report['renders'] += 1
    else:
        checks = [s for s in addon.recipe() if s['pose'] in {'sit_chair', 'sit_side'}]
        checks += [next(s for s in addon.recipe() if s['group'] == 'full_body' and s['index'] == 1), checks[0]]
        for check_index, item in enumerate(checks):
            session.prepare_shot(bpy.context, item)
            label = f'{item["group"]}_{item["index"]:02d}_{item["pose"]}' + ('_repeat' if check_index == len(checks)-1 else '')
            report['poses'][label] = {'physics': session.physics_meta, 'camera': session.camera_meta}
            print('PHYSICS', label, json.dumps(session.physics_meta), flush=True)
        assert session.physics_meta['reused_physics']
    report['render_folder'] = str(session.folder)
finally:
    session.close('physics_checked')
assert len(bpy.data.objects) == before_count, (before_count, len(bpy.data.objects))
assert bpy.context.scene.frame_current == before_frame
assert bpy.data.filepath == before_filepath
assert all(b.use_connect == before_connections[b.name] for b in settings.armature.data.bones)
assert all(abs(b.matrix_basis[i][j]-before_pose[b.name][i][j]) < .001 for b in settings.armature.pose.bones for i in range(4) for j in range(4))
assert all(c.mute == before_mutes[(b.name, c.name)] for b in settings.armature.pose.bones for c in b.constraints)
report['state_restored'] = True
report['status'] = 'complete'
write_json(args.output / 'physics.json', report)
print('SITTING_PHYSICS_OK', args.output)
