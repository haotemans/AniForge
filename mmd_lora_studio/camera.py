"""Automatic framing with explicit lens, aspect, orbit and composition controls."""
import math

from mathutils import Vector, Quaternion
from .material_profiles import material_role
from .core import ShotSettings
from .performance import find_bone

CAMERA_FIELDS = ('camera_projection', 'camera_lens', 'camera_azimuth', 'camera_elevation', 'camera_roll',
                 'camera_zoom', 'camera_shift_x', 'camera_shift_y', 'camera_target_offset', 'camera_aspect',
                 'portrait_focus', 'margin', 'preview_group')


def resolution(settings):
    side = settings.resolution
    return (int(side*.75), side) if settings.camera_aspect == 'PORTRAIT' else (side, int(side*.75)) if settings.camera_aspect == 'LANDSCAPE' else (side, side)


def capture_camera(settings):
    return {name: list(value) if hasattr(value := getattr(settings, name), '__len__') and not isinstance(value, str) else value
            for name in CAMERA_FIELDS}


def camera_apply(settings, value):
    for name, data in value.items():
        if name in CAMERA_FIELDS:
            setattr(settings, name, data)


def box_points(lo, hi):
    return [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]


def model_bounds(meshes, depsgraph):
    points = [obj.evaluated_get(depsgraph).matrix_world @ Vector(corner) for obj in meshes if not obj.hide_render
              for corner in obj.evaluated_get(depsgraph).bound_box]
    if not points:
        raise ValueError('没有可渲染的角色网格。')
    lo = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    hi = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    if hi.z - lo.z < .0001:
        raise ValueError('角色高度无效。')
    return lo, hi


def face_bounds(meshes, depsgraph):
    points = []
    for obj in meshes:
        if obj.hide_render:
            continue
        slots = {index for index, slot in enumerate(obj.material_slots) if slot.material and
                 (slot.material.get('mmd_lora_role') == 'FACE' or material_role(slot.material) == 'FACE')}
        evaluated = obj.evaluated_get(depsgraph)
        indices = {v for poly in evaluated.data.polygons if poly.material_index in slots for v in poly.vertices}
        points += [evaluated.matrix_world @ evaluated.data.vertices[index].co for index in indices]
    if not points:
        return None
    lo = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    hi = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    return (lo, hi) if hi.z - lo.z > .00001 else None


