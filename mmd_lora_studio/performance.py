"""Pose/expression tools. Applying a preset is deterministic, not additive."""
import json
import math
import re

import bpy
from mathutils import Matrix, Quaternion, Vector

from .core import (BONES, MORPHS, EXPRESSION_CHANNELS, EXPRESSION_LABELS, POSE_LABELS,
                   POSE_DIRECTIONS, POSE_BODY, POSE_HEAD, POSE_HANDS, POSE_LEGS, SEATED_POSES,
                   normalized_name, empty_library, validate_library)

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


def bone_side(bone):
    names = [normalized_name(n) for n in aliases(bone)]
    for side, japanese, tokens in [('left', '左', ('left', '_l', '.l', ' l')),
                                   ('right', '右', ('right', '_r', '.r', ' r'))]:
        if any(japanese in n or any(token in n for token in tokens) for n in names):
            return side
    return None


def rotate_character(bone, angles, rig, front, strength):
    pitch, yaw, roll = (math.radians(v) * strength for v in angles)
    axes = [Vector((math.cos(front), math.sin(front), 0)), Vector((0, 0, 1)),
            Vector((math.sin(front), -math.cos(front), 0))]
    to_rig = rig.matrix_world.to_quaternion().inverted()
    delta = Quaternion()
    for axis, angle in zip(axes, (pitch, yaw, roll)):
        delta = Quaternion(to_rig @ axis, angle) @ delta
    matrix = bone.matrix.copy()
    bone.matrix = Matrix.LocRotScale(matrix.translation, delta @ matrix.to_quaternion(), matrix.to_scale())
    bpy.context.view_layer.update()


def place_hand(settings, side_name, target, pole, strength):
    """Two-bone swing in rig space, without adding constraints or Actions."""
    rig = settings.armature
    upper, lower, wrist = [find_bone(rig, side_name + '_' + key, settings) for key in ('arm', 'elbow', 'wrist')]
    if not all((upper, lower, wrist)):
        return []
    inv = rig.matrix_world.inverted()
    target = inv @ target
    pole = inv.to_3x3() @ pole
    origin = upper.head.copy()
    first, second = (lower.head-origin).length, (wrist.head-lower.head).length
    delta = target-origin
    if min(first, second, delta.length) < .00001:
        return []
    direction = delta.normalized()
    distance = min(max(delta.length, abs(first-second)+.00001), first+second-.00001)
    bend = pole-direction*pole.dot(direction)
    if bend.length < .00001:
        return []
    along = (first*first-second*second+distance*distance)/(2*distance)
    elbow_target = origin + direction*along + bend.normalized()*math.sqrt(max(0, first*first-along*along))
    for bone, goal in ((upper, elbow_target), (lower, origin+direction*distance)):
        matrix = bone.matrix.copy()
        current = matrix.to_quaternion() @ Vector((0, 1, 0))
        desired = (goal-matrix.translation).normalized()
        swing = Quaternion().slerp(current.rotation_difference(desired), strength)
        bone.matrix = Matrix.LocRotScale(matrix.translation, swing @ matrix.to_quaternion(), matrix.to_scale())
        bpy.context.view_layer.update()
    return [upper.name, lower.name]


SEATED_STATE = 'aniforge_seated_state'


def restore_seated(rig):
    state = json.loads(rig.get(SEATED_STATE, '{}'))
    for name, values in state.get('bones', {}).items():
        if name in rig.pose.bones:
            rig.pose.bones[name].matrix_basis = Matrix(values)
    for name, constraints in state.get('constraints', {}).items():
        bone = rig.pose.bones.get(name)
        if bone:
            for cname, mute in constraints.items():
                if cname in bone.constraints:
                    bone.constraints[cname].mute = mute
    if SEATED_STATE in rig:
        del rig[SEATED_STATE]
    bpy.context.view_layer.update()


