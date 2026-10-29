"""Conservative material-role hints and non-destructive toon refinements."""
import math
import re

import bpy
from mathutils import Vector

ROLES = {'GENERIC', 'FACE', 'SKIN', 'FACE_SHADOW', 'HAIR', 'HAIR_HIGHLIGHT', 'EYE', 'DECAL', 'STOCKING'}


def material_role(source):
    if not source:
        return 'GENERIC'
    override = source.get('mmd_lora_role')
    if override in ROLES:
        return override
    name = re.sub(r'\.\d{3}$', '', source.name).casefold()
    if name in {'发影', '髮影', '目影', 'hair_shadow', 'eye_shadow'}:
        return 'FACE_SHADOW'
    if name in {'面', '面2', '颜', '顏', 'face', 'skin_face'}:
        return 'FACE'
    if re.fullmatch(r'(?:肌|体肌|skin|skin_body|body_skin)\d*', name):
        return 'SKIN'
    if name in {'袜', '袜+', '襪', '襪+', '丝袜', '絲襪', 'stocking', 'stockings', 'pantyhose', 'tights'}:
        return 'STOCKING'
    if name in {'目', '白目', '白2', '目白', '目hl', '目光', '目光2', '目+hi', '目++hi',
                '睫毛', '睫', '眉', '睫眉', '眉睫', 'eyes', 'iris'}:
        return 'EYE'
    if name in {'体肌+', '体下肌+', 'emo1', 'emo43', 'emo目', 'heng', 'cheek', 'skin_overlay', 'collar_decal'}:
        return 'DECAL'
    if name in {'前发+', '后发+', '前髮+', '後髮+', 'mi_bangs+', 'head+', 'hair_highlight'}:
        return 'HAIR_HIGHLIGHT'
    if name in {'发', '髮', '前发', '后发', '前髮', '後髮', '變色髮', '马尾', '双马尾',
                'mi_bangs', 'head', 'hair', 'bangs'}:
        return 'HAIR'
    return 'GENERIC'


def alpha_image(image):
    """MMD's depth==32 heuristic disables alpha on some 16-bit RGBA PNGs.

    Recover only the identified overlay's alpha, on a copied Blender image.
    The original image datablock and the file on disk remain untouched.
    """
    if image.alpha_mode != 'NONE':
        return image
    existing = next((img for img in bpy.data.images
                     if img.get('mmd_lora_alpha_source') == image.name), None)
    if existing:
        return existing
    result = image.copy()
    result.name = image.name[:44] + '_LoRAAlpha'
    result['mmd_lora_alpha_source'] = image.name
    result.alpha_mode = 'STRAIGHT'
    if result.source == 'FILE' and not result.packed_file:
        result.reload()
    return result


def face_light(tree, settings, minimum=None, role='Face'):
    geometry = tree.nodes.new('ShaderNodeNewGeometry'); geometry.location = (-760, -470)
    geometry.label = role+': smooth normals, independent of cast shadows'
    dot = tree.nodes.new('ShaderNodeVectorMath'); dot.operation = 'DOT_PRODUCT'; dot.location = (-540, -470)
    front = math.radians(settings.front)
    side = Vector((math.cos(front), math.sin(front), 0))
    forward = Vector((math.sin(front), -math.cos(front), 0))
    dot.inputs[1].default_value = (side * .3 + forward * .8 + Vector((0, 0, .5))).normalized()
    tree.links.new(geometry.outputs['Normal'], dot.inputs[0])
    ramp = tree.nodes.new('ShaderNodeValToRGB'); ramp.location = (-310, -470)
    ramp.label = role+' fill (no SDF texture required)'
    ramp.color_ramp.interpolation = 'EASE'
    ramp.color_ramp.elements[0].position = 0
    low = settings.face_fill if minimum is None else minimum
    ramp.color_ramp.elements[0].color = (low, low * .995, low * .99, 1)
    ramp.color_ramp.elements[1].position = .85
    ramp.color_ramp.elements[1].color = (1, 1, 1, 1)
    tree.links.new(dot.outputs['Value'], ramp.inputs[0])
    return ramp.outputs['Color']


def hair_color(tree, color):
    # A restrained cool lift plus a smooth rim keeps dark albedo from crushing
    # into a silhouette; it is not the source model's bright zig-zag overlay.
    lift = tree.nodes.new('ShaderNodeMixRGB'); lift.blend_type = 'ADD'
    lift.location = (210, 300); lift.label = 'Hair: retain dark strand detail'
    lift.inputs[0].default_value = 1
    lift.inputs[2].default_value = (.004, .0045, .006, 1)
    tree.links.new(color, lift.inputs[1])
    facing = tree.nodes.new('ShaderNodeLayerWeight'); facing.location = (-300, -720)
    power = tree.nodes.new('ShaderNodeMath'); power.operation = 'POWER'; power.location = (-80, -720)
    power.inputs[1].default_value = 3
    tree.links.new(facing.outputs['Facing'], power.inputs[0])
    rim = tree.nodes.new('ShaderNodeMixRGB'); rim.blend_type = 'ADD'; rim.location = (430, 300)
    rim.label = 'Hair: subtle broad sheen'
    rim.inputs[2].default_value = (.018, .021, .029, 1)
    tree.links.new(lift.outputs[0], rim.inputs[1])
    tree.links.new(power.outputs[0], rim.inputs[0])
    return rim.outputs[0]
