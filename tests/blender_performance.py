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
rig.data.edit_bones[0].name = 'センター'
for name, head, tail, parent in [('下半身', (0, 0, 1), (0, 0, 1.1), 'センター'),
        ('左足', (.12, 0, 1), (.12, 0, .55), '下半身'), ('右足', (-.12, 0, 1), (-.12, 0, .55), '下半身'),
        ('左ひざ', (.12, 0, .55), (.12, 0, .1), '左足'), ('右ひざ', (-.12, 0, .55), (-.12, 0, .1), '右足'),
        ('左足首', (.12, 0, .1), (.12, -.15, .05), '左ひざ'), ('右足首', (-.12, 0, .1), (-.12, -.15, .05), '右ひざ'),
        ('上半身', (0, 0, 1), (0, 0, 1.4), '下半身'),
        ('上半身2', (0, 0, 1.4), (0, 0, 1.6), '上半身'),
        ('頭', (0, 0, 1.6), (0, 0, 1.9), '上半身2'),
        ('左腕', (.2, 0, 1.4), (.65, 0, 1.4), '上半身2'), ('右腕', (-.2, 0, 1.4), (-.65, 0, 1.4), '上半身2'),
        ('左ひじ', (.65, 0, 1.4), (.95, 0, 1.4), '左腕'), ('右ひじ', (-.65, 0, 1.4), (-.95, 0, 1.4), '右腕'),
        ('左手首', (.95, 0, 1.4), (1.1, 0, 1.4), '左ひじ'), ('右手首', (-.95, 0, 1.4), (-1.1, 0, 1.4), '右ひじ')]:
    bone = rig.data.edit_bones.new(name); bone.head = head; bone.tail = tail; bone.parent = rig.data.edit_bones[parent]
for name, offset in [('左人指1', 0), ('左中指1', .04), ('左薬指1', .08)]:
    bone = rig.data.edit_bones.new(name); bone.head = (1.1, offset, 1.4); bone.tail = (1.2, offset, 1.4); bone.parent = rig.data.edit_bones['左手首']
for name, offset in [('右人指1', 0), ('右中指1', .04), ('右薬指1', .08)]:
    bone = rig.data.edit_bones.new(name); bone.head = (-1.1, offset, 1.4); bone.tail = (-1.2, offset, 1.4)
    bone.parent = rig.data.edit_bones['右手首']; bone.roll = .63
bpy.ops.object.mode_set(mode='OBJECT')
settings.armature = rig
ik = rig.pose.bones['左ひざ'].constraints.new('IK'); ik.name = 'Leg IK restore check'; ik.chain_count = 2

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
addon.pose_apply(settings, 'sit_chair')
assert ik.mute
assert rig.pose.bones['センター'].location.length > .1
thigh = rig.pose.bones['左足']
assert abs((thigh.tail-thigh.head).normalized().z) < .2
addon.pose_apply(settings, 'relaxed')
assert not ik.mute and rig.pose.bones['センター'].location.length < .0001
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
    assert len(dispatched) == 39 and dispatched == addon.recipe()
    dispatched.clear()
    settings.preview_group = 'portrait'; settings.plan_index = 5
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
        assert (scene.render.resolution_x, scene.render.resolution_y) == ((1024, 1024) if shot['group'] == 'portrait' else (768, 1024))
        assert session.camera.data.type == 'PERSP'
    session.close('checked_without_rendering')
    assert before == (settings.camera_lens, settings.camera_projection, settings.camera_aspect, tuple(settings.head_angles), settings.hand_pose)
    assert not list(Path(folder).rglob('*.png'))
assert bpy.ops.ml.shot_plan(report_only=True) == {'FINISHED'}
assert len(json.loads(bpy.data.texts['AniForge 39 Shot Plan'].as_string())['shots']) == 39
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

# Imported poses must retain authored foot IK, and must not leave transformed
# legs, IK controls or fingers behind when changing back to a builtin pose.
addon.restore_seated(rig)
for bone in rig.pose.bones:
    bone.matrix_basis = Matrix.Identity(4)
