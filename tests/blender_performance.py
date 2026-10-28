"""Pose/expression/library/camera integration checks. Does not render images."""
import json
import math
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Matrix, Vector
from bpy_extras.object_utils import world_to_camera_view
import mmd_lora_studio as addon
from mmd_lora_studio.core import POSE_LABELS
from mmd_lora_studio.camera import configure_camera

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
addon.register()
scene = bpy.context.scene; settings = scene.ml_settings
bpy.ops.object.armature_add()
rig = bpy.context.object; rig.name = 'PerformanceRig'
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones[0].name = '下半身'
for name, head, tail, parent in [('上半身', (0, 0, 1), (0, 0, 1.4), '下半身'),
        ('上半身2', (0, 0, 1.4), (0, 0, 1.6), '上半身'),
        ('頭', (0, 0, 1.6), (0, 0, 1.9), '上半身2'),
        ('左腕', (.2, 0, 1.4), (.65, 0, 1.4), '上半身2'), ('右腕', (-.2, 0, 1.4), (-.65, 0, 1.4), '上半身2'),
        ('左ひじ', (.65, 0, 1.4), (.95, 0, 1.4), '左腕'), ('右ひじ', (-.65, 0, 1.4), (-.95, 0, 1.4), '右腕'),
        ('左手首', (.95, 0, 1.4), (1.1, 0, 1.4), '左ひじ'), ('右手首', (-.95, 0, 1.4), (-1.1, 0, 1.4), '右ひじ')]:
    bone = rig.data.edit_bones.new(name); bone.head = head; bone.tail = tail; bone.parent = rig.data.edit_bones[parent]
for name, offset in [('左人指1', 0), ('左中指1', .04), ('左薬指1', .08)]:
    bone = rig.data.edit_bones.new(name); bone.head = (1.1, offset, 1.4); bone.tail = (1.2, offset, 1.4); bone.parent = rig.data.edit_bones['左手首']
bpy.ops.object.mode_set(mode='OBJECT')
settings.armature = rig

def cube(name, center, scale):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object; obj.name = name; obj.scale = scale; obj.parent = rig
    return obj

body = cube('Body', (0, 0, .9), (.7, .35, 1.8))
face = cube('Face', (0, -.05, 1.7), (.3, .2, .35))
material = bpy.data.materials.new('面'); material['mmd_lora_role'] = 'FACE'; face.data.materials.append(material)
face.shape_key_add(name='Basis')
keys = {name: face.shape_key_add(name=name) for name in ('まばたき', '口角上げ', 'あ', 'ウィンク2', 'BodyProportion')}
keys['BodyProportion'].value = .3
meshes = addon.model_meshes(settings)
settings.expression_strength = .5
addon.expression_apply(settings, meshes, 'smile')
assert abs(keys['口角上げ'].value-.35) < .00001 and keys['まばたき'].value == 0
addon.expression_apply(settings, meshes, 'smile')
assert abs(keys['口角上げ'].value-.35) < .00001
addon.expression_apply(settings, meshes, 'blink')
assert abs(keys['まばたき'].value-.5) < .00001 and keys['口角上げ'].value == 0
assert abs(keys['BodyProportion'].value-.3) < .00001
settings.expression_strength = 1
for pose in POSE_LABELS:
    addon.pose_apply(settings, pose)
    first = {bone.name: bone.matrix_basis.copy() for bone in rig.pose.bones}
    addon.pose_apply(settings, pose)
    assert all(abs(first[b.name][i][j]-b.matrix_basis[i][j]) < .0001 for b in rig.pose.bones for i in range(4) for j in range(4)), pose
