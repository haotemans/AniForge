"""Static rendering never starts a physics worker or advances the timeline."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import mmd_lora_studio as addon

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
addon.register()
scene = bpy.context.scene; settings = scene.ml_settings
bpy.ops.object.armature_add()
rig = settings.armature = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones[0].name = 'センター'
for name, head, tail, parent in [
        ('下半身', (0, 0, 1), (0, 0, 1.1), 'センター'),
        ('上半身', (0, 0, 1), (0, 0, 1.4), '下半身'),
        ('上半身2', (0, 0, 1.4), (0, 0, 1.6), '上半身'),
        ('頭', (0, 0, 1.6), (0, 0, 1.9), '上半身2')]:
    bone = rig.data.edit_bones.new(name); bone.head = head; bone.tail = tail; bone.parent = rig.data.edit_bones[parent]
for prefix, sign in [('左', 1), ('右', -1)]:
    for name, head, tail, parent in [
            ('足', (.12*sign, 0, 1), (.12*sign, 0, .55), '下半身'),
            ('ひざ', (.12*sign, 0, .55), (.12*sign, 0, .1), prefix+'足'),
            ('足首', (.12*sign, 0, .1), (.12*sign, -.15, .05), prefix+'ひざ'),
            ('腕', (.2*sign, 0, 1.4), (.65*sign, 0, 1.4), '上半身2'),
            ('ひじ', (.65*sign, 0, 1.4), (.95*sign, 0, 1.4), prefix+'腕'),
            ('手首', (.95*sign, 0, 1.4), (1.1*sign, 0, 1.4), prefix+'ひじ')]:
        bone = rig.data.edit_bones.new(prefix+name); bone.head = head; bone.tail = tail; bone.parent = rig.data.edit_bones[parent]
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, .9))
body = bpy.context.object; body.parent = rig; body.scale = (.6, .3, 1.8)
cloth = body.modifiers.new('Existing cloth', 'CLOTH'); cloth.show_viewport = False
soft = body.modifiers.new('Existing soft body', 'SOFT_BODY'); soft.show_render = False
modifier_flags = [(m, m.show_viewport, m.show_render) for m in (cloth, soft)]
bpy.ops.rigidbody.world_add(); world = scene.rigidbody_world; world.enabled = True
target = bpy.data.objects.new('Existing MMD track target', None); scene.collection.objects.link(target)
track = rig.pose.bones['頭'].constraints.new('COPY_LOCATION'); track.name = 'mmd_tools_rigid_track'; track.target = target
track.mute = False
frame = scene.frame_current; object_count = len(bpy.data.objects)
# Raw values left in an older .blend must never reactivate removed features.
settings['physics_mode'] = 1; settings['physics_frames'] = 240
settings['ui_section'] = 4; addon.migrate_settings()
assert not hasattr(settings, 'physics_mode')
assert 'ui_section' not in settings.bl_rna.properties

with tempfile.TemporaryDirectory() as folder:
    settings.output = folder
    fixture_plan = addon.recipe()
    with patch('subprocess.run', side_effect=AssertionError('Physics subprocess started')), \
         patch('subprocess.Popen', side_effect=AssertionError('Physics worker started')):
        session = addon.RenderSession(bpy.context, fixture_plan); session.setup(bpy.context)
        assert session.manifest['deformation_mode'] == 'ARMATURE_STATIC'
        assert not world.enabled and track.mute
        assert all(not m.show_viewport and not m.show_render for m, _, _ in modifier_flags)
        for shot in fixture_plan:
            session.prepare_shot(bpy.context, shot)
            assert not world.enabled and track.mute
            assert scene.frame_current == frame
        session.close('checked_without_rendering')
        assert world.enabled and not track.mute
        assert all((m.show_viewport, m.show_render) == (v, r) for m, v, r in modifier_flags)
        assert len(bpy.data.objects) == object_count
        session = addon.RenderSession(bpy.context); session.setup(bpy.context)
        try:
            with patch.object(addon, 'configure_camera', side_effect=ValueError('Forced framing error')):
                session.prepare_shot(bpy.context, fixture_plan[0])
        except ValueError:
            session.close('failed', 'Forced framing error')
        else:
            raise AssertionError('Expected a framing error')
        assert world.enabled and not track.mute
        assert all((m.show_viewport, m.show_render) == (v, r) for m, v, r in modifier_flags)
        assert len(bpy.data.objects) == object_count
    assert not list(Path(folder).rglob('*.png'))
    for manifest in Path(folder).rglob('manifest.json'):
        assert 'physics_settings' not in json.loads(manifest.read_text())
assert scene.frame_current == frame
print('STATIC_RENDER_OK: 16 shots, legacy settings ignored, no workers/images, dynamics restored after success/error')
addon.unregister()
