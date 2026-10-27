"""Pose/expression tools. Applying a preset is deterministic, not additive."""
import json
import math
import re

import bpy
from mathutils import Matrix, Quaternion, Vector

from .core import (BONES, MORPHS, EXPRESSION_CHANNELS, EXPRESSION_LABELS, POSE_LABELS,
                   POSE_DIRECTIONS, normalized_name, empty_library, validate_library)

_ENUM_CACHE = {}


def custom_presets(settings):
    return validate_library(json.loads(settings.presets_json)) if settings.presets_json else empty_library()


def store_presets(settings, library):
    settings.presets_json = json.dumps(validate_library(library), ensure_ascii=False)


def enum_presets(settings, category):
    labels = EXPRESSION_LABELS if category == 'expressions' else POSE_LABELS if category == 'poses' else {'AUTO': '当前相机参数'}
    custom = custom_presets(settings)[category]
    key = (category, tuple(custom))
    if key not in _ENUM_CACHE:
        _ENUM_CACHE[key] = [(name, label, '') for name, label in labels.items()] + [(name, name + '（自定义）', '') for name in custom if name not in labels]
    return _ENUM_CACHE[key]


def aliases(item):
    names = [item.name]
    mmd = getattr(item, 'mmd_bone', None)
    if mmd:
        names += [getattr(mmd, 'name_j', ''), getattr(mmd, 'name_e', '')]
    return names


def find_bone(rig, key, settings=None):
    override = custom_presets(settings)['bone_map'].get(key) if settings else None
    if override:
        return rig.pose.bones.get(override)
    expected = {normalized_name(n) for n in BONES[key]}
    return next((bone for bone in rig.pose.bones if expected & {normalized_name(n) for n in aliases(bone)}), None)


def shape_blocks(meshes):
    return [(obj, obj.data.shape_keys.key_blocks) for obj in meshes if obj.data.shape_keys]


def facial_key(name):
    all_aliases = {normalized_name(n) for groups in MORPHS.values() for group in groups for n in group}
    all_aliases |= {normalized_name(n) for channels in EXPRESSION_CHANNELS.values() for group, _ in channels for n in group}
    n = normalized_name(name)
    return n in all_aliases or n.startswith(('el_', 'eb_', 'jawopen', 'mouth', 'brow_', 'eye')) or any(t in name for t in ('口角', '口横', 'まばたき', 'ウィンク', '怒り目', '悲しい目'))


def expression_apply(settings, meshes, name):
    blocks = shape_blocks(meshes)
    saved = custom_presets(settings)['expressions']
    for obj, keys in blocks:
        custom_keys = {key for preset in saved.values() for key in preset.get(obj.name, {})}
        for key in keys[1:]:
            if facial_key(key.name) or key.name in custom_keys:
                key.value = 0
    changed = []
    strength = settings.expression_strength
    custom = saved.get(name)
    if custom is not None:
        for obj, keys in blocks:
            # Exact object name first; a single-mesh preset may be reused on a
            # differently named single-mesh character if shape-key names match.
            weights = custom.get(obj.name, {})
            if not weights and len(blocks) == 1 and len(custom) == 1:
                weights = next(iter(custom.values()))
            for key_name, value in weights.items():
                if key_name in keys:
                    keys[key_name].value = value * strength
                    if abs(value * strength) > .00001:
                        changed.append(obj.name + ':' + key_name)
    else:
        for candidates, value in EXPRESSION_CHANNELS.get(name, []):
            for obj, keys in blocks:
                indexed = {normalized_name(key.name): key for key in keys[1:]}
                key = next((indexed[normalized_name(n)] for n in candidates if normalized_name(n) in indexed), None)
                if key is not None:
                    key.value = max(key.value, value * strength)
                    if strength > 0:
                        changed.append(obj.name + ':' + key.name)
    return list(dict.fromkeys(changed))


def blend_basis(matrix, strength):
    location, rotation, scale = matrix.decompose()
    return Matrix.LocRotScale(location * strength, Quaternion().slerp(rotation, strength),
                             Vector((1, 1, 1)).lerp(scale, strength))


