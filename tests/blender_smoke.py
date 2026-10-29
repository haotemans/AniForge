"""Run with Blender --background --factory-startup --python tests/blender_smoke.py."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from mathutils import Vector
import mmd_lora_studio as addon

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
addon.register()
scene = bpy.context.scene
settings = scene.ml_settings
bpy.ops.object.armature_add(location=(0, 0, 0))
rig = bpy.context.object; rig.name = 'SmokeRig'
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones[0].name = 'body'
for name, head, tail in [('頭', (0, 0, 1.5), (0, 0, 1.9)),
                         ('左腕', (.3, 0, 1.4), (.75, 0, 1.4)),
                         ('右腕', (-.3, 0, 1.4), (-.75, 0, 1.4)),
                         ('左ひじ', (.75, 0, 1.4), (1, 0, 1.4)),
                         ('右ひじ', (-.75, 0, 1.4), (-1, 0, 1.4)),
                         ('左手首', (1, 0, 1.4), (1.1, 0, 1.4)),
                         ('右手首', (-1, 0, 1.4), (-1.1, 0, 1.4)),
                         ('下半身', (0, 0, .9), (0, 0, 1.0)),
                         ('上半身', (0, 0, .9), (0, 0, 1.4))]:
    bone = rig.data.edit_bones.new(name); bone.head = head; bone.tail = tail
for child, parent in [('下半身', 'body'), ('上半身', 'body'), ('頭', '上半身'),
                      ('左腕', '上半身'), ('右腕', '上半身'), ('左ひじ', '左腕'), ('右ひじ', '右腕'),
                      ('左手首', '左ひじ'), ('右手首', '右ひじ')]:
    rig.data.edit_bones[child].parent = rig.data.edit_bones[parent]
bpy.ops.object.mode_set(mode='OBJECT')

def sphere(name, location, scale, color):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, location=location)
    obj = bpy.context.object; obj.name = name; obj.scale = scale; obj.parent = rig
    mat = bpy.data.materials.new(name); mat.diffuse_color = (*color, 1)
    obj.data.materials.append(mat)
    return obj

body = sphere('Coat', (0, 0, .9), (.32, .2, .58), (.1, .22, .65))
head = sphere('Face', (0, 0, 1.65), (.25, .22, .29), (.8, .55, .42))
for x in [-.09, .09]:
    sphere('Eye', (x, -.207, 1.7), (.033, .025, .04), (.02, .025, .035))
for x in [-.13, .13]:
    sphere('Boot', (x, 0, .25), (.1, .13, .3), (.04, .04, .06))
head.shape_key_add(name='Basis')
blink = head.shape_key_add(name='まばたき')
for point in blink.data:
    point.co.z *= .97
structural = head.shape_key_add(name='BodyProportion'); structural.value = .3
settings.armature = rig
settings.output = str(ROOT / 'test_output')
settings.resolution = 256
settings.trigger = 'smoke_character'
texture_dir = ROOT / 'test_output' / 'textures'
texture_dir.mkdir(parents=True, exist_ok=True)
tex = bpy.data.images.new('Coat test diffuse', width=4, height=4, alpha=True)
tex.generated_color = (.1, .22, .65, 1)
tex.filepath_raw = str(texture_dir / 'Coat_BaseColor.png')
tex.file_format = 'PNG'; tex.save()
settings.textures = str(texture_dir)
assert bpy.ops.ml.assign_textures() == {'FINISHED'}
coat_source = body.active_material
assert addon.diffuse_image(coat_source) is not None
source_image = addon.diffuse_image(coat_source)
source_image.filepath = '//missing/Coat_BaseColor.png'
assert bpy.ops.ml.textures() == {'FINISHED'}
assert Path(source_image.filepath).is_file()
# Regression: a real MMD material has multiple image roles and material alpha.
regression = bpy.data.materials.new('MMD regression'); regression.use_nodes = True
nodes = regression.node_tree.nodes
base = nodes.new('ShaderNodeTexImage'); base.name = 'mmd_base_tex'; base.image = source_image
toon = nodes.new('ShaderNodeTexImage'); toon.name = 'mmd_toon_tex'; toon.image = tex
group = bpy.data.node_groups.new('MMDAlphaRegression', 'ShaderNodeTree')
group.interface.new_socket(name='Alpha', in_out='INPUT', socket_type='NodeSocketFloat')
shader = nodes.new('ShaderNodeGroup'); shader.node_tree = group; shader.name = 'mmd_shader'
shader.inputs['Alpha'].default_value = .25
assert addon.diffuse_image(regression) == source_image
converted = addon.toon_material(regression, 'HSR')
assert converted.get('mmd_lora_preserved_layer')
assert abs(converted.node_tree.nodes['mmd_shader'].inputs['Alpha'].default_value - .25) < .0001
shader.inputs['Alpha'].default_value = 0
converted_hidden = addon.toon_material(regression, 'HSR')
assert converted_hidden.node_tree.nodes['mmd_shader'].inputs['Alpha'].default_value == 0
# Role profiles recover disabled PNG alpha on a private image datablock.
from mmd_lora_studio.material_profiles import material_role, alpha_image
source_image.alpha_mode = 'NONE'
restored_alpha = alpha_image(source_image)
assert restored_alpha != source_image and restored_alpha.alpha_mode == 'STRAIGHT'
assert source_image.alpha_mode == 'NONE'
assert alpha_image(source_image) == restored_alpha
source_image.alpha_mode = 'STRAIGHT'
regression.name = 'MI_Bangs+'
shader.inputs['Alpha'].default_value = 1
assert material_role(regression) == 'HAIR_HIGHLIGHT'
profiled_hair = addon.toon_material(regression, 'WUWA', settings)
assert profiled_hair['mmd_lora_role'] == 'HAIR_HIGHLIGHT'
assert abs(next(n for n in profiled_hair.node_tree.nodes if n.type == 'MATH').inputs[1].default_value - settings.hair_highlight_strength) < .0001
regression.name = '发影'
shader.inputs['Alpha'].default_value = .25
profiled_shadow = addon.toon_material(regression, 'ENDFIELD', settings)
assert abs(profiled_shadow.node_tree.nodes['mmd_shader'].inputs['Alpha'].default_value - .25 * settings.face_shadow_strength) < .0001
assert shader.inputs['Alpha'].default_value == .25
regression.name = '顏'
shader.inputs['Alpha'].default_value = 1
profiled_face = addon.toon_material(regression, 'GENSHIN', settings)
assert profiled_face['mmd_lora_role'] == 'FACE'
assert any(n.type == 'NEW_GEOMETRY' for n in profiled_face.node_tree.nodes)
regression.name = '目'
profiled_eye = addon.toon_material(regression, 'WUWA', settings)
assert profiled_eye['mmd_lora_role'] == 'EYE'
assert profiled_eye.surface_render_method == 'BLENDED'
assert not any(n.type == 'SHADERTORGB' for n in profiled_eye.node_tree.nodes)
zzz_eye = addon.toon_material(regression, 'ZZZ', settings)
assert zzz_eye.surface_render_method == 'DITHERED'
regression.name = '体肌+'
profiled_decal = addon.toon_material(regression, 'ZZZ', settings)
assert profiled_decal['mmd_lora_role'] == 'DECAL'
assert not any(n.type == 'SHADERTORGB' for n in profiled_decal.node_tree.nodes)
mesh_count = len(bpy.data.meshes)
for style in addon.STYLES:
    settings.style = style
    assert bpy.ops.ml.materials() == {'FINISHED'}
    assert body.active_material.name.endswith(style)
    assert any(n.type == 'BSDF_TRANSPARENT' for n in body.active_material.node_tree.nodes)

assert len(bpy.data.meshes) == mesh_count
addon.apply_tpose(settings)
addon.neutralize_face(rig, addon.model_meshes(settings))
assert blink.value == 0 and abs(structural.value-.3) < .0001
addon.unregister()
print('MMD_LORA_MATERIALS_OK: six styles, texture repair, private alpha copies; no images rendered')
