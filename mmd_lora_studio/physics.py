"""Read authored MMD data and isolate each unique physics state in a worker."""
import importlib
import math
import json
import subprocess
import tempfile
import sys
import hashlib
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from .performance import aliases
from .core import normalized_name


def mmd_api():
    package = next((name for name in bpy.context.preferences.addons.keys() if name == 'mmd_tools' or name.endswith('.mmd_tools')), None)
    if not package and hasattr(bpy.types.Object, 'mmd_type'):
        package = next((name for name, module in sys.modules.copy().items()
                        if (name == 'mmd_tools' or name.endswith('.mmd_tools')) and hasattr(module, 'register')), None)
    if not package:
        raise ValueError('请先启用 MMD Tools，以读取和运行 PMX 刚体 / 关节。')
    return importlib.import_module(package + '.core.model')


def model_for(rig):
    api = mmd_api()
    root = api.FnModel.find_root_object(rig)
    return api.Model(root) if root else None


def physics_info(rig):
    try:
        model = model_for(rig) if rig else None
        bodies = list(model.rigidBodies()) if model else []
        return {'bodies': len(bodies), 'dynamic': sum(int(o.mmd_rigid.type) != 0 for o in bodies),
                'joints': len(list(model.joints())) if model else 0, 'built': bool(model and model.rootObject().mmd_root.is_built)}
    except ValueError:
        return {'bodies': 0, 'dynamic': 0, 'joints': 0, 'built': False}


def import_physics(context, settings, filepath):
    """Read just authored bones/physics, map to the existing rig, discard donor."""
    path = Path(bpy.path.abspath(filepath)).resolve()
    if not path.is_file() or path.suffix.lower() not in {'.pmx', '.pmd'}:
        raise ValueError('请选择当前角色对应的 PMX / PMD 源文件。')
    target = model_for(settings.armature)
    if not target:
        raise ValueError('当前骨架没有 MMD 根对象，请先用 MMD Tools 导入模型。')
    if list(target.rigidBodies()):
        raise ValueError('当前角色已有物理数据，不重复导入。')
    before = set(bpy.data.objects)
    saved_selection = list(context.selected_objects); saved_active = context.view_layer.objects.active
    world = context.scene.rigidbody_world
    enabled = world.enabled if world else False
    kept = set()
    try:
        result = bpy.ops.mmd_tools.import_model(filepath=str(path), types={'ARMATURE', 'PHYSICS'},
                                               scale=settings.physics_scale, rename_bones=True,
                                               save_log=False, log_level='WARNING')
        if result != {'FINISHED'}:
            raise ValueError('MMD Tools 读取物理失败。')
        imported = set(bpy.data.objects)-before
        root = next(o for o in imported if getattr(o, 'mmd_type', '') == 'ROOT')
        donor = mmd_api().Model(root)
        donor_rig = donor.armature(); rig = settings.armature
        bodies = list(donor.rigidBodies()); joints = list(donor.joints())
        if not bodies:
            raise ValueError('源文件没有刚体数据，不能将静态衣服当成已完成物理。')
        target_names = {}
        for bone in rig.pose.bones:
            for name in aliases(bone):
                if name:
                    target_names.setdefault(normalized_name(name), set()).add(bone.name)
        mapping = {}
        for bone in donor_rig.pose.bones:
            if bone.name in rig.pose.bones:
                mapping[bone.name] = bone.name
                continue
            matches = set().union(*(target_names.get(normalized_name(n), set()) for n in aliases(bone) if n))
            if len(matches) == 1:
                mapping[bone.name] = next(iter(matches))
        missing = [o.mmd_rigid.bone for o in bodies if o.mmd_rigid.bone and o.mmd_rigid.bone not in mapping]
        if missing:
            raise ValueError('物理骨骼与当前角色不匹配：' + ', '.join(missing[:8]))
        transform = rig.matrix_world @ donor_rig.matrix_world.inverted()
        # All source references are validated before attaching anything.
        for obj in bodies:
            bone = mapping.get(obj.mmd_rigid.bone, '')
            matrix = transform @ obj.matrix_world
            obj.parent = target.rigidGroupObject(); obj.matrix_world = matrix
            relation = obj.constraints.get('mmd_tools_rigid_parent')
            if relation:
                relation.target = rig; relation.subtarget = bone
            obj.mmd_rigid.bone = bone
            obj.hide_render = True
        for obj in joints:
            matrix = transform @ obj.matrix_world
            obj.parent = target.jointGroupObject(); obj.matrix_world = matrix
            obj.hide_render = True
        kept = set(bodies+joints+[target.rigidGroupObject(), target.jointGroupObject()])
        settings.physics_source = str(path)
        return physics_info(rig)
    finally:
        for obj in list(set(bpy.data.objects)-before-kept):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0 and isinstance(data, (bpy.types.Mesh, bpy.types.Armature)):
                (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.armatures).remove(data)
        if context.scene.rigidbody_world:
            context.scene.rigidbody_world.enabled = enabled
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in saved_selection:
            if obj.name in context.scene.objects:
                obj.select_set(True)
        context.view_layer.objects.active = saved_active