def finger_bones(rig):
    for bone in rig.pose.bones:
        for name in aliases(bone):
            n = normalized_name(name)
            finger = next((kind for kind, tokens in {'thumb': ('親指',), 'index': ('人指', '人差指'),
                          'middle': ('中指',), 'ring': ('薬指',), 'pinky': ('小指',)}.items()
                           if any(token in n for token in tokens)), None)
            english = re.search(r'(?:^|[_.: ]|hand|finger)(thumb|index|middle|ring|pinky|little)', n)
            if not finger and english:
                finger = 'pinky' if english.group(1) == 'little' else english.group(1)
            if finger:
                segment = re.search(r'[0-3]', n)
                yield bone, finger, int(segment.group()) if segment else 1
                break


def pose_apply(settings, name):
    rig = settings.armature
    custom = custom_presets(settings)['poses'].get(name)
    changed = []
    strength = settings.pose_strength
    if custom is not None:
        for bone_name, values in custom.items():
            bone = rig.pose.bones.get(bone_name)
            if bone:
                bone.matrix_basis = blend_basis(Matrix(values), strength)
                changed.append(bone.name)
    else:
        managed = {key for values in POSE_DIRECTIONS.values() for key in values} | {'head'}
        for key in managed:
            bone = find_bone(rig, key, settings)
            if bone:
                bone.rotation_mode = 'QUATERNION'
                bone.rotation_quaternion = Quaternion()
        bpy.context.view_layer.update()
        front = math.radians(settings.front)
        side = Vector((math.cos(front), math.sin(front), 0))
        forward = Vector((math.sin(front), -math.cos(front), 0))
        to_rig = rig.matrix_world.to_3x3().inverted()
        for key, direction in POSE_DIRECTIONS.get(name, {}).items():
            bone = find_bone(rig, key, settings)
            if bone:
                x, y, z = direction
                target = (to_rig @ (side*x + forward*y + Vector((0, 0, z)))).normalized()
                matrix = bone.matrix.copy()
                current = matrix.to_quaternion() @ Vector((0, 1, 0))
                swing = Quaternion().slerp(current.rotation_difference(target), strength)
                bone.matrix = Matrix.LocRotScale(matrix.translation, swing @ matrix.to_quaternion(), matrix.to_scale())
                bpy.context.view_layer.update()
                changed.append(bone.name)
    if settings.hand_pose != 'KEEP':
        for bone, finger, segment in finger_bones(rig):
            extended = settings.hand_pose == 'POINT' and finger == 'index' or settings.hand_pose == 'PEACE' and finger in {'index', 'middle'}
            angle = 0 if extended else (12 if settings.hand_pose == 'RELAXED' else 65)
            if finger == 'thumb':
                angle *= .35
            if segment == 0:
                angle = 0
            bone.rotation_mode = 'XYZ'
            bone.rotation_euler = (math.radians(angle) * strength, 0, 0)
            changed.append(bone.name)
    head = find_bone(rig, 'head', settings)
    if head and any(abs(v) > .00001 for v in settings.head_angles):
        pitch, yaw, roll = (math.radians(v) for v in settings.head_angles)
        front = math.radians(settings.front)
        axes = [Vector((math.cos(front), math.sin(front), 0)), Vector((0, 0, 1)), Vector((math.sin(front), -math.cos(front), 0))]
        to_rig = rig.matrix_world.to_quaternion().inverted()
        delta = Quaternion()
        for axis, angle in zip(axes, (pitch, yaw, roll)):
            delta = Quaternion(to_rig @ axis, angle) @ delta
        matrix = head.matrix.copy()
        head.matrix = Matrix.LocRotScale(matrix.translation, delta @ matrix.to_quaternion(), matrix.to_scale())
        changed.append(head.name)
    bpy.context.view_layer.update()
    return list(dict.fromkeys(changed))


def capture_expression(settings, meshes):
    return {obj.name: {key.name: key.value for key in keys[1:] if settings.capture_all_keys or facial_key(key.name)}
            for obj, keys in shape_blocks(meshes)}


def capture_pose(settings):
    return {bone.name: [list(row) for row in bone.matrix_basis] for bone in settings.armature.pose.bones}