settings.pose_strength = 0
addon.pose_apply(settings, 'wave')
assert rig.pose.bones['左腕'].rotation_quaternion.angle < .0001
settings.pose_strength = 1
settings.head_angles = (8, 12, -4)
addon.pose_apply(settings, 'relaxed')
head_before = rig.pose.bones['頭'].matrix_basis.copy()
addon.pose_apply(settings, 'relaxed')
assert all(abs(head_before[i][j]-rig.pose.bones['頭'].matrix_basis[i][j]) < .00001 for i in range(4) for j in range(4))
settings.head_angles = (0, 0, 0)
settings.hand_pose = 'FIST'; addon.pose_apply(settings, 'relaxed')
assert abs(rig.pose.bones['左人指1'].rotation_euler.x) > .5
settings.hand_pose = 'PEACE'; addon.pose_apply(settings, 'relaxed')
assert abs(rig.pose.bones['左人指1'].rotation_euler.x) < .00001 and abs(rig.pose.bones['左薬指1'].rotation_euler.x) > .5
settings.hand_pose = 'KEEP'
settings.pose = 'hand_hip'; addon.pose_apply(settings, settings.pose)
settings.preset_name = '我的站姿'
assert bpy.ops.ml.preset(action='SAVE_POSE') == {'FINISHED'}
assert settings.pose == '我的站姿'
addon.pose_apply(settings, settings.pose)
settings.preset_name = '轻微微笑'; addon.expression_apply(settings, meshes, 'smile')
assert bpy.ops.ml.preset(action='SAVE_EXPRESSION') == {'FINISHED'}
assert settings.expression == '轻微微笑'
assert 'BodyProportion' not in addon.custom_presets(settings)['expressions']['轻微微笑'][face.name]
settings.preset_name = '竖幅85mm'
settings.camera_projection = 'PERSP'; settings.camera_aspect = 'PORTRAIT'
assert bpy.ops.ml.preset(action='SAVE_CAMERA') == {'FINISHED'}
settings.camera_lens = 35
assert bpy.ops.ml.preset(action='CAMERA') == {'FINISHED'}
assert settings.camera_lens == 85

with tempfile.TemporaryDirectory() as folder:
    path = str(Path(folder) / 'library.json')
    assert bpy.ops.ml.export_presets(filepath=path) == {'FINISHED'}
    saved = addon.custom_presets(settings)
    settings.presets_json = ''
    assert bpy.ops.ml.import_presets(filepath=path) == {'FINISHED'}
    assert addon.custom_presets(settings) == saved
    settings.output = folder
    dispatched = []
    with patch.object(addon.RenderSession, 'render', lambda self, context, shot: dispatched.append(shot.copy())):
        assert bpy.ops.ml.render_dataset() == {'FINISHED'}
    assert len(dispatched) == 3 and all(s['pose'] == '我的站姿' and s['expression'] == '轻微微笑' for s in dispatched)
    dispatched.clear()
    with patch.object(addon.RenderSession, 'render', lambda self, context, shot: dispatched.append(shot.copy())):
        assert bpy.ops.ml.render_dataset(test_only=False) == {'FINISHED'}
    assert len(dispatched) == 36 and dispatched == addon.recipe()
    dispatched.clear()
    settings.preview_group = 'portrait'; settings.plan_index = 3
    with patch.object(addon.RenderSession, 'render', lambda self, context, shot: dispatched.append(shot.copy())):
        assert bpy.ops.ml.render_dataset(use_plan_shot=True) == {'FINISHED'}
    assert [s['azimuth'] for s in dispatched] == [0, 90, 180]
    assert all(s['pose'] == 'peace_face' and s['expression'] == 'wink' for s in dispatched)

camera = bpy.data.objects.new('Fit Camera', bpy.data.cameras.new('Fit Camera')); scene.collection.objects.link(camera)
scene.render.resolution_percentage = 100
settings.camera_elevation = 12; settings.camera_roll = 8
settings.camera_shift_x = .1; settings.camera_shift_y = -.08
settings.camera_target_offset = (.03, 0, .01)
for projection in ('ORTHO', 'PERSP'):
    for aspect in ('SQUARE', 'PORTRAIT', 'LANDSCAPE'):
        settings.camera_projection = projection; settings.camera_aspect = aspect
        for group in ('portrait', 'half_body', 'full_body', 'outfit_detail'):
            for angle in (0, 90, 180):
                data = configure_camera(bpy.context, settings, meshes, camera, {'group': group, 'azimuth': angle})
                projected = [world_to_camera_view(scene, camera, Vector(point)) for point in data['roi']]
                assert all(-.0001 <= p.x <= 1.0001 and -.0001 <= p.y <= 1.0001 and p.z > 0 for p in projected), (projection, aspect, group, angle, [tuple(p) for p in projected])

