"""Static, proportion-based hand-on-waist fitting; no simulation or mesh edits."""
import math

import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

_REPORTS = {}


def hand_frame(rig, side, wrist, fingers):
    from .performance import bone_side
    roots = {kind: b for b, kind, i in fingers if i == 1 and bone_side(b) == side}
    middle = roots.get('middle') or roots.get('index')
    index, pinky = roots.get('index'), roots.get('pinky') or roots.get('ring')
    if middle and index and pinky:
        forward = middle.bone.head_local-wrist.bone.head_local
        palm = forward.cross(index.bone.head_local-pinky.bone.head_local) * (1 if side == 'left' else -1)
        if forward.length > 1e-6 and palm.length > 1e-6:
            return forward.normalized(), palm.normalized(), forward.length
    rest = wrist.bone.matrix_local
    return rest.col[1].xyz.normalized(), rest.col[2].xyz.normalized() * (-1 if side == 'left' else 1), wrist.bone.length


def orient_wrist(rig, wrist, rest_frame, forward, inward):
    def frame(f, p):
        f = f.normalized(); p = p-f*p.dot(f)
        if p.length < 1e-6:
            raise ValueError('手掌方向与手指方向重合，请调整手部偏移。')
        p.normalize()
        return Matrix((f, p, f.cross(p))).transposed()
    inverse = rig.matrix_world.to_3x3().inverted()
    desired = frame(inverse @ forward, inverse @ inward)
    rest = frame(*rest_frame[:2])
    rotation = (desired @ rest.transposed() @ wrist.bone.matrix_local.to_3x3()).to_quaternion()
    matrix = wrist.matrix.copy()
    wrist.matrix = Matrix.LocRotScale(matrix.translation, rotation, matrix.to_scale())
    bpy.context.view_layer.update()


def twist_forearm(rig, elbow, wrist, rest_frame, inward):
    # Share pronation with the forearm rather than putting its entire roll
    # into the wrist. This also keeps wrist-weighted cuffs from corkscrewing.
    wrist.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    axis = (wrist.head-elbow.head).normalized()
    rotation = wrist.matrix.to_quaternion() @ wrist.bone.matrix_local.to_quaternion().inverted()
    current = rotation @ rest_frame[1]
    desired = rig.matrix_world.to_3x3().inverted() @ inward
    current -= axis*current.dot(axis); desired -= axis*desired.dot(axis)
    if min(current.length, desired.length) < 1e-6:
        return
    current.normalize(); desired.normalize()
    angle = math.atan2(axis.dot(current.cross(desired)), current.dot(desired))
    matrix = elbow.matrix.copy()
    elbow.matrix = Matrix.LocRotScale(matrix.translation, Quaternion(axis, angle) @ matrix.to_quaternion(), matrix.to_scale())
    bpy.context.view_layer.update()