baseline = addon.capture_pose(settings)
custom = addon.custom_presets(settings)
custom['poses']['native IK pose'] = addon.capture_pose(settings)
custom['poses']['native IK pose']['左足'] = [list(row) for row in Matrix.Rotation(1.0, 4, 'X')]
custom['poses']['native IK pose']['センター'] = [list(row) for row in Matrix.Translation((.1, 0, -.1))]
custom['poses']['native IK pose']['左人指1'] = [list(row) for row in Matrix.Rotation(.6, 4, 'X')]
custom['pose_options']['native IK pose'] = {'leg_mode': 'KEEP'}
addon.store_presets(settings, custom)
settings.pose = 'native IK pose'; settings.expression = 'smile'
settings.head_angles = (0, 0, 0); settings.hand_pose = 'KEEP'
addon.pose_apply(settings, settings.pose)
assert not ik.mute and rig.pose.bones['センター'].location.length > .05
addon.pose_apply(settings, 'relaxed')
assert not ik.mute and rig.pose.bones['センター'].location.length < .00001
for name in ('左足', '左人指1'):
    assert all(abs(rig.pose.bones[name].matrix_basis[i][j]-baseline[name][i][j]) < .00001 for i in range(4) for j in range(4))
settings.preview_group = 'portrait'; settings.plan_index = 5
settings.slot_left_hand = settings.slot_right_hand = True
assert bpy.ops.ml.assign_shot_pose() == {'FINISHED'}
shot = addon.dataset_plan(settings)[4]
assert shot['pose'] == 'native IK pose' and shot['performance']['hand_pose'] == 'KEEP'
assert len(addon.dataset_plan(settings)) == 39
settings.dataset_variation = 'CURRENT'
assert all(s['pose'] == settings.pose and 'pose_overridden' not in s for s in addon.dataset_plan(settings))
settings.dataset_variation = 'CYCLE'
assert bpy.ops.ml.assign_shot_pose(reset=True) == {'FINISHED'}
assert addon.dataset_plan(settings)[4]['pose'] == 'peace_face'
# The included-hand frame must contain fingertips beyond the old wrist box.
addon.restore_seated(rig)
for bone in rig.pose.bones:
    bone.matrix_basis = Matrix.Identity(4)
scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
settings.camera_shift_x = settings.camera_shift_y = settings.camera_roll = 0
settings.camera_zoom = 1; settings.camera_target_offset = (0, 0, 0)
settings.camera_aspect = 'PORTRAIT'; settings.camera_projection = 'PERSP'
bpy.context.view_layer.update()
configure_camera(bpy.context, settings, meshes, camera, {'group': 'portrait', 'azimuth': 0, 'include_hands': ['left_wrist']})
tip = rig.matrix_world @ rig.pose.bones['左人指1'].tail
projected = world_to_camera_view(scene, camera, tip)
assert 0 <= projected.x <= 1 and 0 <= projected.y <= 1 and projected.z > 0
# Clamp on property changes and file migration, before panel drawing or an
# operator can look up an absent slot in a nine-photo group.
settings.preview_group = 'portrait'; settings.plan_index = 12
assert settings.plan_index == 12
for group in ('half_body', 'full_body', 'outfit_detail'):
    settings.preview_group = group
    assert settings.plan_index == 9
    settings.plan_index = 12
    assert settings.plan_index == 9
    settings['plan_index'] = 12
    addon.migrate_settings()
    assert settings.plan_index == 9
    settings.preview_group = 'portrait'; settings.plan_index = 12
print('ANIFORGE_PERFORMANCE_CAMERA_OK: native IK / preset switching, slot overrides, 17 deterministic poses, 72 camera fits + 39 linked shots; no renders')
from mmd_lora_studio.hand_pose import hand_frame, hand_shape_metrics
from mmd_lora_studio.performance import finger_bones, bone_side
# Articulated thumbs and a genuinely skinned fingertip prevent a regression
# from being hidden by correct-looking bone matrices with rigid geometry.
bpy.context.view_layer.objects.active = rig; rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
for prefix, sign in [('左', 1), ('右', -1)]:
    parent = prefix+'手首'
    for i, head, tail in [(0, (1.02, -.02, 1.4), (1.06, -.045, 1.4)),
                           (1, (1.06, -.045, 1.4), (1.09, -.06, 1.4)),
                           (2, (1.09, -.06, 1.4), (1.12, -.075, 1.4))]:
        bone = rig.data.edit_bones.new(prefix+'親指'+str(i))
        bone.head = (head[0]*sign, head[1], head[2]); bone.tail = (tail[0]*sign, tail[1], tail[2])
        bone.parent = rig.data.edit_bones[parent]; bone.roll = .37*(i+1)*sign
        parent = bone.name
