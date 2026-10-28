"""Check a local VPD library on a loaded character; never render images."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Matrix, Vector
from bpy_extras.object_utils import world_to_camera_view
from tools.blender_source import load_addon
addon = load_addon()
from mmd_lora_studio.core import ShotSettings, dataset_coverage, write_json
from mmd_lora_studio.pose_library import scan_pose_folder

parser = argparse.ArgumentParser()
parser.add_argument('--poses', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--game', default='character')
parser.add_argument('--save-copy', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
args.output.mkdir(parents=True, exist_ok=True)
addon.register()
settings = bpy.context.scene.ml_settings
rig = settings.armature = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
meshes = addon.model_meshes(settings)
settings.pose_folder = str(args.poses)
source_blend = bpy.data.filepath
files = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.poses.rglob('*') if p.is_file()}
pose = [(bone, bone.rotation_mode, bone.matrix_basis.copy()) for bone in rig.pose.bones]
mutes = [(c, c.mute) for bone in rig.pose.bones for c in bone.constraints]
shapes = [(key, key.value) for _, keys in addon.shape_blocks(meshes) for key in keys]
actions = len(bpy.data.actions)


def pose_error(before):
    return max(abs(matrix[i][j]-bone.matrix_basis[i][j]) for bone, _, matrix in before for i in range(4) for j in range(4))


def reset():
    addon.restore_seated(rig)
    for bone, mode, matrix in pose:
        bone.rotation_mode = mode; bone.matrix_basis = matrix
    for constraint, mute in mutes:
        constraint.mute = mute
    for key, value in shapes:
        key.value = value
    bpy.context.view_layer.update()


assert bpy.ops.ml.import_pose_folder() == {'FINISHED'}
assert pose_error(pose) < .00001
assert all(c.mute == mute for c, mute in mutes)
assert all(abs(key.value-value) < .00001 for key, value in shapes)
assert len(bpy.data.actions) == actions
assert not (rig.animation_data and rig.animation_data.action)
library = addon.custom_presets(settings)
report = json.loads(settings.pose_folder_report)
assert len(report['imported']) == sum(not e['paired'] for e in scan_pose_folder(args.poses))
report.update(game=args.game, source_blend=source_blend, shots=[], renders=0)
shots = addon.dataset_plan(settings)
assert len(shots) == 36
assert set(dataset_coverage(shots)['per_group'].values()) == {9}
camera = bpy.data.objects.new('Local pose audit camera', bpy.data.cameras.new('Local pose audit camera'))
bpy.context.scene.collection.objects.link(camera)
try:
    for shot in shots:
        reset()
        effective = ShotSettings(settings, shot)
        addon.pose_apply(effective, shot['pose'])
        addon.expression_apply(effective, meshes, shot['expression'])
        first = [(b, b.rotation_mode, b.matrix_basis.copy()) for b in rig.pose.bones]
        addon.pose_apply(effective, shot['pose'])
        assert pose_error(first) < .0001, shot['title']
        assert all(math.isfinite(v) for b in rig.pose.bones for row in b.matrix for v in row), shot['title']
        fit = addon.configure_camera(bpy.context, settings, meshes, camera, shot)
        projected = [world_to_camera_view(bpy.context.scene, camera, Vector(p)) for p in fit['roi']]
        assert all(-.0001 <= p.x <= 1.0001 and -.0001 <= p.y <= 1.0001 and p.z > 0 for p in projected), shot['title']
        entry = {key: shot[key] for key in ('group', 'index', 'title', 'pose', 'expression', 'azimuth')}
        entry['included_hands'] = fit['included_hands']
        if shot['pose'] in library['poses']:
            saved = library['poses'][shot['pose']]
            entry['captured_matrix_error'] = max(abs(b.matrix_basis[i][j]-saved[b.name][i][j]) for b in rig.pose.bones if b.name in saved for i in range(4) for j in range(4))
            # Evaluated joint positions are useful for diagnosing IK across rigs.
            evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
            entry['joints'] = {key: list(evaluated.matrix_world @ evaluated.pose.bones[bone.name].head)
                               for key in ('head', 'left_wrist', 'right_wrist', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle')
                               if (bone := addon.find_bone(rig, key, settings))}
            entry['muted_ik'] = [b.name+':'+c.name for b in rig.pose.bones for c in b.constraints if c.type == 'IK' and c.mute]
            assert all(c.mute == mute for c, mute in mutes), (shot['title'], 'authored IK changed')
            assert entry['captured_matrix_error'] < .0001, (shot['title'], entry['captured_matrix_error'])
        report['shots'].append(entry)
finally:
    reset()
    bpy.data.objects.remove(camera, do_unlink=True)

assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in files.items())
assert pose_error(pose) < .00001
report.update(status='complete', source_files_unchanged=True, camera_fits=len(report['shots']),
              coverage=dataset_coverage(shots), library_poses=len(library['poses']), shot_overrides=len(library['shot_overrides']))
write_json(args.output / (args.game+'.json'), report)
write_json(args.output / (args.game+'_local_presets.json'), library)
if args.save_copy:
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / (args.game+'_local_poses.blend')), copy=True)
print('POSE_FOLDER_AUDIT_OK', args.game, len(report['imported']), report['camera_fits'], 'no renders', flush=True)