def shape_hand(rig, side, mode, fingers, rest_frame):
    from .performance import bone_side
    if mode == 'KEEP':
        return []
    palm = rest_frame[1]
    relaxed = {'index': (12, 20, 13), 'middle': (14, 23, 14), 'ring': (16, 25, 15), 'pinky': (18, 27, 16)}
    hip = {'index': (10, 22, 10), 'middle': (13, 27, 12), 'ring': (17, 32, 15), 'pinky': (22, 36, 18)}
    roots = {kind: b for b, kind, i in fingers if i == 1 and bone_side(b) == side}
    thumbs = sorted([(i, b) for b, kind, i in fingers if kind == 'thumb' and bone_side(b) == side], key=lambda item: item[0])
    thumb_root = thumbs[0][1] if thumbs else None
    radial = (roots['index'].bone.head_local-roots['middle'].bone.head_local) if 'index' in roots and 'middle' in roots else rest_frame[1].cross(rest_frame[0])*(1 if side == 'left' else -1)
    radial -= rest_frame[0]*radial.dot(rest_frame[0])+palm*radial.dot(palm)
    radial = radial.normalized() if radial.length > 1e-6 else palm.cross(rest_frame[0]).normalized()
    changed = []
    for bone, kind, segment in fingers:
        if bone_side(bone) != side:
            continue
        if mode == 'HIP' and bone == thumb_root:
            # Keep a moderate thumb spread and a curved IP/MCP chain. A fully
            # abducted, extended thumb creates the previous rigid L silhouette.
            desired = (rest_frame[0]*.85+radial*.4-palm*.2).normalized()
            current = (bone.bone.tail_local-bone.bone.head_local).normalized()
            rest = bone.bone.matrix_local.to_quaternion()
            bone.rotation_mode = 'QUATERNION'
            bone.rotation_quaternion = rest.inverted() @ current.rotation_difference(desired) @ rest
            changed.append(bone.name)
            continue
        extended = mode == 'OPEN' or mode == 'POINT' and kind == 'index' or mode == 'PEACE' and kind in {'index', 'middle'}
        if kind == 'thumb':
            angle = (0, 24, 22)[min(segment, 2)] if mode == 'HIP' else (0, 12, 15)[min(segment, 2)] if mode == 'RELAXED' else 0 if mode == 'OPEN' else (12, 25, 20)[min(segment, 2)]
        elif extended or segment == 0:
            angle = 0
        else:
            angle = (hip[kind] if mode == 'HIP' else relaxed[kind] if mode == 'RELAXED' else (45, 70, 45))[min(segment-1, 2)]
        forward = (bone.bone.tail_local-bone.bone.head_local).normalized()
        hinge = forward.cross(palm)
        if hinge.length < 1e-6:
            continue
        inverse = bone.bone.matrix_local.to_3x3().inverted()
        rotation = Quaternion((inverse @ hinge.normalized()).normalized(), math.radians(angle))
        if mode in {'HIP', 'RELAXED'} and kind != 'thumb' and segment == 1 and 'middle' in roots:
            middle = (roots['middle'].bone.tail_local-roots['middle'].bone.head_local).normalized()
            a = (forward-palm*forward.dot(palm)).normalized()
            b = (middle-palm*middle.dot(palm)).normalized()
            close = math.atan2(palm.dot(a.cross(b)), a.dot(b))*(.6 if mode == 'HIP' else .35)
            rotation = Quaternion((inverse @ palm).normalized(), close) @ rotation
        if mode == 'PEACE' and kind in {'index', 'middle'} and segment == 1:
            rotation = Quaternion((inverse @ palm).normalized(), math.radians(8 if kind == 'index' else -6)) @ rotation
        bone.rotation_mode = 'QUATERNION'; bone.rotation_quaternion = rotation
        changed.append(bone.name)
    bpy.context.view_layer.update()
    return changed


def model_meshes(rig):
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH' or obj.hide_render or getattr(obj, 'mmd_type', '') in {'RIGID_BODY', 'JOINT', 'TRACK_TARGET'}:
            continue
        parent = obj.parent
        while parent and parent != rig:
            parent = parent.parent
        if parent == rig or any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers):
            yield obj


def surface_geometry(settings, fingers):
    from .performance import find_bone, bone_side
    rig = settings.armature
    trunk = {b.name for key in ('lower_body', 'upper_body', 'upper_body2') if (b := find_bone(rig, key, settings))}
    blocked = {b.name for key in ('head', 'left_arm', 'right_arm', 'left_shoulder', 'right_shoulder') if (b := find_bone(rig, key, settings))}
    body_names = {b.name for b in rig.data.bones if ({b.name} | {p.name for p in b.parent_recursive}) & trunk
                  and not ({b.name} | {p.name for p in b.parent_recursive}) & blocked}
    hand_names = {side: {b.name for b, _, _ in fingers if bone_side(b) == side} |
                  ({w.name} if (w := find_bone(rig, side+'_wrist', settings)) else set()) for side in ('left', 'right')}
    vertices = []; triangles = []; samples = {'left': [], 'right': []}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in model_meshes(rig):
        evaluated = obj.evaluated_get(depsgraph)
        if len(evaluated.data.vertices) != len(obj.data.vertices):
            continue
        groups = {g.index for g in obj.vertex_groups if g.name in body_names}
        hands = {side: {g.index for g in obj.vertex_groups if g.name in names} for side, names in hand_names.items()}
        hand_indices = {side: [v.index for v in obj.data.vertices if sum(g.weight for g in v.groups if g.group in indices) >= .55]
                        for side, indices in hands.items()}
        excluded = set(hand_indices['left']) | set(hand_indices['right'])
        body = {v.index for v in obj.data.vertices if v.index not in excluded and sum(g.weight for g in v.groups if g.group in groups) >= .35}
        offset = len(vertices)
        vertices += [evaluated.matrix_world @ v.co for v in evaluated.data.vertices]
        evaluated.data.calc_loop_triangles()
        triangles += [tuple(i+offset for i in t.vertices) for t in evaluated.data.loop_triangles if set(t.vertices) <= body]
        for side, indices in hand_indices.items():
            if indices:
                samples[side].append((obj, indices))
    tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True) if triangles else None
    return tree, samples