class LocalMMDPhysics:
    """Worker-only engine; the worker exits after exporting the evaluated mesh."""
    def __init__(self, context, settings):
        self.context = context; self.scene = context.scene; self.settings = settings
        self.model = None; self.collections = []
        self.info = physics_info(settings.armature)
        self.reason = 'disabled' if settings.physics_mode == 'OFF' else 'missing_authored_physics'
        if settings.physics_mode == 'OFF':
            return
        if self.info['dynamic'] == 0:
            if settings.physics_mode == 'MMD':
                raise ValueError('当前角色未导入动态刚体；请先从原 PMX 补入物理数据。')
            return
        self.model = model_for(settings.armature)
        if self.info['built']:
            raise ValueError('当前 MMD 物理已组装；请先在 MMD Tools 中清理物理，避免覆盖现有模拟。')
        world = self.scene.rigidbody_world
        if not world or world.point_cache.is_baked:
            raise ValueError('请在未烘焙的 MMD 物理工程中运行独立照片模拟。')
        self.damping = [(o.rigid_body, o.rigid_body.linear_damping, o.rigid_body.angular_damping)
                        for o in self.model.rigidBodies() if o.rigid_body and int(o.mmd_rigid.type) != 0]
        for name in ('AniForge Physics Bodies', 'AniForge Physics Joints'):
            col = bpy.data.collections.new(name); self.scene.collection.children.link(col); self.collections.append(col)

    def settle(self, context, supports=()):
        if not self.model:
            return dict(self.info, status=self.reason, frames=0)
        rig = self.settings.armature
        target = {b.name: b.matrix_basis.copy() for b in rig.pose.bones}
        world = self.scene.rigidbody_world
        world.enabled = False
        for bone in rig.pose.bones:
            bone.matrix_basis = Matrix.Identity(4)
        context.view_layer.update()
        self.scene.frame_set(1)
        self.model.build(non_collision_distance_scale=1.5, collision_margin=.00001)
        for rb, linear, angular in self.damping:
            rb.linear_damping = max(linear, self.settings.physics_damping)
            rb.angular_damping = max(angular, self.settings.physics_damping)
        bodies = list(self.model.rigidBodies())
        all_physics = list(self.model.rootObject().children_recursive)
        for obj in bodies+list(supports):
            if obj.name not in self.collections[0].objects:
                self.collections[0].objects.link(obj)
        for obj in all_physics:
            if obj.rigid_body_constraint and obj.name not in self.collections[1].objects:
                self.collections[1].objects.link(obj)
        world.collection, world.constraints = self.collections
        world.substeps_per_frame = self.settings.physics_substeps
        world.solver_iterations = 30; world.time_scale = 1
        transition = self.settings.physics_transition
        count = transition+self.settings.physics_frames
        max_count = transition+self.settings.physics_frames*3
        world.point_cache.frame_start = 1; world.point_cache.frame_end = max_count+2
        self.scene.frame_set(1)
        world.enabled = True
        dynamic = [o for o in bodies if int(o.mmd_rigid.type) != 0]
        previous = None; steps = []
        height = max((rig.matrix_world.to_3x3() @ b.bone.head_local).length for b in rig.pose.bones)
        from .performance import blend_basis
        for frame in range(1, max_count+2):
            blend = min((frame-1)/max(transition, 1), 1)
            for bone in rig.pose.bones:
                if 'mmd_tools_rigid_track' not in bone.constraints:
                    bone.matrix_basis = blend_basis(target[bone.name], blend)
            context.view_layer.update()
            self.scene.frame_set(frame)
            depsgraph = context.evaluated_depsgraph_get()
            positions = [o.evaluated_get(depsgraph).matrix_world.translation.copy() for o in dynamic]
            if any(not all(math.isfinite(v) for v in p) for p in positions):
                raise ValueError('衣服物理出现无效位置，已停止当前照片。')
            if previous:
                steps.append(max((a-b).length for a, b in zip(positions, previous))/max(height, .0001))
            previous = positions
            if frame >= count+1 and max(steps[-5:], default=0) <= self.settings.physics_tolerance:
                break
        speed = max(steps[-5:], default=0)
        status = 'settled' if speed <= self.settings.physics_tolerance else 'not_converged'
        if speed > .2:
            raise ValueError('衣服物理发生大幅跳动，请检查刚体碰撞或增加过渡帧。')
        if status != 'settled' and self.settings.physics_strict:
            raise ValueError('衣服物理尚未稳定，请增加稳定帧数或检查关节。')
        return dict(self.info, status=status, frames=frame, transition=transition, joint_mode=self.settings.physics_joint_mode,
                    max_final_step_per_height=speed, tolerance=self.settings.physics_tolerance)

