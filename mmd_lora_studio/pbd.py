"""Coarse MMD bone-chain dynamics for stills; original geometry is only skinned.

Distance projection follows the PBD formulation of Mueller et al. (2006).
This is a proxy simulation, not a high-resolution garment or self-collision solver.
"""
import math

import bpy
import numpy as np
from mathutils import Matrix, Vector

from .performance import blend_basis
from .physics import model_for, physics_info


def settle_proxy(settings, supports):
    model = model_for(settings.armature); rig = settings.armature
    info = physics_info(rig)
    world = bpy.context.scene.rigidbody_world
    if world:
        world.enabled = False
    target = {b.name: b.matrix_basis.copy() for b in rig.pose.bones}
    bodies = list(model.rigidBodies())
    particles = {}
    for obj in bodies:
        name = obj.mmd_rigid.bone
        if int(obj.mmd_rigid.type) and name in rig.pose.bones and not any(t in name.casefold() for t in ('胸', 'breast', 'bust')):
            particles.setdefault(name, obj)
    names = sorted(particles, key=lambda name: len(rig.pose.bones[name].parent_recursive))
    if not names:
        raise ValueError('没有可模拟的衣服 / 头发骨骼。')
    indices = {name: index*2+1 for index, name in enumerate(names)}
    active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = rig
    with bpy.context.temp_override(object=rig, active_object=rig):
        bpy.ops.object.mode_set(mode='EDIT')
        for name in names:
            rig.data.edit_bones[name].use_connect = False
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = active
    for bone in rig.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
        if bone.name in indices:
            for constraint in bone.constraints:
                constraint.mute = True
    bpy.context.view_layer.update()
    to_world = rig.matrix_world; to_rig = to_world.inverted()
    rest = [tuple(to_world @ point) for name in names for point in (rig.pose.bones[name].head, rig.pose.bones[name].tail)]
    pinned = set(); graph = {name: set() for name in names}
    pairs = []
    for name in names:
        bone = rig.pose.bones[name]
        pairs.append((indices[name]-1, indices[name]))
        if bone.parent and bone.parent.name in indices:
            pairs.append((indices[bone.parent.name], indices[name]-1))
            graph[name].add(bone.parent.name); graph[bone.parent.name].add(name)
    for joint in model.joints():
        constraint = joint.rigid_body_constraint
        if not constraint:
            continue
        a, b = constraint.object1, constraint.object2
        if not a or not b:
            continue
        an, bn = a.mmd_rigid.bone, b.mmd_rigid.bone
        if an in indices and bn in indices and an != bn:
            ia, ib = indices[an], indices[bn]
            graph[an].add(bn); graph[bn].add(an)
            candidates = [(i, j) for i in (ia-1, ia) for j in (ib-1, ib)]
            nearest = min(candidates, key=lambda pair: (Vector(rest[pair[0]])-Vector(rest[pair[1]])).length)
            pairs.append(nearest)
            # Across a cloth row, preserve both ends of the strip. Along a
            # chain, the shared joint is a hinge and may bend under gravity.
            if nearest in {(ia-1, ib-1), (ia, ib)}:
                pairs.extend(((ia-1, ib-1), (ia, ib)))
        elif an in indices:
            pinned.add(an)
        elif bn in indices:
            pinned.add(bn)
    remaining = set(names)
    while remaining:
        component = set(); pending = [next(iter(remaining))]
        while pending:
            name = pending.pop()
            if name in component:
                continue
            component.add(name); pending.extend(graph[name]-component)
        remaining -= component
        if not component & pinned:
            highest = max(rest[indices[name]-1][2] for name in component)
            pinned.update(name for name in component if rest[indices[name]-1][2] >= highest-.01)
    anchors = [(indices[name]-1, name) for name in pinned]
    pairs = sorted({tuple(sorted(pair)) for pair in pairs})
    positions = np.array(rest, dtype=np.float64); previous = positions.copy()
    edge_i = np.array([p[0] for p in pairs]); edge_j = np.array([p[1] for p in pairs])
    lengths = np.linalg.norm(positions[edge_j]-positions[edge_i], axis=1)
    weights = np.ones(len(positions)); weights[[index for index, _ in anchors]] = 0
    particle_count = len(positions)
    total = weights[edge_i]+weights[edge_j]
    total = np.maximum(total, 1e-8)
    degrees = np.zeros(len(positions))
    np.add.at(degrees, edge_i, 1); np.add.at(degrees, edge_j, 1)
    degrees = np.maximum(degrees, 1)[:, None]
    height = max(b.bone.head_local.z for b in rig.pose.bones)-min(b.bone.head_local.z for b in rig.pose.bones)
    radius = height*.005
    colliders = []
    for obj in bodies:
        if int(obj.mmd_rigid.type) != 0:
            continue
        co = np.array([tuple(v.co) for v in obj.data.vertices])
        half = np.maximum(np.abs(co).max(axis=0), 1e-5)
        allowed = np.repeat([particles[name].mmd_rigid.collision_group_mask[obj.mmd_rigid.collision_group_number]
                             and obj.mmd_rigid.collision_group_mask[particles[name].mmd_rigid.collision_group_number] for name in names], 2)
        # A deform bone can be inside the body even when its skinned surface
        # is outside. Do not force such authored embedded controls out of skin.
        local = (positions-np.array(obj.matrix_world.translation)) @ np.linalg.inv(np.array(obj.matrix_world.to_3x3())).T
        shape = obj.mmd_rigid.shape
        if shape == 'BOX':
            embedded = np.all(np.abs(local) < half+radius, axis=1)
        else:
            near = np.zeros_like(local)
            r = float(max(half))
            if shape == 'CAPSULE':
                axis = int(np.argmax(half)); r = float(np.delete(half, axis).max())
                near[:, axis] = np.clip(local[:, axis], -max(half[axis]-r, 0), max(half[axis]-r, 0))
            embedded = np.linalg.norm(local-near, axis=1) < r+radius
        allowed &= ~embedded
        colliders.append((obj, obj.mmd_rigid.shape, half, np.flatnonzero(allowed & (weights > 0))))
    for obj in supports:
        if not obj.get('aniforge_floor') and not settings.physics_seat_collision:
            continue
        co = np.array([tuple(v.co) for v in obj.data.vertices])
        shape = 'GROUND' if obj.get('aniforge_floor') else 'SEAT' if obj.get('aniforge_seat_top') else 'BOX'
        colliders.append((obj, shape, np.abs(co).max(axis=0), np.flatnonzero(weights > 0)))
    count = settings.physics_transition+settings.physics_frames
    maximum = settings.physics_transition+settings.physics_frames*3
    fps = bpy.context.scene.render.fps/bpy.context.scene.render.fps_base
    dt = 1/max(fps, 1)
    gravity = np.array([0, 0, -9.81*dt*dt])
    floor_z = max((obj.matrix_world.translation.z+max(v.co.z for v in obj.data.vertices)*obj.scale.z
                   for obj in supports if obj.get('aniforge_floor')), default=-math.inf)
    speeds = []; matrices = []; contact_sides = {}
    for frame in range(maximum+1):
        blend = min(frame/max(settings.physics_transition, 1), 1)
        for bone in rig.pose.bones:
            bone.matrix_basis = blend_basis(target[bone.name], blend)
        bpy.context.view_layer.update()
        for index, name in anchors:
            positions[index] = tuple(to_world @ rig.pose.bones[name].head)
        matrices.clear()
        for obj, shape, half, allowed in colliders:
            matrix = obj.matrix_world.copy()
            if getattr(obj, 'mmd_type', '') == 'RIGID_BODY' and obj.mmd_rigid.bone in rig.pose.bones:
                bone = rig.pose.bones[obj.mmd_rigid.bone]
                matrix = to_world @ bone.matrix @ bone.bone.matrix_local.inverted() @ to_rig @ matrix
            basis = np.array(matrix.to_3x3()); inv = np.linalg.inv(basis)
            origin = np.array(matrix.translation)
            if frame == 0 and shape == 'SEAT':
                contact_sides[obj.name] = np.where(((positions[allowed]-origin) @ inv.T)[:, 2] >= 0, 1, -1)
            elif frame == 0 and shape not in {'BOX', 'GROUND'}:
                rest_local = (positions[allowed]-origin) @ inv.T
                near = np.zeros_like(rest_local)
                if shape == 'CAPSULE':
                    axis = int(np.argmax(half)); r = float(np.delete(half, axis).max())
                    near[:, axis] = np.clip(rest_local[:, axis], -max(half[axis]-r, 0), max(half[axis]-r, 0))
                direction = rest_local-near
                norm = np.maximum(np.linalg.norm(direction, axis=1), 1e-8)
                contact_sides[obj.name] = direction/norm[:, None]
            matrices.append((shape, half, allowed, origin, basis, inv, contact_sides.get(obj.name)))
        old = positions.copy()
        positions += ((positions-previous)*(1-settings.physics_damping)+gravity)*weights[:, None]
        for iteration in range(32):
            delta = positions[edge_j]-positions[edge_i]
            distance = np.maximum(np.linalg.norm(delta, axis=1), 1e-8)
            correction = delta*((distance-lengths)/distance)[:, None]
            changes = np.zeros_like(positions)
            np.add.at(changes, edge_i, correction*(weights[edge_i]/total)[:, None])
            np.add.at(changes, edge_j, -correction*(weights[edge_j]/total)[:, None])
            positions += changes/degrees
            if iteration % 4 != 3:
                continue
            for shape, half, allowed, origin, basis, inv, preferred in matrices:
                if not len(allowed):
                    continue
                local = (positions[allowed]-origin) @ inv.T
                padding = radius/max(np.linalg.norm(basis, axis=0).min(), 1e-5)
                if shape == 'GROUND':
                    local[:, 2] = np.maximum(local[:, 2], half[2]+padding)
                elif shape in {'BOX', 'SEAT'}:
                    extent = half+padding
                    inside = np.all(np.abs(local) < extent, axis=1)
                    if inside.any():
                        selected = np.flatnonzero(inside)
                        axes = np.full(len(selected), 2) if shape == 'SEAT' else np.argmin(extent-np.abs(local[selected]), axis=1)
                        sign = preferred[selected] if shape == 'SEAT' else np.where(local[selected, axes] >= 0, 1, -1)
                        local[selected, axes] = sign*extent[axes]
                else:
                    nearest = np.zeros_like(local)
                    r = float(min(half))
                    if shape == 'CAPSULE':
                        axis = int(np.argmax(half)); r = float(np.delete(half, axis).max())
                        nearest[:, axis] = np.clip(local[:, axis], -max(half[axis]-r, 0), max(half[axis]-r, 0))
                    else:
                        r = float(max(half))
                    d = local-nearest
                    length = np.linalg.norm(d, axis=1)
                    inside = length < r+padding
                    if inside.any():
                        d[length < 1e-8] = (0, 0, 1)
                        length[length < 1e-8] = 1
                        normal = d/np.maximum(length, 1e-8)[:, None]
                        local[inside] = nearest[inside]+normal[inside]*(r+padding)
                projected = local @ basis.T+origin
                if shape not in {'GROUND', 'SEAT'}:
                    below = inside & (projected[:, 2] < floor_z+radius)
                    # Authored foot colliders can extend below the floor.
                    # Resolve the intersection above that collider instead of
                    # alternating between its underside and the ground plane.
                    projected[below, 2] = max(floor_z+radius, origin[2]+float(np.abs(basis[2]) @ half)+radius)
                positions[allowed] = projected
        previous = old
        motion = np.linalg.norm(positions-old, axis=1)
        speed = float(motion.max())/max(height, 1e-4)
        speeds.append(speed)
        if frame >= count and max(speeds[-5:]) <= settings.physics_tolerance:
            break
    if not np.isfinite(positions).all():
        raise ValueError('衣服代理物理出现无效位置。')
    final_speed = max(speeds[-5:])
    status = 'settled' if final_speed <= settings.physics_tolerance else 'not_converged'
    if status != 'settled' and settings.physics_strict:
        raise ValueError(f'衣服代理尚未稳定（位移 / 身高 {final_speed:.4f}；{names[int(motion.argmax())//2]}）。')
    # Flat MMD physics hierarchies need translation as well as rotation.
    for name in names:
        bone = rig.pose.bones[name]; matrix = bone.matrix.copy()
        matrix.translation = to_rig @ Vector(positions[indices[name]-1])
        goal = to_rig @ Vector(positions[indices[name]])
        direction = goal-matrix.translation
        if direction.length > 1e-6:
            axis = matrix.to_quaternion() @ Vector((0, 1, 0))
            rotation = axis.rotation_difference(direction.normalized()) @ matrix.to_quaternion()
            bone.matrix = Matrix.LocRotScale(matrix.translation, rotation, matrix.to_scale())
            bpy.context.view_layer.update()
    return dict(info, status=status, frames=frame+1, transition=settings.physics_transition, joint_mode='PROXY',
                particles=particle_count, distance_constraints=len(pairs), max_final_step_per_height=final_speed,
                tolerance=settings.physics_tolerance, self_collision=False, anchors=len(anchors),
                seat_collision=settings.physics_seat_collision,
                worst_particle_bone=names[int(motion.argmax())//2])
