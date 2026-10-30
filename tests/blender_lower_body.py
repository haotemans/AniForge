"""Exclude arm fragments using private geometry; restore after errors, no renders."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import mmd_lora_studio as addon
from mmd_lora_studio.static_pose import lower_body_arm_mask

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
addon.register()
bpy.ops.object.armature_add()
rig = bpy.context.object; settings = bpy.context.scene.ml_settings; settings.armature = rig
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones[0].name = '下半身'
for prefix, sign in (('左', 1), ('右', -1)):
    arm = rig.data.edit_bones.new(prefix+'腕'); arm.head = (sign*.2, 0, 1.4); arm.tail = (sign*.6, 0, 1.1)
    arm.parent = rig.data.edit_bones['下半身']
    wrist = rig.data.edit_bones.new(prefix+'手首'); wrist.head = arm.tail; wrist.tail = (sign*.7, 0, 1)
    wrist.parent = arm
    leg = rig.data.edit_bones.new(prefix+'足'); leg.head = (sign*.1, 0, 1); leg.tail = (sign*.1, 0, .1)
    leg.parent = rig.data.edit_bones['下半身']
bpy.ops.object.mode_set(mode='OBJECT')


def cubes(name, parts):
    vertices = []; faces = []; groups = []
    for x, z, bone in parts:
        offset = len(vertices)
        vertices.extend((x+dx, dy, z+dz) for dx in (-.04, .04) for dy in (-.04, .04) for dz in (-.04, .04))
        faces.extend(tuple(offset+i for i in face) for face in
                     ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)))
        groups.append((bone, list(range(offset, offset+8))))
    mesh = bpy.data.meshes.new(name); mesh.from_pydata(vertices, [], faces); mesh.update()
    obj = bpy.data.objects.new(name, mesh); bpy.context.scene.collection.objects.link(obj); obj.parent = rig
    for bone, indices in groups:
        group = obj.vertex_groups.get(bone) or obj.vertex_groups.new(name=bone)
        group.add(indices, 1, 'REPLACE')
    return obj


body = cubes('Mixed body', [(.6, 1, '左手首'), (-.6, 1, '右手首'), (.1, .5, '左足'), (-.1, .5, '右足')])
body.shape_key_add(name='Basis'); key = body.shape_key_add(name='BodyProportion'); key.value = .3
shared = body.copy(); shared.data = body.data; bpy.context.scene.collection.objects.link(shared)
hand = cubes('Separate glove', [(.6, 1, '左手首')])
accessory = cubes('Wrist accessory', [(.6, 1, '左手首')]); accessory.vertex_groups.clear()
accessory.parent_type = 'BONE'; accessory.parent_bone = '左手首'
hidden = cubes('Already hidden glove', [(.6, 1, '左手首')]); hidden.hide_render = True
meshes = [body, shared, hand, accessory, hidden]
bpy.context.view_layer.update()


def snapshot():
    return [(obj.data, obj.hide_render, [g.name for g in obj.vertex_groups],
             [m.name for m in obj.modifiers],
             [[(g.group, g.weight) for g in v.groups] for v in obj.data.vertices]) for obj in meshes]


before = snapshot(); counts = (len(bpy.data.meshes), len(bpy.data.shape_keys))
for fail in (False, True):
    try:
        with lower_body_arm_mask(settings, meshes) as info:
            assert info == {'excluded_arm_vertices': 40, 'hidden_arm_objects': 2}
            assert body.data != before[0][0] and shared.data != before[1][0]
            assert body.data != shared.data
            assert len(body.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons) == 12
            assert len(shared.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons) == 12
            assert hand.hide_render and accessory.hide_render and hidden.hide_render
            assert before[0][0] == before[1][0]
            assert len(before[0][0].polygons) == 24
            assert abs(body.data.shape_keys.key_blocks['BodyProportion'].value-.3) < 1e-6
            if fail:
                raise RuntimeError('Forced render failure')
    except RuntimeError:
        assert fail
    assert snapshot() == before
    assert (len(bpy.data.meshes), len(bpy.data.shape_keys)) == counts
    assert abs(key.value-.3) < 1e-6
with lower_body_arm_mask(settings, meshes, enabled=False) as info:
    assert snapshot() == before and info == {'excluded_arm_vertices': 0, 'hidden_arm_objects': 0}

addon.unregister()
print('LOWER_BODY_MASK_OK: shared meshes, skin weights, shape keys, bone attachments and hidden objects restored after success/error; other framings unchanged; zero renders')