class MMDPhysics:
    """The interactive Blender process never builds or cleans a Bullet rig."""
    def __init__(self, context, settings):
        self.context = context; self.settings = settings; self.model = None
        self.temp = None; self.meshes = []; self.data = []; self.hidden = []
        self.info = physics_info(settings.armature)
        self.reason = 'disabled' if settings.physics_mode == 'OFF' else 'missing_authored_physics'
        self.sequence = 0
        if settings.physics_mode == 'OFF':
            return
        if not self.info['dynamic']:
            if settings.physics_mode == 'MMD':
                raise ValueError('当前角色未导入动态刚体；请先从原 PMX 补入物理数据。')
            return
        if self.info['built']:
            raise ValueError('请先在 MMD Tools 中清理已组装的物理；后台计算使用未组装的数据副本。')
        self.model = model_for(settings.armature)
        # Save a copy: the user's file path and source .blend remain untouched.
        self.temp = tempfile.TemporaryDirectory(prefix='aniforge_physics_')
        self.directory = Path(self.temp.name).resolve()
        self.snapshot = self.directory / 'source.blend'
        try:
            bpy.ops.wm.save_as_mainfile(filepath=str(self.snapshot), check_existing=False, copy=True)
        except Exception:
            self.temp.cleanup(); self.temp = None
            raise

    def clear(self):
        for obj, hidden in self.hidden:
            obj.hide_render = hidden
        self.hidden.clear()
        for obj in self.meshes:
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in self.data:
            bpy.data.meshes.remove(data)
        self.meshes.clear(); self.data.clear()

    def settle(self, context, supports=(), meshes=()):
        if not self.model:
            return dict(self.info, status=self.reason, frames=0)
        context.view_layer.update()
        self.sequence += 1
        stem = self.directory / f'shot_{self.sequence:03d}'
        path = stem.with_suffix('.json'); output = stem.with_suffix('.npz')
        rig = self.settings.armature
        job = {'snapshot': str(self.snapshot), 'output': str(output), 'rig': rig.name,
               'bones': {b.name: [list(row) for row in b.matrix_basis] for b in rig.pose.bones},
               'constraints': {b.name: {c.name: c.mute for c in b.constraints} for b in rig.pose.bones},
               'meshes': [obj.name for obj in meshes],
               'shapes': {obj.name: {key.name: key.value for key in obj.data.shape_keys.key_blocks} for obj in meshes if obj.data.shape_keys},
               'settings': {name: getattr(self.settings, name) for name in ('physics_transition', 'physics_frames', 'physics_substeps', 'physics_damping', 'physics_joint_mode', 'physics_seat_collision', 'physics_tolerance', 'physics_strict')},
               'supports': [{'ground': obj.name.startswith('AniForge Physics Ground'), 'seat_top': obj.name == 'AniForge Seat', 'matrix': [list(row) for row in obj.matrix_world], 'vertices': [list(v.co) for v in obj.data.vertices],
                             'faces': [list(p.vertices) for p in obj.data.polygons]} for obj in supports]}
        def canonical(value):
            if isinstance(value, float):
                return round(value, 5)
            if isinstance(value, dict):
                return {key: canonical(item) for key, item in value.items()}
            if isinstance(value, list):
                return [canonical(item) for item in value]
            return value
        digest = hashlib.sha256(json.dumps(canonical({key: value for key, value in job.items() if key not in {'snapshot', 'output'}}),
                                           ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        output = self.directory / (digest+'.npz'); job['output'] = str(output)
        path.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        logpath = stem.with_suffix('.log')
        cache_hit = output.is_file()
        if not cache_hit:
            with logpath.open('w', encoding='utf-8') as log:
                result = subprocess.run([bpy.app.binary_path, '--background', '--factory-startup', '--python-exit-code', '1',
                                         '--python', str(Path(__file__).with_name('physics_worker.py')), '--', str(path)],
                                        stdout=log, stderr=subprocess.STDOUT, timeout=300,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not output.is_file():
                tail = logpath.read_text(encoding='utf-8', errors='replace')[-1800:]
                raise ValueError('后台物理计算失败，当前照片未输出。' + tail)
        import numpy as np
        with np.load(output, allow_pickle=False) as archive:
            meta = json.loads(str(archive['metadata']))
            meta['reused_physics'] = cache_hit
            if meta.get('error'):
                raise ValueError(meta['error'])
            for index, source in enumerate(meshes):
                prefix = f'm{index}_'
                vertices = archive[prefix+'vertices']
                loop = archive[prefix+'loops']; starts = archive[prefix+'starts']; sizes = archive[prefix+'sizes']
                if not np.isfinite(vertices).all():
                    raise ValueError('后台物理网格包含无效顶点。')
                data = bpy.data.meshes.new(source.name+'_settled')
                data.from_pydata(vertices, [], [loop[int(start):int(start+size)].tolist() for start, size in zip(starts, sizes)])
                self.data.append(data)
                for material in source.data.materials:
                    data.materials.append(material)
                data.polygons.foreach_set('material_index', archive[prefix+'materials'])
                data.polygons.foreach_set('use_smooth', archive[prefix+'smooth'])
                uv_names = json.loads(str(archive[prefix+'uv_names']))
                for uv_index, name in enumerate(uv_names):
                    layer = data.uv_layers.new(name=name)
                    layer.data.foreach_set('uv', archive[prefix+f'uv{uv_index}'].ravel())
                data.normals_split_custom_set(archive[prefix+'normals'].tolist())
                obj = bpy.data.objects.new(source.name+'_settled', data)
                context.scene.collection.objects.link(obj)
                obj.matrix_world = source.matrix_world.copy()
                obj.hide_render = source.hide_render
                self.meshes.append(obj); self.hidden.append((source, source.hide_render)); source.hide_render = True
        return meta

    def close(self):
        self.clear()
        if self.temp:
            self.temp.cleanup(); self.temp = None