def fit_report(rig):
    return _REPORTS.get(rig.as_pointer(), {})


def hand_shape_metrics(rig, side, wrist, elbow, fingers):
    from .performance import bone_side
    chains = {kind: sorted([(i, b) for b, k, i in fingers if k == kind and bone_side(b) == side], key=lambda item: item[0])
              for kind in ('thumb', 'index', 'middle', 'ring', 'pinky')}
    def angle(a, b):
        return math.degrees(a.angle(b)) if min(a.length, b.length) > 1e-6 else None
    def direction(b):
        return (b.tail-b.head).normalized()
    rest = hand_frame(rig, side, wrist, fingers)
    rotation = wrist.matrix.to_quaternion() @ wrist.bone.matrix_local.to_quaternion().inverted()
    forward, palm = rotation @ rest[0], rotation @ rest[1]
    def in_plane(v):
        return (v-palm*v.dot(palm)).normalized()
    thumb = chains['thumb']
    metrics = {'wrist_bend_degrees': angle(wrist.head-elbow.head, forward),
               'thumb_open_degrees': angle(in_plane(direction(thumb[0][1])), in_plane(forward)) if thumb else None,
               'thumb_tip_bend_degrees': angle(direction(thumb[-2][1]), direction(thumb[-1][1])) if len(thumb) >= 2 else None,
               'finger_joint_bends': {kind: [angle(direction(a[1]), direction(b[1])) for a, b in zip(chain, chain[1:])]
                                      for kind, chain in chains.items() if kind != 'thumb'}}
    return metrics


