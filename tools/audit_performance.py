"""Audit a loaded local rig, expressions, VPD and camera fitting without renders."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Matrix, Vector
from bpy_extras.object_utils import world_to_camera_view
import mmd_lora_studio as addon
from mmd_lora_studio.core import POSE_LABELS, EXPRESSION_LABELS, write_json
from mmd_lora_studio.camera import configure_camera

parser = argparse.ArgumentParser()
parser.add_argument('--game', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
addon.register()
settings = bpy.context.scene.ml_settings
settings.armature = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
meshes = addon.model_meshes(settings)
snapshot = [(bone, bone.rotation_mode, bone.matrix_basis.copy()) for bone in settings.armature.pose.bones]
shapes = [(key, key.value) for _, keys in addon.shape_blocks(meshes) for key in keys]

def reset():
    for bone, mode, matrix in snapshot:
        bone.rotation_mode = mode; bone.matrix_basis = matrix
    for key, value in shapes:
        key.value = value
    bpy.context.view_layer.update()

report = {'game': args.game, 'status': 'running', 'renders': 0, 'poses': {}, 'expressions': {}, 'camera_fits': 0}
for pose in POSE_LABELS:
    reset()
    changed = addon.pose_apply(settings, pose)
    before = {bone.name: bone.matrix_basis.copy() for bone in settings.armature.pose.bones}
    addon.pose_apply(settings, pose)
    difference = max(abs(before[bone.name][i][j]-bone.matrix_basis[i][j]) for bone in settings.armature.pose.bones for i in range(4) for j in range(4))
    assert difference < .001, (args.game, pose, difference)
    report['poses'][pose] = {'mapped_bones': len(changed), 'repeat_error': difference}
for expression in EXPRESSION_LABELS:
    report['expressions'][expression] = addon.expression_apply(settings, meshes, expression)
    assert expression == 'neutral' or report['expressions'][expression], (args.game, expression)
reset()
assert bpy.ops.ml.mapping_report() == {'FINISHED'}
report['mapping'] = json.loads(settings.mapping_report)

camera = bpy.data.objects.new('Audit camera', bpy.data.cameras.new('Audit camera'))
bpy.context.scene.collection.objects.link(camera)
bpy.context.scene.render.resolution_percentage = 100
settings.camera_aspect = 'PORTRAIT'; settings.camera_elevation = 8; settings.camera_roll = 5
for projection in ('ORTHO', 'PERSP'):
    settings.camera_projection = projection
    for group in ('portrait', 'half_body', 'full_body', 'outfit_detail'):
        for angle in (0, 90, 180):
            result = configure_camera(bpy.context, settings, meshes, camera, dict(group=group, azimuth=angle))
            projected = [world_to_camera_view(bpy.context.scene, camera, Vector(p)) for p in result['roi']]
            assert all(-.0001 <= p.x <= 1.0001 and -.0001 <= p.y <= 1.0001 and p.z > 0 for p in projected), (args.game, projection, group, angle)
            report['camera_fits'] += 1

if args.game == 'WUWA':
    head = addon.find_bone(settings.armature, 'head')
    bone_name = getattr(head.mmd_bone, 'name_j', '') or head.name
    settings.preset_name = 'VPD integration check'
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'fixture.vpd'
        text = 'Vocaloid Pose Data file\n\nmodel.osm;\n1;\n\nBone0{' + bone_name + '\n0,0,0;\n0,0.173648,0,0.984808;\n}\nMorph0{まばたき\n0.4;\n}\n'
        path.write_bytes(text.encode('cp932'))
        assert bpy.ops.ml.import_vpd(filepath=str(path)) == {'FINISHED'}
        assert settings.pose == 'VPD integration check'
        assert not (settings.armature.animation_data and settings.armature.animation_data.action)
        assert any(abs(head.matrix_basis[i][j]-Matrix.Identity(4)[i][j]) > .001 for i in range(4) for j in range(4))
        report['vpd_static_import'] = True
reset()
report['status'] = 'complete'
write_json(Path(args.output) / (args.game + '.json'), report)
print('REAL_PERFORMANCE_AUDIT_OK', args.game)
