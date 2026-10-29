"""T-pose adaptation, sixteen camera fits, migration and dispatch; zero renders."""
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Matrix, Vector
from bpy_extras.object_utils import world_to_camera_view
import mmd_lora_studio as addon
from mmd_lora_studio.static_pose import CharacterState, has_animation

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
addon.register()
scene = bpy.context.scene; settings = scene.ml_settings
bpy.ops.object.armature_add()
rig = bpy.context.object; settings.armature = rig
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones[0].name = 'センター'
for name, head, tail, parent in [
        ('下半身', (0, 0, 1), (0, 0, 1.1), 'センター'),
        ('上半身', (0, 0, 1), (0, 0, 1.4), '下半身'),
        ('頭', (0, 0, 1.6), (0, 0, 1.9), '上半身')]:
    b = rig.data.edit_bones.new(name); b.head = head; b.tail = tail; b.parent = rig.data.edit_bones[parent]
for label, sign in [('左', 1), ('右', -1)]:
    for name, head, tail, parent in [
            ('腕', (.2*sign, 0, 1.45), (.55*sign, 0, 1.2), '上半身'),
            ('ひじ', (.55*sign, 0, 1.2), (.85*sign, 0, .98), label+'腕'),
            ('手首', (.85*sign, 0, .98), (1*sign, 0, .88), label+'ひじ')]:
        b = rig.data.edit_bones.new(label+name); b.head = head; b.tail = tail
        b.roll = .63*sign; b.parent = rig.data.edit_bones[parent]
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1))
body = bpy.context.object; body.parent = rig; body.scale = (.6, .3, 2)
body.shape_key_add(name='Basis'); face = body.shape_key_add(name='まばたき'); face.value = .8
shape = body.shape_key_add(name='BodyProportion'); shape.value = .3
clothes = body.shape_key_add(name='CapeOff'); clothes.value = .4
driver = face.driver_add('value'); driver.driver.expression = '.8'
constraint = rig.pose.bones['左腕'].constraints.new('COPY_ROTATION'); constraint.target = rig
rig.pose.bones['左腕'].rotation_mode = 'XYZ'; rig.pose.bones['左腕'].rotation_euler.x = .31
meshes = addon.model_meshes(settings)
baseline = CharacterState(rig, meshes)

def assert_tpose():
    for side, sign in [('left', 1), ('right', -1)]:
        for part in ('arm', 'elbow', 'wrist'):
            bone = addon.find_bone(rig, side+'_'+part)
            direction = (rig.matrix_world.to_3x3() @ (bone.tail-bone.head)).normalized()
            expected = Vector((sign, 0, 0)) if settings.front == 0 else Vector((0, sign, 0))
            assert direction.dot(expected) > .9999, (bone.name, list(direction), list(expected))

addon.apply_tpose(settings); addon.neutralize_face(rig, meshes); bpy.context.view_layer.update()
assert_tpose()
assert face.value == 0 and driver.mute
assert abs(shape.value-.3) < .0001 and abs(clothes.value-.4) < .0001
snapshot = [b.matrix_basis.copy() for b in rig.pose.bones]
addon.apply_tpose(settings)
assert all((b.matrix_basis.translation-m.translation).length < 1e-6 and
           b.matrix_basis.to_quaternion().rotation_difference(m.to_quaternion()).angle < 1e-4
           for b, m in zip(rig.pose.bones, snapshot))
# The same solver also follows a rotated/scaled object and rotated character front.
rig.rotation_euler.z = .7; rig.scale = (1.2, 1.2, 1.2); settings.front = 90
bpy.context.view_layer.update(); addon.apply_tpose(settings); assert_tpose()
rig.rotation_euler.z = 0; rig.scale = (1, 1, 1); settings.front = 0
baseline.restore()
assert not driver.mute and not constraint.mute and abs(face.value-.8) < .0001
old = rig.pose.bones['左腕'].matrix_basis.copy()
addon.refresh_previews()
automatic_camera = scene.camera
assert automatic_camera and automatic_camera.get('aniforge_camera')
assert scene.world.get('aniforge_white') and tuple(scene.world.color) == (1, 1, 1)
assert not scene.render.film_transparent
assert addon.ensure_camera(scene) == automatic_camera
assert len([o for o in scene.objects if o.type == 'CAMERA']) == 1
baseline.restore()

# Removed settings must never recreate a saved gesture or trigger native calls.
settings['presets_json'] = '{"poses":{"old":{}},"expressions":{"old":{}},"cameras":{"old lens":{"camera_lens":75}}}'
settings['animchar_reference'] = 'old.png'; settings['dataset_variation'] = 1
settings['ui_section'] = 1; settings['plan_index'] = 12
settings['preview_group'] = 3
addon.migrate_settings()
assert not hasattr(settings, 'ui_section') and settings.plan_index == 4 and settings.preview_group == 'lower_body'
assert all(name not in settings for name in addon.REMOVED_SETTINGS)
assert addon.camera_library(settings)['cameras']['old lens']['camera_lens'] == 75
assert 'pose' not in settings.bl_rna.properties and 'expression' not in settings.bl_rna.properties
assert not any(name.startswith('mmd_lora_studio.animchar') for name in sys.modules)
# Named view buttons are a UI alias for the existing persisted shot index.
assert settings.preview_view == 'BACK'
settings.preview_view = 'LEFT'
assert settings.plan_index == 2 and addon.selected_shot(settings)['azimuth'] == 90
settings.plan_index = 3
assert settings.preview_view == 'RIGHT'
settings.armature = None
assert bpy.ops.ml.pick_character() == {'FINISHED'}
assert settings.armature == rig

