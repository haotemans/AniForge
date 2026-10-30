"""Static A pose and natural face only. No selectable pose/expression presets."""
import math
from contextlib import contextmanager

import bpy
from mathutils import Matrix, Vector

from .core import BONES, normalized_name


def aliases(item):
    mmd = getattr(item, 'mmd_bone', None)
    return [item.name] + ([getattr(mmd, 'name_j', ''), getattr(mmd, 'name_e', '')] if mmd else [])


def find_bone(rig, key, settings=None):
    names = list(BONES[key])
    # MMD Tools' "rename bones" option turns 左腕 into 腕.L. This must
    # still work when its RNA metadata is unavailable in a saved project.
    names += [n[1:]+suffix for n in BONES[key] for prefix, suffix in (('左', '.L'), ('右', '.R')) if n.startswith(prefix)]
    expected = {normalized_name(n) for n in names}
    return next((b for b in rig.pose.bones if expected & {normalized_name(n) for n in aliases(b)}), None)


def shape_blocks(meshes):
    return [(obj, obj.data.shape_keys.key_blocks) for obj in meshes if obj.data.shape_keys]


def has_animation(rig, meshes):
    """Reject active Action/NLA channels without unlinking the user's animation."""
    def animated(owner):
        data = owner.animation_data
        if not data:
            return False
        if data.action:
            action = data.action
            if hasattr(action, 'fcurves'):
                if action.fcurves:
                    return True
            elif any(bag.fcurves for layer in action.layers for strip in layer.strips
                     for bag in getattr(strip, 'channelbags', ())):
                return True
        return any(not track.mute and any(not strip.mute for strip in track.strips) for track in data.nla_tracks)
    return any(animated(owner) for owner in [rig, *meshes, *(obj.data.shape_keys for obj in meshes if obj.data.shape_keys)])


def face_key_names(rig):
    """Use MMD facial categories when available; leave clothing/shape morphs intact."""
    root = rig
    while root and getattr(root, 'mmd_type', '') != 'ROOT':
        root = root.parent
    data = getattr(root, 'mmd_root', None)
    return {m.name for category in ('vertex_morphs', 'group_morphs')
            for m in getattr(data, category, ()) if m.category in {'EYE', 'EYEBROW', 'MOUTH'}}


def facial_key(name):
    name = normalized_name(name)
    return name in {'あ', 'い', 'う', 'え', 'お', 'a', 'i', 'u', 'e', 'o', 'smile', 'blink', 'wink',
                    'happy', 'angry', 'sad', 'surprised', '笑い', 'にこり', 'にやり', '困る', '怒り',
                    '悲しい', 'びっくり', '驚き', 'まばたき', '瞬き', 'ウィンク', 'ウインク'} or name.startswith(
                        ('el_', 'eb_', 'jawopen', 'mouth', 'brow_', 'eye')) or any(
                            token in name for token in ('口角', '口横', 'まばたき', 'ウィンク', '怒り目', '悲しい目'))


def neutralize_face(rig, meshes):
    names = face_key_names(rig)
    cleared = []
    for obj, keys in shape_blocks(meshes):
        facial = [key for key in keys[1:] if key.name in names or facial_key(key.name)]
        paths = {key.path_from_id('value') for key in facial}
        animation = obj.data.shape_keys.animation_data
        if animation:
            for driver in animation.drivers:
                if driver.data_path in paths:
                    driver.mute = True
        for key in facial:
            key.value = 0
            cleared.append(obj.name+':'+key.name)
    return cleared


def apply_apose(settings):
    """Reset gestures and align both arms 45 degrees down, preserving bone roll."""
    rig = settings.armature
    missing = [key for key in ('left_arm', 'right_arm', 'left_elbow', 'right_elbow') if find_bone(rig, key) is None]
    if missing:
        raise ValueError('无法定位 A Pose 所需手臂骨骼：'+', '.join(missing))
    rig.data.pose_position = 'POSE'
    for bone in rig.pose.bones:
        for constraint in bone.constraints:
            constraint.mute = True
        bone.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    angle = math.radians(settings.front)
    side = Vector((math.cos(angle), math.sin(angle), 0))
    to_rig = rig.matrix_world.to_3x3().inverted()
    changed = []
    for hand, sign in (('left', 1), ('right', -1)):
        direction = (to_rig @ (side*sign + Vector((0, 0, -1)))).normalized()
        for part in ('arm', 'elbow', 'wrist'):
            bone = find_bone(rig, hand+'_'+part)
            if bone is None:
                continue
            matrix = bone.matrix.copy()
            current = matrix.to_quaternion() @ Vector((0, 1, 0))
            swing = current.rotation_difference(direction)
            bone.matrix = Matrix.LocRotScale(matrix.translation, swing @ matrix.to_quaternion(), matrix.to_scale())
            bpy.context.view_layer.update()
            changed.append(bone.name)
    return changed