with tempfile.TemporaryDirectory() as folder:
    settings.output = folder
    before = (settings.camera_lens, settings.camera_projection, settings.camera_aspect, tuple(settings.head_angles), settings.hand_pose)
    session = addon.RenderSession(bpy.context, addon.recipe()); session.setup(bpy.context)
    for shot in addon.recipe():
        session.prepare_shot(bpy.context, shot)
        projected = [world_to_camera_view(scene, session.camera, Vector(p)) for p in session.camera_meta['roi']]
        assert all(-.0001 <= p.x <= 1.0001 and -.0001 <= p.y <= 1.0001 and p.z > 0 for p in projected), shot['title']
        assert (scene.render.resolution_x, scene.render.resolution_y) == (768, 1024)
        assert session.camera.data.type == 'PERSP'
    session.close('checked_without_rendering')
    assert before == (settings.camera_lens, settings.camera_projection, settings.camera_aspect, tuple(settings.head_angles), settings.hand_pose)
    assert not list(Path(folder).rglob('*.png'))
assert bpy.ops.ml.shot_plan(report_only=True) == {'FINISHED'}
assert len(json.loads(bpy.data.texts['AniForge 36 Shot Plan'].as_string())['shots']) == 36
assert bpy.ops.ml.shot_plan() == {'FINISHED'}
before_cameras = len(bpy.data.cameras)
settings.camera_aspect = 'LANDSCAPE'; settings.camera_shift_y = .4
try:
    configure_camera(bpy.context, settings, meshes, camera, {'group': 'portrait', 'azimuth': 0})
    raise AssertionError('Expected an impossible framing shift to be rejected')
except ValueError:
    pass
settings.camera_shift_y = 0
assert bpy.ops.ml.frame_camera() == {'FINISHED'}
assert bpy.ops.ml.frame_camera() == {'FINISHED'}
assert len(bpy.data.cameras) == before_cameras
with tempfile.TemporaryDirectory() as folder:
    settings.output = folder
    before_pose = [(bone, bone.rotation_mode, bone.matrix_basis.copy()) for bone in rig.pose.bones]
    before_shapes = [(key, key.value) for _, keys in addon.shape_blocks(meshes) for key in keys]
    before_objects = len(bpy.data.objects)
    before_camera = scene.camera
    scene.render.pixel_aspect_x = 1.2; scene.render.pixel_aspect_y = 1.4
    session = addon.RenderSession(bpy.context)
    session.setup(bpy.context)
    assert scene.render.pixel_aspect_x == scene.render.pixel_aspect_y == 1
    session.prepare_shot(bpy.context, dict(group='portrait', index=1, azimuth=0, pose='wave', expression='blink'))
    session.close('cancelled')
    assert scene.camera == before_camera and len(bpy.data.objects) == before_objects
    assert abs(scene.render.pixel_aspect_x-1.2) < .0001 and abs(scene.render.pixel_aspect_y-1.4) < .0001
    assert all(abs(bone.matrix_basis[i][j]-matrix[i][j]) < .0001 and bone.rotation_mode == mode for bone, mode, matrix in before_pose for i in range(4) for j in range(4))
    assert all(abs(key.value-value) < .0001 for key, value in before_shapes)

print('ANIFORGE_PERFORMANCE_CAMERA_OK: 15 deterministic poses, named JSON library, 72 camera fits + 36 linked shots; no renders')
addon.unregister()
