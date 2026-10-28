"""One isolated physics job; output evaluated geometry, then exit the worker."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
import addon_utils
import numpy as np
from mathutils import Matrix
import mmd_lora_studio as addon
from mmd_lora_studio.physics import LocalMMDPhysics
from mmd_lora_studio.pbd import settle_proxy


def run(job):
    addon_utils.enable('bl_ext.user_default.mmd_tools', default_set=False)
    addon.register()
    bpy.ops.wm.open_mainfile(filepath=job['snapshot'])
    settings = bpy.context.scene.ml_settings
    settings.armature = bpy.data.objects[job['rig']]
    for name, value in job['settings'].items():
        setattr(settings, name, value)
    settings.physics_mode = 'MMD'
    rig = settings.armature
    for bone in rig.pose.bones:
        bone.matrix_basis = Matrix(job['bones'][bone.name])
        for constraint in bone.constraints:
            constraint.mute = job['constraints'].get(bone.name, {}).get(constraint.name, constraint.mute)
    for obj_name, values in job['shapes'].items():
        obj = bpy.data.objects[obj_name]
        for name, value in values.items():
            obj.data.shape_keys.key_blocks[name].value = value
    supports = []
    for item in job['supports']:
        mesh = bpy.data.meshes.new('Support'); mesh.from_pydata(item['vertices'], [], item['faces'])
        obj = bpy.data.objects.new('Support', mesh); bpy.context.scene.collection.objects.link(obj)
        obj['aniforge_floor'] = item.get('ground', False)
        obj['aniforge_seat_top'] = item.get('seat_top', False)
        obj.matrix_world = Matrix(item['matrix']); bpy.context.view_layer.objects.active = obj
        if settings.physics_joint_mode == 'AUTHORED' and (item.get('ground') or settings.physics_seat_collision):
            bpy.ops.rigidbody.object_add()
            obj.rigid_body.type = 'PASSIVE'; obj.rigid_body.collision_shape = 'BOX'
            obj.rigid_body.use_margin = True; obj.rigid_body.collision_margin = .0001
        supports.append(obj)
    bpy.context.view_layer.update()
    if settings.physics_joint_mode == 'PROXY':
        meta = settle_proxy(settings, supports)
    else:
        engine = LocalMMDPhysics(bpy.context, settings)
        meta = engine.settle(bpy.context, supports)
    arrays = {'metadata': np.array(json.dumps(meta))}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for index, name in enumerate(job['meshes']):
        source = bpy.data.objects[name]
        obj = source.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(obj, preserve_all_data_layers=True, depsgraph=depsgraph)
        prefix = f'm{index}_'
        def data(key, size, dtype, collection, prop):
            array = np.empty(size, dtype=dtype); collection.foreach_get(prop, array); arrays[prefix+key] = array
            return array
        vertices = data('vertices', len(mesh.vertices)*3, np.float32, mesh.vertices, 'co')
        arrays[prefix+'vertices'] = vertices.reshape(-1, 3)
        data('loops', len(mesh.loops), np.int32, mesh.loops, 'vertex_index')
        data('starts', len(mesh.polygons), np.int32, mesh.polygons, 'loop_start')
        data('sizes', len(mesh.polygons), np.int32, mesh.polygons, 'loop_total')
        data('materials', len(mesh.polygons), np.int32, mesh.polygons, 'material_index')
        data('smooth', len(mesh.polygons), np.bool_, mesh.polygons, 'use_smooth')
        normals = data('normals', len(mesh.corner_normals)*3, np.float32, mesh.corner_normals, 'vector')
        arrays[prefix+'normals'] = normals.reshape(-1, 3)
        arrays[prefix+'uv_names'] = np.array(json.dumps([layer.name for layer in mesh.uv_layers]))
        for uv_index, layer in enumerate(mesh.uv_layers):
            data(f'uv{uv_index}', len(layer.data)*2, np.float32, layer.data, 'uv')
    output = Path(job['output']); temporary = output.with_suffix('.partial.npz')
    np.savez_compressed(temporary, **arrays); temporary.replace(output)
    print('ANIFORGE_PHYSICS_WORKER_COMPLETE', meta, flush=True)


if __name__ == '__main__':
    path = Path(sys.argv[sys.argv.index('--')+1])
    try:
        run(json.loads(path.read_text(encoding='utf-8')))
        # MMD/Bullet teardown can crash after a complex rig. This process owns
        # no unsaved user work; all output has been atomically flushed above.
        os._exit(0)
    except Exception:
        import traceback
        traceback.print_exc(); sys.stderr.flush()
        os._exit(1)