@contextmanager
def lower_body_arm_mask(settings, meshes, enabled=True):
    """Exclude arm fragments only while drawing a lower-body image.

    Mixed body meshes use private data and a temporary modifier. Neither the
    source mesh/weights nor the shared A pose is edited. Always unwind on error.
    """
    info = {'excluded_arm_vertices': 0, 'hidden_arm_objects': 0}
    if not enabled:
        yield info
        return
    rig = settings.armature
    roots = [b for key in ('left_arm', 'right_arm', 'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist')
             if (b := find_bone(rig, key)) is not None]
    names = {b.name for root in roots for b in (root, *root.children_recursive)}
    changes = []
    try:
        for obj in meshes:
            if obj.hide_render:
                continue
            parent = obj
            attached = False
            while parent:
                if parent.parent == rig and parent.parent_type == 'BONE' and parent.parent_bone in names:
                    attached = True
                    break
                parent = parent.parent
            indices = {g.index for g in obj.vertex_groups if g.name in names}
            vertices = [v.index for v in obj.data.vertices if
                        sum(g.weight for g in v.groups if g.group in indices) >= .5]
            if not attached and not vertices:
                continue
            state = {'object': obj, 'hidden': obj.hide_render, 'source': obj.data,
                     'copy': None, 'group': None, 'modifier': None}
            changes.append(state)
            info['excluded_arm_vertices'] += len(vertices)
            if attached or len(vertices) == len(obj.data.vertices):
                obj.hide_render = True
                info['hidden_arm_objects'] += 1
                continue
            if obj.mode == 'EDIT':
                raise ValueError('下半身出图前请先退出网格编辑模式。')
            duplicate = state['copy'] = obj.data.copy()
            duplicate.name = obj.data.name + '_AniForgeLowerBody'
            obj.data = duplicate
            group = state['group'] = obj.vertex_groups.new(name='AniForge excluded arms')
            group.add(vertices, 1, 'REPLACE')
            mask = state['modifier'] = obj.modifiers.new('AniForge lower-body crop', 'MASK')
            mask.vertex_group = group.name; mask.invert_vertex_group = True
            mask.threshold = .5; mask.use_smooth = False
        bpy.context.view_layer.update()
        yield info
    finally:
        for state in reversed(changes):
            obj = state['object']
            if state['modifier'] is not None:
                obj.modifiers.remove(state['modifier'])
            if state['group'] is not None:
                obj.vertex_groups.remove(state['group'])
            obj.data = state['source']
            obj.hide_render = state['hidden']
            if state['copy'] is not None:
                bpy.data.meshes.remove(state['copy'])
        bpy.context.view_layer.update()


class CharacterState:
    """Restore the user's rig and face after rendering, cancellation or failure."""
    def __init__(self, rig, meshes):
        self.rig = rig
        self.position = rig.data.pose_position
        self.pose = [(b, b.rotation_mode, b.matrix_basis.copy()) for b in rig.pose.bones]
        self.constraints = [(c, c.mute) for b in rig.pose.bones for c in b.constraints]
        self.keys = [(k, k.value) for _, keys in shape_blocks(meshes) for k in keys]
        self.drivers = [(d, d.mute) for obj in meshes if obj.data.shape_keys
                        if (animation := obj.data.shape_keys.animation_data) for d in animation.drivers]

    def restore(self):
        self.rig.data.pose_position = self.position
        for bone, mode, matrix in self.pose:
            bone.rotation_mode = mode
            bone.matrix_basis = matrix
        for constraint, mute in self.constraints:
            constraint.mute = mute
        for key, value in self.keys:
            key.value = value
        for driver, mute in self.drivers:
            driver.mute = mute
        bpy.context.view_layer.update()