bpy.ops.object.mode_set(mode='OBJECT')
tip_mesh = bpy.data.meshes.new('Thumb skin fixture')
tip_mesh.from_pydata([(1.12, -.075, 1.4), (1.123, -.075, 1.4), (1.12, -.072, 1.4)], [], [(0, 1, 2)])
tip_skin = bpy.data.objects.new('Thumb skin fixture', tip_mesh); scene.collection.objects.link(tip_skin)
tip_skin.parent = rig
group = tip_skin.vertex_groups.new(name='左親指2'); group.add([0, 1, 2], 1, 'REPLACE')
modifier = tip_skin.modifiers.new('Thumb skinning', 'ARMATURE'); modifier.object = rig
fingers = list(finger_bones(rig))
addon.restore_seated(rig)
for bone in rig.pose.bones:
    bone.matrix_basis = Matrix.Identity(4)
wrist_baseline = {side: addon.find_bone(rig, side+'_wrist', settings).matrix_basis.copy() for side in ('left', 'right')}
settings.hand_pose = 'AUTO'
mirror_positions = {}
for mirror in ('LEFT', 'RIGHT'):
    settings.hip_hand = mirror
    shot = next(s for s in addon.dataset_plan(settings) if s['group'] == 'half_body' and s['index'] == 5)
    assert shot['azimuth'] == (30 if mirror == 'LEFT' else -30)
    effective = addon.ShotSettings(settings, shot)
    addon.pose_apply(effective, 'hand_hip')
    mirror_positions[mirror] = {side: addon.find_bone(rig, side+'_wrist', settings).head.copy() for side in ('left', 'right')}
    first = addon.capture_pose(settings)
    addon.pose_apply(effective, 'hand_hip')
    assert all(abs(b.matrix_basis[i][j]-first[b.name][i][j]) < .0001 for b in rig.pose.bones for i in range(4) for j in range(4))
    side = mirror.lower(); wrist = addon.find_bone(rig, side+'_wrist', settings)
    shape = hand_shape_metrics(rig, side, wrist, addon.find_bone(rig, side+'_elbow', settings), fingers)
    assert 10 < shape['thumb_open_degrees'] < 55, shape
    assert 8 < shape['thumb_tip_bend_degrees'] < 40, shape
    _, inward, _ = hand_frame(rig, side, wrist, fingers)
    for bone, kind, segment in fingers:
        if bone_side(bone) == side and kind != 'thumb':
            rest = bone.bone.matrix_local.to_quaternion()
            delta = rest @ bone.matrix_basis.to_quaternion() @ rest.inverted()
            direction = delta @ (bone.bone.tail_local-bone.bone.head_local).normalized()
            assert direction.dot(inward) > .01, (mirror, bone.name)
    if mirror == 'LEFT':
        graph = bpy.context.evaluated_depsgraph_get()
        posed_tip = tip_skin.evaluated_get(graph).data.vertices[0].co.copy()
        thumb = rig.pose.bones['左親指2']; thumb_basis = thumb.matrix_basis.copy()
        thumb.matrix_basis = Matrix.Identity(4); bpy.context.view_layer.update()
        straight_tip = tip_skin.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices[0].co.copy()
        assert (posed_tip-straight_tip).length > .001
        thumb.matrix_basis = thumb_basis; bpy.context.view_layer.update()
for side in ('left', 'right'):
    opposite = 'right' if side == 'left' else 'left'
    a, b = mirror_positions['LEFT'][side], mirror_positions['RIGHT'][opposite]
    assert abs(a.x+b.x)<.01 and abs(a.y-b.y)<.01 and abs(a.z-b.z)<.01
addon.pose_apply(settings, 'relaxed')
for side, matrix in wrist_baseline.items():
    wrist = addon.find_bone(rig, side+'_wrist', settings)
    assert all(abs(wrist.matrix_basis[i][j]-matrix[i][j]) < .00001 for i in range(4) for j in range(4))
print('HAND_HIP_FIT_OK: mirrored targets/cameras, inward finger flex with rolled bones, repeat and state restoration; no renders')
addon.unregister()
