"""Head/leg detail framing ignores unrelated geometry; no images rendered."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
import mmd_lora_studio as addon
from mmd_lora_studio.camera import configure_camera, bone_region_points, face_bounds, head_region

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
addon.register()
settings = bpy.context.scene.ml_settings
bpy.ops.object.armature_add()
rig = settings.armature = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones[0].name = '下半身'
rig.data.edit_bones[0].head = (0, 0, 1)
rig.data.edit_bones[0].tail = (0, 0, 1.1)
for name, head, tail, parent in [
        ('頭', (0, 0, 1.55), (0, 0, 1.85), '下半身'),
        ('左足', (.12, 0, .96), (.12, 0, .5), '下半身'),
        ('右足', (-.12, 0, .96), (-.12, 0, .5), '下半身'),
        ('左足首', (.12, 0, .16), (.12, -.2, .05), '左足'),
        ('右足首', (-.12, 0, .16), (-.12, -.2, .05), '右足')]:
    bone = rig.data.edit_bones.new(name); bone.head = head; bone.tail = tail
    bone.parent = rig.data.edit_bones[parent]
bpy.ops.object.mode_set(mode='OBJECT')


def part(name, low, high, bone, role='GENERIC'):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object; obj.name = name; obj.parent = rig
    obj.location = (Vector(low)+Vector(high))*.5
    obj.scale = Vector(high)-Vector(low)
    group = obj.vertex_groups.new(name=bone)
    group.add(list(range(len(obj.data.vertices))), 1, 'REPLACE')
    material = bpy.data.materials.new(name); material['mmd_lora_role'] = role
    obj.data.materials.append(material)
    return obj


face = part('Face', (-.12, -.15, 1.55), (.12, .10, 1.84), '頭', 'FACE')
hair = part('Hair cap', (-.18, -.16, 1.54), (.18, .20, 1.94), '頭', 'HAIR')
ornament = part('Head ornament', (-.20, 0, 1.92), (-.12, .08, 2.08), '頭')
tail = part('Long hair', (.4, .1, .1), (.7, .2, 1.4), '頭', 'HAIR')
skirt = part('Wide skirt', (-1.3, -.3, .2), (1.3, .3, 1.2), '下半身')
prop = part('Unrelated prop', (1.8, -.1, 0), (2, .1, 2), '下半身')
legs = []
for sign, prefix in ((1, '左'), (-1, '右')):
    x = sign*.12
    legs.append(part(prefix+'leg', (x-.07, -.06, .14), (x+.07, .07, .96), prefix+'足'))
    legs.append(part(prefix+'shoe', (x-.10, -.32, 0), (x+.10, .12, .16), prefix+'足首'))
# A bone-parented shoe ornament must also fit even without vertex groups.
shoe_detail = part('Heel detail', (.08, .10, .02), (.16, .18, .08), '左足首')
shoe_detail.vertex_groups.clear()
bpy.context.view_layer.update()
shoe_world = shoe_detail.matrix_world.copy()
shoe_detail.parent_type = 'BONE'; shoe_detail.parent_bone = '左足首'
bpy.context.view_layer.update()
shoe_detail.matrix_world = shoe_world
bpy.context.view_layer.update()
meshes = addon.model_meshes(settings)
camera = addon.ensure_camera(bpy.context.scene)
settings.margin = 1.10; settings.resolution = 512

dg = bpy.context.evaluated_depsgraph_get()
region = head_region(meshes, dg, rig, Vector((0, 0, 1.55)), face_bounds(meshes, dg), 1.7)
assert region[0].x >= -.201 and region[1].x <= .181
assert region[0].z >= 1.53 and abs(region[1].z-2.08) < .001
leg_points = bone_region_points(meshes, dg, rig, ('left_leg', 'right_leg'), top=1)
assert min(p.z for p in leg_points) <= .001
assert max(abs(p.x) for p in leg_points) < .3, [(tuple(p)) for p in leg_points]

checks = 0
for projection in ('ORTHO', 'PERSP'):
    settings.camera_projection = projection
    for aspect in ('SQUARE', 'PORTRAIT', 'LANDSCAPE'):
        settings.camera_aspect = aspect
        for group in ('portrait', 'lower_body'):
            for shot in (s for s in addon.recipe() if s['group'] == group):
                meta = configure_camera(bpy.context, settings, meshes, camera, shot)
                for point in meta['roi']:
                    uv = world_to_camera_view(bpy.context.scene, camera, Vector(point))
                    assert -.001 <= uv.x <= 1.001 and -.001 <= uv.y <= 1.001 and uv.z > 0
                if group == 'portrait':
                    assert min(p[2] for p in meta['roi']) >= 1.53
                    assert abs(max(p[2] for p in meta['roi'])-2.08) < .001
                    assert max(abs(p[0]) for p in meta['roi']) <= .201
                else:
                    assert min(p[2] for p in meta['roi']) <= .001
                    assert .96 < max(p[2] for p in meta['roi']) < 1.02
                    assert max(abs(p[0]) for p in meta['roi']) < .3
                checks += 1

# Weight-less imports retain a material-based head fallback and central legs.
for obj in meshes:
    obj.vertex_groups.clear()
shoe_detail.parent_type = 'OBJECT'
bpy.context.view_layer.update()
for group in ('portrait', 'lower_body'):
    meta = configure_camera(bpy.context, settings, meshes, camera,
                            next(s for s in addon.recipe() if s['group'] == group))
    assert max(abs(p[0]) for p in meta['roi']) < .5

addon.unregister()
print('DETAIL_FRAMING_OK:', checks, 'fits; head ornaments and complete shoes retained; long hair/skirt/props excluded from fit; zero renders')