def configure_camera(context, settings, meshes, camera, shot):
    settings = ShotSettings(settings, shot)
    scene = context.scene
    lo, hi = model_bounds(meshes, context.evaluated_depsgraph_get())
    height = hi.z - lo.z
    center = (lo + hi) / 2
    group = shot['group']
    focus = shot.get('focus', '')
    width_pixels, height_pixels = resolution(settings)
    aspect = width_pixels / height_pixels
    front = math.radians(settings.front)
    side = Vector((math.cos(front), math.sin(front), 0))
    forward = Vector((math.sin(front), -math.cos(front), 0))
    rig = settings.armature
    evaluated_rig = rig.evaluated_get(context.evaluated_depsgraph_get()) if rig else None
    def joint(key):
        bone = find_bone(rig, key, settings) if rig else None
        return evaluated_rig.matrix_world @ evaluated_rig.pose.bones[bone.name].head if bone else None
    head = joint('head')
    torso = joint('upper_body')
    hips = joint('lower_body')
    body_height = max((head.z-lo.z) + height*.07, height*.5) if head else height
    body_center = torso if torso is not None else center
    # Approximate anatomical zones. These locate clothing on the wearer;
    # no claim is made that a particular model actually wears a collar/skirt.
    regions = {'COLLAR': (.82, .22, 0), 'CHEST': (.72, .30, 0),
               'SLEEVE_LEFT': (.68, .42, .22), 'SLEEVE_RIGHT': (.68, .42, -.22),
               'WAIST': (.54, .28, 0), 'HIPS': (.43, .34, 0), 'HEM': (.25, .47, 0),
               'BACK_UPPER': (.73, .39, 0), 'BACK_WAIST': (.5, .34, 0), 'SHOES': (.08, .28, 0)}
    if group == 'full_body':
        points = box_points(lo, hi)
        target = center.copy()
    elif group == 'portrait' and (face := face_bounds(meshes, context.evaluated_depsgraph_get())):
        face_lo, face_hi = face
        face_height = face_hi.z - face_lo.z
        target = (face_lo + face_hi) / 2 + Vector((0, 0, face_height * .1))
        multiplier = 1.55 if (focus or settings.portrait_focus) == 'FACE' else 2.6
        size = max(face_height * multiplier, (face_hi.x-face_lo.x) * 1.5 / aspect)
        roi_lo = target - Vector((size*aspect*.5, (face_hi.y-face_lo.y)*.5, size*.5))
        roi_hi = target + Vector((size*aspect*.5, (face_hi.y-face_lo.y)*.5, size*.5))
        points = box_points(roi_lo, roi_hi)
    elif group == 'half_body' and head is not None and hips is not None:
        top = head + Vector((0, 0, body_height*.15))
        bottom = hips - Vector((0, 0, body_height*.05))
        target = (top+bottom)*.5
        size = max(top.z-bottom.z, body_height*.35)
        half = Vector((size*aspect*.5, body_height*.18, size*.5))
        points = box_points(target-half, target+half)
    elif group == 'outfit_detail' and focus in regions:
        vertical, fraction, lateral = regions[focus]
        target = Vector((body_center.x, body_center.y, lo.z + body_height*vertical)) + side * body_height*lateral
        size = body_height*fraction
        if hips is not None and head is not None:
            chest = joint('upper_body2')
            chest = chest if chest is not None else head.lerp(hips, .4)
            anchors = {'COLLAR': head.lerp(chest, .2), 'CHEST': chest.lerp(hips, .25),
                       'WAIST': hips.lerp(chest, .3), 'HIPS': hips-Vector((0, 0, body_height*.06)),
                       'BACK_UPPER': chest.lerp(hips, .2), 'BACK_WAIST': hips.lerp(chest, .25)}
            if focus in anchors:
                target = anchors[focus].copy()
            if focus.startswith('SLEEVE_'):
                arm = joint('left_arm' if focus == 'SLEEVE_LEFT' else 'right_arm')
                wrist = joint('left_wrist' if focus == 'SLEEVE_LEFT' else 'right_wrist')
                if arm is not None and wrist is not None:
                    target = arm.lerp(wrist, .5)
                    size = max(size, (wrist-arm).length*1.25)
            if focus == 'HEM':
                knees = [p for key in ('left_knee', 'right_knee') if (p := joint(key)) is not None]
                if knees:
                    target = hips.lerp(sum(knees, Vector())/len(knees), .8)
            if focus == 'SHOES':
                ankles = [p for key in ('left_ankle', 'right_ankle') if (p := joint(key)) is not None]
                if ankles:
                    target = sum(ankles, Vector())/len(ankles)
        half = Vector((size*aspect*.5, body_height*.16, size*.5))
        points = box_points(target-half, target+half)
    else:
        fraction, vertical = {'portrait': (.29, .86), 'half_body': (.46, .78), 'outfit_detail': (.48, .49)}[group]
        target = Vector((center.x, center.y, lo.z + height * vertical))
        size = height * fraction
        width = min(hi.x-lo.x, size * aspect)
        points = box_points(target-Vector((width*.5, (hi.y-lo.y)*.5, size*.5)), target+Vector((width*.5, (hi.y-lo.y)*.5, size*.5)))
    included = []
    for key in shot.get('include_hands', []):
        point = joint(key)
        if point is not None:
            radius = body_height*.065
            points += box_points(point-Vector((radius, radius, radius)), point+Vector((radius, radius, radius)))
            included.append(key)
    if included:
        target = Vector(tuple((min(p[i] for p in points)+max(p[i] for p in points))*.5 for i in range(3)))
    x, y, z = settings.camera_target_offset
    target += height * (side*x + forward*y + Vector((0, 0, z)))
    azimuth = math.radians(settings.front + settings.camera_azimuth + shot.get('azimuth', 0))
    elevation = math.radians(shot.get('elevation', settings.camera_elevation))
    direction = Vector((math.sin(azimuth)*math.cos(elevation), -math.cos(azimuth)*math.cos(elevation), math.sin(elevation)))
    rotation = (-direction).to_track_quat('-Z', 'Y') @ Quaternion(Vector((0, 0, 1)), math.radians(settings.camera_roll))
    camera.rotation_mode = 'QUATERNION'
    camera.rotation_quaternion = rotation
    data = camera.data
    data.type = settings.camera_projection
    data.lens = settings.camera_lens
    data.sensor_fit = 'HORIZONTAL'
    data.sensor_width = 36
    data.sensor_height = 24
    data.shift_x = settings.camera_shift_x
    data.shift_y = settings.camera_shift_y
    data.ortho_scale = 1
    scene.render.resolution_x, scene.render.resolution_y = resolution(settings)
    local = [rotation.inverted() @ (point-target) for point in points]
    frame = data.view_frame(scene=scene)
    if data.type == 'PERSP':
        frame = [point / -point.z for point in frame]
    left, right = min(p.x for p in frame), max(p.x for p in frame)
    bottom, top = min(p.y for p in frame), max(p.y for p in frame)
    if not left < 0 < right or not bottom < 0 < top:
        raise ValueError('移轴超出当前画幅的自动对焦范围，请减小水平或垂直移轴。')
    def fit(p):
        return max(p.x/(right if p.x >= 0 else left), p.y/(top if p.y >= 0 else bottom))
    if data.type == 'ORTHO':
        data.ortho_scale = max(fit(p) for p in local) * settings.margin / settings.camera_zoom
        distance = height * 3
    else:
        distance = max(p.z + fit(p)*settings.margin for p in local) / settings.camera_zoom
        distance = max(distance, max(p.z for p in local) + height*.1)
    camera.location = target + direction * distance
    data.clip_start = max(height*.001, .0001)
    data.clip_end = max(height*20, distance+height*5)
    context.view_layer.update()
    return {'projection': data.type, 'lens': data.lens, 'target': list(target), 'location': list(camera.location),
            'ortho_scale': data.ortho_scale, 'resolution': list(resolution(settings)),
            'roi': [list(point) for point in points], 'azimuth': settings.camera_azimuth+shot.get('azimuth', 0),
            'elevation': shot.get('elevation', settings.camera_elevation), 'focus': focus,
            'included_hands': included}