def begin_leg_fk(settings):
    rig = settings.armature
    keys = ('center', 'left_leg', 'right_leg', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle')
    bones = {key: find_bone(rig, key, settings) for key in keys}
    missing = [key for key, bone in bones.items() if bone is None]
    if missing:
        raise ValueError('坐姿缺少骨骼：' + ', '.join(missing) + '；请在 bone_map 中指定对应骨骼。')
    state = {'bones': {b.name: [list(row) for row in b.matrix_basis] for b in bones.values()}, 'constraints': {}}
    # MMD rigs may place IK on the knee, ankle or an auxiliary deform chain.
    leg_names = {b.name for key, b in bones.items() if key != 'center'}
    for bone in rig.pose.bones:
        for constraint in bone.constraints:
            if constraint.type == 'IK':
                chain = [bone] + list(bone.parent_recursive[:max(0, constraint.chain_count-1)]) if constraint.chain_count else [bone]+list(bone.parent_recursive)
                if leg_names & {b.name for b in chain}:
                    state['constraints'].setdefault(bone.name, {})[constraint.name] = constraint.mute
                    constraint.mute = True
    rig[SEATED_STATE] = json.dumps(state)
    return bones


def apply_seated(settings, name, side, forward, to_rig):
    rig = settings.armature
    bones = begin_leg_fk(settings)
    strength = settings.pose_strength
    for key, bone in bones.items():
        if key != 'center':
            bone.rotation_mode = 'QUATERNION'; bone.rotation_quaternion = Quaternion()
    bpy.context.view_layer.update()
    # Ground follows the lowest rest foot endpoint; the support is derived from
    # actual limb lengths, so child/adult MMD proportions do not share a height.
    ground = min((rig.matrix_world @ b.tail).z for key, b in bones.items() if 'ankle' in key)
    for key, direction in POSE_LEGS[name].items():
        bone = bones[key]
        x, y, z = direction
        desired = (to_rig @ (side*x+forward*y+Vector((0, 0, z)))).normalized()
        matrix = bone.matrix.copy()
        current = matrix.to_quaternion() @ Vector((0, 1, 0))
        swing = Quaternion().slerp(current.rotation_difference(desired), strength)
        bone.matrix = Matrix.LocRotScale(matrix.translation, swing @ matrix.to_quaternion(), matrix.to_scale())
        bpy.context.view_layer.update()
    floor_now = min((rig.matrix_world @ b.tail).z for key, b in bones.items() if 'ankle' in key)
    center = bones['center']
    matrix = center.matrix.copy()
    matrix.translation += to_rig @ Vector((0, 0, ground-floor_now))
    center.matrix = matrix
    bpy.context.view_layer.update()
    return [b.name for b in bones.values()]


def pose_apply(settings, name):
    rig = settings.armature
    restore_seated(rig)
    custom = custom_presets(settings)['poses'].get(name)
    changed = []
    strength = settings.pose_strength
    if custom is not None:
        if any((bone := find_bone(rig, key, settings)) and bone.name in custom and Matrix(custom[bone.name]).to_quaternion().angle > .5
               for key in ('left_leg', 'right_leg')):
            begin_leg_fk(settings)
        for bone_name, values in custom.items():
            bone = rig.pose.bones.get(bone_name)
            if bone:
                bone.matrix_basis = blend_basis(Matrix(values), strength)
                changed.append(bone.name)
    else:
        managed = {key for values in POSE_DIRECTIONS.values() for key in values} | {'head', 'upper_body', 'upper_body2', 'lower_body'}
        for key in managed:
            bone = find_bone(rig, key, settings)
            if bone:
                bone.rotation_mode = 'QUATERNION'
                bone.rotation_quaternion = Quaternion()
        bpy.context.view_layer.update()
        front = math.radians(settings.front)
        for key, angles in POSE_BODY.get(name, {}).items():
            bone = find_bone(rig, key, settings)
            if bone:
                rotate_character(bone, angles, rig, front, strength)
                changed.append(bone.name)
        side = Vector((math.cos(front), math.sin(front), 0))
        forward = Vector((math.sin(front), -math.cos(front), 0))
        to_rig = rig.matrix_world.to_3x3().inverted()
        if name in SEATED_POSES:
            changed += apply_seated(settings, name, side, forward, to_rig)
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
        # Face gestures need a hand target scaled to this rig, rather than
        # assuming that every character has the same arm/head proportions.
        head = find_bone(rig, 'head', settings)
        left, right = find_bone(rig, 'left_arm', settings), find_bone(rig, 'right_arm', settings)
        if head and left and right:
            head_point = rig.matrix_world @ head.head
            shoulder_span = (rig.matrix_world.to_3x3() @ (left.head-right.head)).length
            unit = max(shoulder_span, .0001)
            # Offsets are measured in shoulder spans from the head joint.
            targets = {'peace_face': {'right': (-.85, .45, .2)},
                       'thinking': {'right': (-.12, .65, -.15)},
                       'salute': {'right': (-.6, .35, .5)},
                       'shy': {'left': (.28, .65, -.7), 'right': (-.28, .65, -.7)},
                       'cat_paws': {'left': (.65, .7, -.55), 'right': (-.65, .7, -.55)}}
            for hand, (x, y, z) in targets.get(name, {}).items():
                target = head_point + unit*(side*x+forward*y+Vector((0, 0, z)))
                pole = side*(1 if hand == 'left' else -1) + Vector((0, 0, -1)) + forward*.2
                changed += place_hand(settings, hand, target, pole, strength)
    if settings.hand_pose != 'KEEP':
        fingers = list(finger_bones(rig))
        for bone, finger, segment in fingers:
            mode = settings.hand_pose
            if mode == 'AUTO':
                mode = POSE_HANDS.get(name, {}).get(bone_side(bone), 'RELAXED')
            extended = mode == 'OPEN' or mode == 'POINT' and finger == 'index' or mode == 'PEACE' and finger in {'index', 'middle'}
            angle = 0 if extended else (12 if mode == 'RELAXED' else 65)
            if finger == 'thumb':
                angle *= .35
            if segment == 0:
                angle = 0
            bone.rotation_mode = 'XYZ'
            bone.rotation_euler = (math.radians(angle) * strength, 0, 0)
            if mode == 'PEACE' and finger in {'index', 'middle'} and segment == 1:
                same_hand = [(b, f) for b, f, i in fingers if i == 1 and bone_side(b) == bone_side(bone)]
                index = next((b for b, f in same_hand if f == 'index'), None)
                pinky = next((b for b, f in same_hand if f == 'pinky'), None)
                if index and pinky:
                    across = bone.bone.matrix_local.to_3x3().inverted() @ (index.bone.head_local-pinky.bone.head_local)
                    across.y = 0
                    if across.length > .00001:
                        spread = .16 if finger == 'index' else -.13
                        target = (Vector((0, 1, 0)) + across.normalized()*spread*strength).normalized()
                        bone.rotation_euler = Vector((0, 1, 0)).rotation_difference(target).to_euler('XYZ')
            changed.append(bone.name)
    head = find_bone(rig, 'head', settings)
    head_angles = settings.head_angles
    if not custom and not any(abs(v) > .00001 for v in head_angles):
        head_angles = POSE_HEAD.get(name, head_angles)
    if head and any(abs(v) > .00001 for v in head_angles):
        rotate_character(head, head_angles, rig, math.radians(settings.front), strength)
        changed.append(head.name)
    bpy.context.view_layer.update()
    return list(dict.fromkeys(changed))


def capture_expression(settings, meshes):
    return {obj.name: {key.name: key.value for key in keys[1:] if settings.capture_all_keys or facial_key(key.name)}
            for obj, keys in shape_blocks(meshes)}


def capture_pose(settings):
    return {bone.name: [list(row) for row in bone.matrix_basis] for bone in settings.armature.pose.bones}