def apply_hand_hip(settings):
    from .performance import find_bone, finger_bones, bone_side, place_hand, blend_basis
    rig = settings.armature
    fingers = list(finger_bones(rig))
    keys = [side+'_'+part for side in ('left', 'right') for part in ('arm', 'elbow', 'wrist')]
    bones = {key: find_bone(rig, key, settings) for key in keys}
    hip = find_bone(rig, 'lower_body', settings)
    if not hip or not all(bones.values()):
        raise ValueError('叉腰姿势需要下半身、双侧上臂、肘和手首骨骼，请先检查骨骼映射。')
    controlled = list({b.name: b for b in list(bones.values())+[b for b, _, _ in fingers]}.values())
    for bone in list(bones.values()):
        bone.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    front = math.radians(settings.front)
    side_axis = Vector((math.cos(front), math.sin(front), 0)); forward = Vector((math.sin(front), -math.cos(front), 0)); up = Vector((0, 0, 1))
    origin = rig.matrix_world @ hip.head
    left, right = (rig.matrix_world @ bones[side+'_arm'].head for side in ('left', 'right'))
    unit = max((left-right).length, .0001)
    waist_z = origin.z+max((left.z+right.z)*.5-origin.z, unit)*.32
    tree, samples = surface_geometry(settings, fingers)
    distance = max(unit*12, (rig.matrix_world @ find_bone(rig, 'head', settings).head-origin).length*4) if find_bone(rig, 'head', settings) else unit*12
    margin = unit*getattr(settings, 'hip_clearance', .02)
    hip_side = 'right' if getattr(settings, 'hip_hand', 'LEFT') == 'RIGHT' else 'left'
    reports = {}; changed = []

    def front_surface(x, z):
        point = origin+side_axis*x+up*(z-origin.z)+forward*distance
        hit, normal, _, _ = tree.ray_cast(point, -forward, distance*2) if tree else (None, None, None, None)
        if hit is not None and normal.dot(forward) < 0:
            normal = -normal
        return hit, normal

    def hand_points(side):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj, indices in samples[side]:
            evaluated = obj.evaluated_get(depsgraph)
            for index in indices:
                yield evaluated.matrix_world @ evaluated.data.vertices[index].co

    for side in (hip_side, 'right' if hip_side == 'left' else 'left'):
        sign = 1 if side == 'left' else -1
        wrist = bones[side+'_wrist']; arm = bones[side+'_arm']; elbow = bones[side+'_elbow']
        rest = hand_frame(rig, side, wrist, fingers)
        world_palm = max((rig.matrix_world.to_3x3() @ (rest[0]*rest[2])).length, unit*.2)
        is_hip = side == hip_side
        if is_hip:
            center, _ = front_surface(0, waist_z)
            width = unit*.75
            if center is not None:
                visible = []
                for i in range(1, 31):
                    x = sign*unit*i*.05
                    hit, _ = front_surface(x, waist_z)
                    if hit is None or (hit-center).dot(forward) < -unit*.4:
                        break
                    visible.append(abs(x))
                if visible:
                    width = visible[-1]
            contact, normal = front_surface(sign*width*.68, waist_z)
            if contact is None:
                contact = origin+side_axis*(sign*width*.68)+forward*unit*.55+up*(waist_z-origin.z)
                normal = (forward+side_axis*sign*.25).normalized()
            # Aim the whole palm along the waist arc, not along a single
            # flank triangle. A flank normal makes all fingers point forward
            # into empty space even when the heel of the hand is near the hip.
            _, inner_normal = front_surface(sign*width*.42, waist_z)
            if inner_normal is not None:
                normal = inner_normal
            # Cloth folds can tilt a triangle normal sharply upward. Use the
            # waist cross-section for hand orientation, keeping wrist extension
            # modest instead of following the fold with an upturned palm.
            normal.z = 0
            normal = normal.normalized() if normal.length > 1e-6 else forward.copy()
            direction = -side_axis*sign+up*.08
            direction = (direction-normal*direction.dot(normal)).normalized()
            inward = -normal
            target = contact+normal*(margin+world_palm*.14)-direction*world_palm*.55
            outward = forward
            offset = getattr(settings, 'hip_offset', (0, 0, 0))
            pole = side_axis*sign+forward*.1-up*1.05
        else:
            shoulder = rig.matrix_world @ arm.head
            reach = (rig.matrix_world.to_3x3() @ (elbow.bone.head_local-arm.bone.head_local)).length
            reach += (rig.matrix_world.to_3x3() @ (wrist.bone.head_local-elbow.bone.head_local)).length
            target = origin+side_axis*sign*unit*.85+forward*unit*.3
            horizontal = target-shoulder; horizontal.z = 0
            target.z = shoulder.z-math.sqrt(max(reach*reach*.97**2-horizontal.length_squared, unit*unit*.2))
            direction = (side_axis*sign*.15+forward*.06-up).normalized()
            inward = -side_axis*sign+forward*.1
            outward = (side_axis*sign+forward*.7).normalized()
            offset = getattr(settings, 'free_hand_offset', (0, 0, 0))
            pole = side_axis*sign+forward*.15-up*.2
        target += unit*(side_axis*sign*offset[0]+forward*offset[1]+up*offset[2])
        mode = ('HIP' if is_hip else 'RELAXED') if settings.hand_pose == 'AUTO' else settings.hand_pose
        changed += shape_hand(rig, side, mode, fingers, rest)
        best = None; candidate_scores = []
        # Clearance alone preferred a flat hand hovering in front of the waist.
        # Compare a few modest palm-flexion angles by actual contact distances,
        # while retaining collision clearance and a limit on wrist bending.
        for pitch in ((0, 12, 24) if is_hip and tree else (0,)):
            flex = Quaternion(direction.cross(inward).normalized(), math.radians(pitch))
            candidate_direction, candidate_inward = flex @ direction, flex @ inward
            goal = target.copy(); total_shift = 0; deficit = 0; first_intrusion = None
            for iteration in range(8):
                if not is_hip:
                    horizontal = goal-shoulder; horizontal.z = 0
                    maximum = reach*.985
                    if horizontal.length > maximum*.98:
                        horizontal *= maximum*.98/horizontal.length
                        goal.x = shoulder.x+horizontal.x; goal.y = shoulder.y+horizontal.y
                    goal.z = max(goal.z, shoulder.z-math.sqrt(max(maximum*maximum-horizontal.length_squared, 0)))
                changed += place_hand(settings, side, goal, pole, 1)
                twist_forearm(rig, elbow, wrist, rest, candidate_inward)
                orient_wrist(rig, wrist, rest, candidate_direction, candidate_inward)
                deficit = 0
                if tree:
                    for point in hand_points(side):
                        hit = tree.ray_cast(point+outward*distance, -outward, distance*2)[0]
                        if hit is not None:
                            value = margin-(point-hit).dot(outward)
                            if value > deficit:
                                deficit = value
                                if iteration == 0:
                                    first_intrusion = {'hand_point': list(point), 'surface_point': list(hit), 'depth': value}
                if deficit <= unit*.002:
                    break
                step = min(deficit+unit*.005, unit*.2)
                goal += outward*step; total_shift += step
            actual = rig.matrix_world @ wrist.head
            middle = next((b for b, kind, segment in fingers if kind == 'middle' and segment == 1 and bone_side(b) == side), None)
            center = actual.lerp(rig.matrix_world @ middle.head, .6) if middle else actual
            nearest = tree.find_nearest(center) if tree else (None, None, None, None)
            finger_distances = {}
            if tree:
                for kind in ('thumb', 'index', 'middle', 'ring', 'pinky'):
                    chain = [(i, b) for b, k, i in fingers if k == kind and bone_side(b) == side]
                    if chain:
                        tip = rig.matrix_world @ max(chain, key=lambda item: item[0])[1].tail
                        finger_distances[kind] = tree.find_nearest(tip)[3]
            tip_distances = [d for k, d in finger_distances.items() if k != 'thumb' and d is not None]
            mean_contact = sum(tip_distances)/len(tip_distances) if tip_distances else None
            contact_review = bool(is_hip and nearest[3] is not None and (nearest[3] > unit*.2 or mean_contact is not None and mean_contact > unit*.1))
            shape = hand_shape_metrics(rig, side, wrist, elbow, fingers)
            shape_review = bool(is_hip and (
                shape['thumb_open_degrees'] is None or not 10 <= shape['thumb_open_degrees'] <= 60 or
                shape['thumb_tip_bend_degrees'] is None or not 8 <= shape['thumb_tip_bend_degrees'] <= 45 or
                shape['wrist_bend_degrees'] is None or shape['wrist_bend_degrees'] > 70))
            score = (mean_contact or 0)+.35*(nearest[3] or 0)+.15*max(tip_distances, default=0)
            score += max(0, (shape['wrist_bend_degrees'] or 0)-65)*unit/90
            score += max(0, deficit)*100+(goal-actual).length*10
            report = {'role': 'hip' if is_hip else 'relaxed', 'surface_available': bool(tree),
                      'iterations': iteration+1, 'clearance_deficit': deficit, 'outward_shift': total_shift,
                      'initial_intrusion': first_intrusion, 'contact_pitch_degrees': pitch,
                      'palm_center': list(center), 'palm_surface_distance': nearest[3],
                      'finger_surface_distances': finger_distances, 'mean_finger_contact_distance': mean_contact,
                      'contact_needs_review': contact_review, 'target_error': (goal-actual).length, 'wrist': list(actual),
                      'hand_shape': shape, 'shape_needs_review': shape_review,
                      'status': 'fitted' if tree and deficit <= unit*.002 and (goal-actual).length <= unit*.02 and not contact_review and not shape_review else 'needs_review'}
            candidate_scores.append({'pitch': pitch, 'score': score, 'mean_finger_distance': mean_contact})
            if best is None or score < best['score']:
                best = {'score': score, 'report': report, 'bones': [(b, b.matrix_basis.copy()) for b in (arm, elbow, wrist)]}
        for bone, matrix in best['bones']:
            bone.matrix_basis = matrix
        bpy.context.view_layer.update()
        reports[side] = dict(best['report'], candidate_scores=candidate_scores)
        changed.append(wrist.name)
    if settings.pose_strength < 1:
        for bone in controlled:
            bone.matrix_basis = blend_basis(bone.matrix_basis, settings.pose_strength)
        bpy.context.view_layer.update()
    _REPORTS[rig.as_pointer()] = {'pose': 'hand_hip', 'hip_hand': hip_side, 'hands': reports,
                                 'scope': 'static torso/garment envelope; inspect sleeves, accessories and occlusion'}
    return list(dict.fromkeys(changed))