with tempfile.TemporaryDirectory() as folder:
    settings.output = folder; settings.resolution = 256
    settings.camera_name = 'My camera'; settings.camera_lens = 65
    assert bpy.ops.ml.camera_preset(save=True) == {'FINISHED'}
    settings.camera_lens = 85
    assert bpy.ops.ml.camera_preset() == {'FINISHED'}
    assert settings.camera_lens == 65
    cameras = str(Path(folder)/'camera.json')
    assert bpy.ops.ml.export_cameras(filepath=cameras) == {'FINISHED'}
    assert set(json.loads(Path(cameras).read_text())) == {'schema_version', 'cameras'}
    settings.camera_presets_json = ''
    assert bpy.ops.ml.import_cameras(filepath=cameras) == {'FINISHED'}
    assert 'My camera' in addon.camera_library(settings)['cameras']
    for projection in ('ORTHO', 'PERSP'):
        settings.camera_projection = projection
        camera_state = addon.remember_camera(automatic_camera)
        session = addon.RenderSession(bpy.context, addon.recipe())
        try:
            session.setup(bpy.context)
            assert session.camera == automatic_camera
            for shot in addon.recipe():
                session.prepare_shot(bpy.context, shot)
                assert_tpose()
                assert all(c.mute for b in rig.pose.bones for c in b.constraints)
                assert face.value == 0 and driver.mute
                for point in session.camera_meta['roi']:
                    p = world_to_camera_view(scene, session.camera, Vector(point))
                    assert -.001 <= p.x <= 1.001 and -.001 <= p.y <= 1.001 and p.z > 0, (shot, p)
                if shot['group'] == 'lower_body':
                    roi = session.camera_meta['roi']
                    assert min(p[2] for p in roi) <= .001
                    assert 1 < max(p[2] for p in roi) < 1.5
        finally:
            session.close('checked_without_rendering')
        assert not driver.mute and not constraint.mute
        assert scene.camera == automatic_camera
        assert len([o for o in scene.objects if o.type == 'CAMERA']) == 1
        restored = addon.remember_camera(automatic_camera)
        assert max(abs(restored[0][i][j]-camera_state[0][i][j]) for i in range(4) for j in range(4)) < 1e-5
        assert restored[1:] == camera_state[1:], (restored[1:], camera_state[1:])
        assert abs(face.value-.8) < .0001 and abs(shape.value-.3) < .0001
        assert all(abs(rig.pose.bones['左腕'].matrix_basis[i][j]-old[i][j]) < 1e-6 for i in range(4) for j in range(4))
    for test_only, count in ((True, 3), (False, 16)):
        dispatched = []
        with patch.object(addon.RenderSession, 'render', lambda self, context, shot: dispatched.append(shot.copy())):
            assert bpy.ops.ml.render_dataset(test_only=test_only) == {'FINISHED'}
        assert len(dispatched) == count
        if test_only:
            assert [s['azimuth'] for s in dispatched] == [0, 90, 180]
        else:
            assert dispatched == addon.recipe()
    # Only the selected view is dispatched by the one-image operator.
    dispatched = []
    with patch.object(addon.RenderSession, 'render', lambda self, context, shot: dispatched.append(shot.copy())):
        assert bpy.ops.ml.preview() == {'FINISHED'}
    assert dispatched == [addon.selected_shot(settings)]
    assert not list(Path(folder).rglob('*.png'))

# Action channels remain untouched and explicitly block static rendering.
rig.keyframe_insert(data_path='location', frame=1)
assert has_animation(rig, meshes)
try:
    addon.RenderSession(bpy.context)
except ValueError:
    pass
else:
    raise AssertionError('Action channels were not rejected')
rig.animation_data_clear()
# Renamed MMD bones also resolve when MMD Tools metadata is unavailable.
rig.pose.bones['左腕'].name = '腕.L'
rig.pose.bones['左ひじ'].name = 'ひじ.L'
rig.pose.bones['右腕'].name = '腕.R'
rig.pose.bones['右ひじ'].name = 'ひじ.R'
addon.apply_tpose(settings); assert_tpose()
addon.unregister()
from _bpy_restrict_state import RestrictBlend
with RestrictBlend():
    addon.register(); addon.unregister()
print('TPOSE_OK: A-pose/roll/transform adaptation, neutral face, migration, 32 camera fits, 16/3/1 dispatch; zero renders')
