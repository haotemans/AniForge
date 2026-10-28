"""AnimChar pose storage/replay and UI contracts; no network or rendering."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Matrix
from tools.blender_source import load_addon
addon = load_addon(); addon.register()
from mmd_lora_studio import animchar, workflow
from mmd_lora_studio.core import ShotSettings
result_temp = tempfile.TemporaryDirectory()
result_patch = patch.object(animchar, 'result_directory', return_value=Path(result_temp.name))
result_patch.start()

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.object.armature_add()
rig = bpy.context.object; rig.name = 'AnimChar replay fixture'
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones[0].name = 'センター'
spec = [('下半身', (0, 0, 1), (0, 0, 1.1), 'センター'),
        ('上半身', (0, 0, 1.1), (0, 0, 1.4), '下半身'),
        ('上半身2', (0, 0, 1.4), (0, 0, 1.6), '上半身'),
        ('首', (0, 0, 1.6), (0, 0, 1.7), '上半身2'),
        ('頭', (0, 0, 1.7), (0, 0, 1.9), '首')]
for prefix, sign in [('左', 1), ('右', -1)]:
    spec += [(prefix+'腕', (.2*sign, 0, 1.5), (.6*sign, 0, 1.5), '上半身2'),
             (prefix+'ひじ', (.6*sign, 0, 1.5), (.9*sign, 0, 1.5), prefix+'腕'),
             (prefix+'手首', (.9*sign, 0, 1.5), (1.05*sign, 0, 1.5), prefix+'ひじ'),
             (prefix+'足', (.1*sign, 0, 1), (.1*sign, 0, .55), '下半身'),
             (prefix+'ひざ', (.1*sign, 0, .55), (.1*sign, 0, .1), prefix+'足'),
             (prefix+'足首', (.1*sign, 0, .1), (.1*sign, -.1, .05), prefix+'ひざ')]
for name, head, tail, parent in spec:
    b = rig.data.edit_bones.new(name); b.head = head; b.tail = tail; b.parent = rig.data.edit_bones[parent]
bpy.ops.object.mode_set(mode='OBJECT')
settings = bpy.context.scene.ml_settings; settings.armature = rig
bpy.ops.mesh.primitive_cube_add(size=1)
body = bpy.context.object; body.parent = rig
proxy = rig.copy(); proxy.data = rig.data.copy(); bpy.context.scene.collection.objects.link(proxy)
proxy.pose.bones['センター'].matrix_basis = Matrix.Translation((.2, .1, .03)) @ Matrix.Rotation(.2, 4, 'Z')
proxy.pose.bones['右ひじ'].matrix_basis = Matrix.Rotation(.4, 4, 'X')
bpy.context.view_layer.update()
expected = {b.name: b.matrix.copy() for b in proxy.pose.bones}
original = {b.name: b.matrix_basis.copy() for b in rig.pose.bones}
shot = next(s for s in addon.recipe() if s['group'] == 'half_body' and s['index'] == 5)
name = animchar.bind(settings, shot, proxy, reference='synthetic native-result fixture')
assert 'センター' in addon.custom_presets(settings)['poses'][name]
assert all(b.matrix_basis == original[b.name] for b in rig.pose.bones)
target = bpy.data.objects.new('Existing controller', None); bpy.context.scene.collection.objects.link(target)
target.rotation_euler.z = .9
constraint = rig.pose.bones['頭'].constraints.new('COPY_ROTATION'); constraint.target = target
resolved = next(s for s in addon.dataset_plan(settings) if s.get('asset_slot') == 'half_body:5')
addon.pose_apply(ShotSettings(settings, resolved), resolved['pose'])
assert constraint.mute
assert all(abs(b.matrix[i][j]-expected[b.name][i][j]) < .0001 for b in rig.pose.bones for i in range(4) for j in range(4))
addon.pose_apply(settings, 'relaxed')
assert not constraint.mute
key = animchar.rig_key(rig); rig.location.x += 2
assert animchar.rig_key(rig) == key
rig.name = 'Renamed object'; rig.data.name = 'Renamed data block.001'
assert animchar.rig_key(rig) == key
saved_json = settings.presets_json
settings.presets_json = ''
recovered = animchar.restore_results(settings)
assert recovered['restored'] == 1 and recovered['total'] == 1
assert addon.custom_presets(settings)['poses'][name] == json.loads(saved_json)['poses'][name]
assert next(s for s in addon.dataset_plan(settings) if s.get('asset_slot') == 'half_body:5')['asset_status'] == 'CAPTURED'
with patch.object(animchar, 'modules', side_effect=AssertionError('Should reuse saved result without account call')):
    animchar.start_capture(bpy.context, settings, shot, 'synthetic native-result fixture')
assert not animchar.busy()
other_scene = bpy.data.scenes.new('New project scene')
other_scene.collection.objects.link(rig)
animchar.restore_scene_results(other_scene.ml_settings)
assert other_scene.ml_settings.armature == rig
assert len(addon.custom_presets(other_scene.ml_settings)['shot_assets']) == 1
rig.animation_data_create(); rig.animation_data.action = bpy.data.actions.new('Empty native Action')
assert not animchar.has_action_channels(rig)
rig.pose.bones['頭'].keyframe_insert(data_path='rotation_quaternion', frame=1)
assert animchar.has_action_channels(rig)
rig.animation_data_clear()
settings.preview_group = 'portrait'; settings.plan_index = 1
with patch.object(addon, 'RenderSession') as constructor:
    try:
        result = bpy.ops.ml.preview(use_plan_shot=True)
        assert result == {'CANCELLED'}
    except RuntimeError as exc:
        assert 'AnimChar' in str(exc)
    assert not constructor.called

class Layout:
    def __init__(self): self.calls = []
    def column(self, **kwargs): return self
    def row(self, **kwargs): return self
    def box(self): return self
    def label(self, **kwargs): self.calls.append(('label', kwargs))
    def prop(self, *args, **kwargs): self.calls.append(('prop', kwargs))
    def operator(self, name, **kwargs): self.calls.append((name, kwargs)); return SimpleNamespace()
    def template_icon(self, **kwargs): pass

before = settings.presets_json
layout = Layout()
with patch.object(workflow, 'preview_icon', return_value=0):
    workflow.draw_shots(layout, settings)
assert before == settings.presets_json
assert sum(name == 'ml.select_shot' for name, _ in layout.calls) == 12
assert any(name == 'ml.animchar' for name, _ in layout.calls)
print('ANIMCHAR_WORKFLOW_OK: root/ancestor replay, controller restoration, empty/action guard, missing-pose preflight, read-only 12-slot UI; no network/images')
addon.unregister()
from _bpy_restrict_state import RestrictBlend
with RestrictBlend():
    addon.register()
addon.migrate_settings()
assert len(addon.custom_presets(other_scene.ml_settings)['shot_assets']) == 1
addon.unregister()
print('PERSISTENT_RESULTS_OK: renamed data, cleared scene JSON, fresh scene, restricted startup registration; no network/images')
result_patch.stop(); result_temp.cleanup()
