bl_info = {
    'name': 'AniForge - MMD LoRA Studio', 'author': 'AniForge', 'version': (0, 7, 0),
    'blender': (4, 2, 0), 'location': 'View3D > Sidebar > MMD LoRA',
    'description': 'MMD toon materials and 36-shot LoRA dataset rendering',
    'category': 'Render',
}

import json
import math
from datetime import datetime
from pathlib import Path

import bpy
from bpy.props import StringProperty, EnumProperty, PointerProperty, IntProperty, FloatProperty, BoolProperty, FloatVectorProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Vector, Matrix

from .core import (STYLES, GROUPS, MORPHS, BONES, POSE_ROTATIONS, POSE_DIRECTIONS, POSE_LABELS,
                   EXPRESSION_LABELS, SEATED_POSES, ShotSettings, current_recipe, dataset_coverage, recipe, three_view_recipe, caption, safe_name, write_json)
from .material_profiles import material_role, alpha_image, face_light, hair_color
from .performance import (aliases, find_bone, shape_blocks, custom_presets, store_presets, enum_presets,
                          expression_apply, pose_apply, capture_expression, capture_pose, restore_seated, SEATED_STATE)
from .camera import configure_camera, capture_camera, camera_apply, resolution, CAMERA_FIELDS
from .physics import MMDPhysics, physics_info, import_physics


def model_meshes(settings):
    rig = settings.armature
    if not rig:
        raise ValueError('请先指定角色骨架。')
    result = []
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
            continue
        if getattr(obj, 'mmd_type', '') in {'RIGID_BODY', 'JOINT', 'TRACK_TARGET'}:
            continue
        parent = obj.parent
        descendant = False
        while parent:
            if parent == rig:
                descendant = True
                break
            parent = parent.parent
        if descendant or any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers):
            result.append(obj)
    if not result:
        raise ValueError('指定骨架未关联任何网格；请检查父级或骨架修改器。')
    return result


def diffuse_image(mat):
    if not mat or not mat.node_tree:
        return None
    if mat.get('mmd_lora_diffuse_image'):
        explicit = bpy.data.images.get(mat['mmd_lora_diffuse_image'])
        if explicit:
            return explicit
    # MMD Tools has separate base, sphere and toon image nodes. Their presence
    # makes a generic "only one texture" heuristic fail on ordinary PMX models.
    base = mat.node_tree.nodes.get('mmd_base_tex')
    if base is not None and base.type == 'TEX_IMAGE' and base.image:
        return base.image
    nodes = [n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image]
    # Prefer the existing base-color link; do not accidentally choose a normal map.
    for node in mat.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            for link in node.inputs['Base Color'].links:
                if link.from_node.type == 'TEX_IMAGE':
                    return link.from_node.image
    return nodes[0].image if len(nodes) == 1 else None


def toon_material(source, style, settings=None):
    _, threshold, shadow = STYLES[style]
    role = material_role(source) if settings and settings.material_profiles else 'GENERIC'
    if source:
        source.use_fake_user = True
    shader = source.node_tree.nodes.get('mmd_shader') if source and source.node_tree else None
    alpha_socket = shader.inputs.get('Alpha') if shader else None
    opacity = float(alpha_socket.default_value) if alpha_socket else (source.diffuse_color[3] if source else 1)
    # Authored semitransparent shadow/highlight layers can rely on MMD's
    # backface and blend logic. Preserve that graph rather than flatten it.
    if shader and opacity < .999 and role != 'HAIR_HIGHLIGHT':
        mat = source.copy()
        mat.name = source.name + '_LoRA_' + style
        mat['mmd_lora_material'] = True
        mat['mmd_lora_source'] = source.name
        mat['mmd_lora_preserved_layer'] = True
        mat['mmd_lora_role'] = role
        if role == 'FACE_SHADOW':
            mat.node_tree.nodes['mmd_shader'].inputs['Alpha'].default_value = opacity * settings.face_shadow_strength
        return mat
    mat = bpy.data.materials.new((source.name if source else 'Material') + '_LoRA_' + style)
    mat.use_nodes = True
    mat['mmd_lora_material'] = True
    mat['mmd_lora_source'] = source.name if source else ''
    mat['mmd_lora_role'] = role
    mat.use_backface_culling = source.use_backface_culling if source else False
    # Reading the saved socket also works when opening an MMD .blend while
    # MMD Tools is disabled and mmd_material's RNA property is unavailable.
    mat.diffuse_color = (*tuple(source.diffuse_color[:3]), opacity) if source else (.8, .8, .8, opacity)
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new('ShaderNodeOutputMaterial'); out.location = (820, 80)
    diffuse = tree.nodes.new('ShaderNodeBsdfDiffuse'); diffuse.location = (-700, -180)
    rgb = tree.nodes.new('ShaderNodeShaderToRGB'); rgb.location = (-490, -180)
    tree.links.new(diffuse.outputs[0], rgb.inputs[0])
    ramp = tree.nodes.new('ShaderNodeValToRGB'); ramp.location = (-280, -120)
    ramp.color_ramp.interpolation = 'CONSTANT'
    ramp.color_ramp.elements[0].position = 0
    ramp.color_ramp.elements[0].color = (shadow * 0.93, shadow * 0.96, shadow, 1)
    ramp.color_ramp.elements[1].position = threshold
    ramp.color_ramp.elements[1].color = (1, 1, 1, 1)
    tree.links.new(rgb.outputs[0], ramp.inputs[0])
    multiply = tree.nodes.new('ShaderNodeMixRGB'); multiply.blend_type = 'MULTIPLY'
    multiply.location = (30, 100); multiply.inputs[0].default_value = 1
    multiply.inputs[1].default_value = tuple(source.diffuse_color) if source else (0.8, 0.8, 0.8, 1)
    tree.links.new(ramp.outputs[0], multiply.inputs[2])
    if role == 'FACE':
        tree.links.new(face_light(tree, settings), multiply.inputs[2])
    elif role in {'EYE', 'DECAL'}:
        # Shader to RGB evaluates transparent layers against the probe in
        # EEVEE's dithered path. Thin iris/decal layers must use direct color.
        tree.links.remove(multiply.inputs[2].links[0])
        multiply.inputs[2].default_value = (1, 1, 1, 1)
        tree.nodes.remove(rgb)
        tree.nodes.remove(diffuse)
        tree.nodes.remove(ramp)
    emission = tree.nodes.new('ShaderNodeEmission'); emission.location = (260, 100)
    tree.links.new(multiply.outputs[0], emission.inputs['Color'])
    if role == 'HAIR':
        tree.links.new(hair_color(tree, multiply.outputs[0]), emission.inputs['Color'])
    image = diffuse_image(source)
    if role == 'HAIR_HIGHLIGHT':
        opacity *= settings.hair_highlight_strength
        if image:
            image = alpha_image(image)
    transparent = tree.nodes.new('ShaderNodeBsdfTransparent'); transparent.location = (260, -80)
    mix = tree.nodes.new('ShaderNodeMixShader'); mix.location = (570, 80)
    mix.inputs[0].default_value = opacity
    tree.links.new(transparent.outputs[0], mix.inputs[1])
    tree.links.new(emission.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], out.inputs[0])
    if image:
        tex = tree.nodes.new('ShaderNodeTexImage'); tex.image = image; tex.location = (-680, 150)
        tree.links.new(tex.outputs['Color'], multiply.inputs[1])
        alpha = tree.nodes.new('ShaderNodeMath'); alpha.operation = 'MULTIPLY'; alpha.location = (0, -200)
        alpha.inputs[1].default_value = opacity
        tree.links.new(tex.outputs['Alpha'], alpha.inputs[0])
        tree.links.new(alpha.outputs[0], mix.inputs[0])
    if hasattr(mat, 'surface_render_method'):
        # Locking every game's eye shells to BLENDED can reorder the iris and
        # sclera (notably ZZZ). The sampled WW eye stack needs this path.
        mat.surface_render_method = 'BLENDED' if role == 'EYE' and style == 'WUWA' else 'DITHERED'
    return mat


class MLSettings(bpy.types.PropertyGroup):
    armature: PointerProperty(name='角色骨架', type=bpy.types.Object, poll=lambda self, obj: obj.type == 'ARMATURE')
    style: EnumProperty(name='游戏风格', items=[(k, v[0], '风格近似预设，非官方着色器') for k, v in STYLES.items()])
    output: StringProperty(name='输出目录', subtype='DIR_PATH', default='//lora_dataset')
    textures: StringProperty(name='贴图目录', subtype='DIR_PATH')
    trigger: StringProperty(name='角色触发词', default='my_character')
    tags: StringProperty(name='附加标签', default='anime style, simple background')
    resolution: IntProperty(name='分辨率', default=1024, min=256, max=4096)
    preview_group: EnumProperty(name='预览构图', items=[(v, label, '') for v, label in zip(GROUPS, ('肖像', '半身', '全身', '衣服细节'))])
    front: FloatProperty(name='角色正面角度', default=0, min=-180, max=180, description='0 表示朝向世界 -Y；90 表示朝向 +X')
    margin: FloatProperty(name='构图留白', default=1.18, min=1.02, max=2)
    expression: EnumProperty(name='表情', items=lambda self, context: enum_presets(self, 'expressions'))
    pose: EnumProperty(name='姿势', items=lambda self, context: enum_presets(self, 'poses'))
    expression_strength: FloatProperty(name='表情强度', default=1, min=0, max=1)
    pose_strength: FloatProperty(name='姿势强度', default=1, min=0, max=1)
    head_angles: FloatVectorProperty(name='头部俯仰 / 转向 / 歪头', size=3, default=(0, 0, 0), min=-45, max=45)
    hand_pose: EnumProperty(name='手型模板', items=[('AUTO', '跟随姿势', '', 5), ('KEEP', '保留当前手型', '', 0), ('RELAXED', '轻微放松', '', 1), ('FIST', '握拳', '', 2), ('POINT', '食指指向', '', 3), ('PEACE', '剪刀手', '', 4)], default='AUTO')
    preset_name: StringProperty(name='新预设名称', description='留空时覆盖当前选中的预设')
    capture_all_keys: BoolProperty(name='保存全部形态键', default=False, description='关闭时仅保存识别出的脸部键，避免绑定体型变化')
    camera_preset: EnumProperty(name='镜头预设', items=lambda self, context: enum_presets(self, 'cameras'))
    camera_projection: EnumProperty(name='投影', items=[('ORTHO', '正交', ''), ('PERSP', '透视', '')], default='ORTHO')
    camera_lens: FloatProperty(name='焦距 (mm)', default=85, min=20, max=200)
    camera_azimuth: FloatProperty(name='环绕角度 (°)', default=0, min=-180, max=180)
    camera_elevation: FloatProperty(name='俯仰角度 (°)', default=0, min=-60, max=60)
    camera_roll: FloatProperty(name='画面倾斜 (°)', default=0, min=-45, max=45)
    camera_zoom: FloatProperty(name='构图放大', default=1, min=.5, max=2)
    camera_shift_x: FloatProperty(name='水平移轴', default=0, min=-.4, max=.4)
    camera_shift_y: FloatProperty(name='垂直移轴', default=0, min=-.4, max=.4)
    camera_target_offset: FloatVectorProperty(name='对焦偏移（身高比例）', size=3, default=(0, 0, 0), min=-.5, max=.5)
    camera_aspect: EnumProperty(name='画幅', items=[('SQUARE', '1:1 方形', ''), ('PORTRAIT', '3:4 竖幅', ''), ('LANDSCAPE', '4:3 横幅', '')], default='SQUARE')
    portrait_focus: EnumProperty(name='肖像范围', items=[('HEAD', '头肩（按脸部定位）', ''), ('FACE', '脸部特写', '')], default='HEAD')
    dataset_variation: EnumProperty(name='36 张拍摄方案', items=[('CYCLE', '二次元姿势 / 表情 / 镜头联动', '每张使用编排的手势、表情、头部与镜头'), ('CURRENT', '当前姿势 / 表情 / 镜头参数', '沿用当前控制，仍拍四组各九个目标')], default='CYCLE')
    plan_index: IntProperty(name='组内镜头', default=1, min=1, max=9)
    physics_mode: EnumProperty(name='衣服 / 头发物理', items=[('AUTO', '有 MMD 物理时自动稳定', '', 0), ('MMD', '必须运行 MMD 物理', '', 1), ('OFF', '关闭', '', 2)], default='AUTO')
    physics_source: StringProperty(name='原 PMX / PMD', subtype='FILE_PATH')
    physics_scale: FloatProperty(name='源模型导入比例', default=.08, min=.0001, max=100)
    physics_transition: IntProperty(name='摆姿过渡帧', default=20, min=1, max=120)
    physics_frames: IntProperty(name='物理稳定帧', default=60, min=10, max=240)
    physics_substeps: IntProperty(name='每帧物理子步', default=10, min=1, max=60)
    physics_damping: FloatProperty(name='静态照片阻尼', default=.8, min=0, max=.99)
    physics_joint_mode: EnumProperty(name='物理方式', items=[('PROXY', '衣服 / 头发骨链代理', '重力、距离约束和身体 / 坐凳碰撞，驱动原始骨骼'), ('AUTHORED', '原始 MMD 刚体', '')], default='PROXY')
    physics_tolerance: FloatProperty(name='稳定阈值（身高比例）', default=.003, min=.0001, max=.03, precision=4)
    physics_strict: BoolProperty(name='未稳定时停止出图', default=True)
    physics_seat_collision: BoolProperty(name='衣服与坐凳碰撞', default=False, description='复杂披风可能卡在凳面，开启后须检查收敛和画面')
    developer_tools: BoolProperty(name='显示开发用三视图测试', default=False)
    mapping_report: StringProperty(default='')
    material_profiles: BoolProperty(name='头发 / 脸部优化', default=True)
    hair_highlight_strength: FloatProperty(name='头发高光层强度', default=.22, min=0, max=1)
    face_fill: FloatProperty(name='脸部柔和补光', default=.92, min=.5, max=1)
    face_shadow_strength: FloatProperty(name='脸部阴影层强度', default=.3, min=0, max=1)
    presets_json: StringProperty(default='')
    running: BoolProperty(default=False, options={'SKIP_SAVE'})
    progress: StringProperty(default='等待开始', options={'SKIP_SAVE'})
    ui_section: EnumProperty(name='功能', items=[('MATERIAL', '材质', '', 0), ('PERFORMANCE', '姿势', '', 1), ('CAMERA', '镜头', '', 2), ('PHYSICS', '物理', '', 4), ('RENDER', '输出', '', 3)], default='PERFORMANCE')


class ML_OT_materials(bpy.types.Operator):
    bl_idname = 'ml.materials'; bl_label = '应用三渲二材质（可撤销）'; bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            settings = context.scene.ml_settings
            cache = {}
            missing = 0
            for obj in model_meshes(settings):
                for slot in obj.material_slots:
                    source = slot.material
                    if source is None:
                        continue
                    if source.get('mmd_lora_material'):
                        source = bpy.data.materials.get(source.get('mmd_lora_source', '')) or source
                    if not diffuse_image(source):
                        missing += 1
                    if source.name not in cache:
                        cache[source.name] = toon_material(source, settings.style, settings)
                    slot.material = cache[source.name]
            context.scene.render.engine = eevee_engine(context.scene)
            self.report({'INFO'}, f'已生成 {len(cache)} 个材质；{missing} 个槽未识别底色贴图。原材质仍保留。')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


def eevee_engine(scene):
    engines = scene.render.bl_rna.properties['engine'].enum_items.keys()
    return 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in engines else 'BLENDER_EEVEE'


class ML_OT_textures(bpy.types.Operator):
    bl_idname = 'ml.textures'; bl_label = '重连丢失贴图'; bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.ml_settings
        directory = Path(bpy.path.abspath(settings.textures))
        if not settings.textures or not directory.is_dir():
            self.report({'ERROR'}, '请指定有效的贴图目录。'); return {'CANCELLED'}
        index = {}
        for file in directory.rglob('*'):
            if file.is_file():
                index.setdefault(file.name.lower(), []).append(file)
        repaired = ambiguous = 0
        try:
            meshes = model_meshes(settings)
            images = {n.image for obj in meshes for slot in obj.material_slots if slot.material and slot.material.node_tree
                      for n in slot.material.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image}
            for image in images:
                if image.packed_file or image.source != 'FILE' or Path(bpy.path.abspath(image.filepath)).is_file():
                    continue
                matches = index.get(image.filepath.replace('\\', '/').split('/')[-1].lower(), [])
                if len(matches) == 1:
                    image.filepath = str(matches[0]); image.reload(); repaired += 1
                elif len(matches) > 1:
                    ambiguous += 1
            self.report({'INFO'}, f'已重连 {repaired} 张贴图；{ambiguous} 张重名需手动选择。')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_assign_textures(bpy.types.Operator):
    bl_idname = 'ml.assign_textures'; bl_label = '按材质名匹配底色贴图'; bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.ml_settings
        directory = Path(bpy.path.abspath(settings.textures))
        if not settings.textures or not directory.is_dir():
            self.report({'ERROR'}, '请指定有效的贴图目录。'); return {'CANCELLED'}
        try:
            index = {}
            for file in directory.rglob('*'):
                if file.suffix.lower() in {'.png', '.jpg', '.jpeg', '.tga', '.bmp', '.tif', '.tiff', '.webp'}:
                    index.setdefault(file.stem.casefold(), []).append(file)
            materials = {slot.material for obj in model_meshes(settings) for slot in obj.material_slots if slot.material}
            matched = 0
            for material in materials:
                if material.get('mmd_lora_material'):
                    continue
                names = [material.name]
                mmd = getattr(material, 'mmd_material', None)
                if mmd:
                    names.extend([getattr(mmd, 'name_j', ''), getattr(mmd, 'name_e', '')])
                candidates = {file for name in names if name for suffix in ('', '_BaseColor', '_Albedo', '_Diffuse', '_Color')
                              for file in index.get((name + suffix).casefold(), [])}
                if len(candidates) != 1:
                    continue
                image = bpy.data.images.load(str(next(iter(candidates))), check_existing=True)
                material.use_nodes = True
                tree = material.node_tree
                principled = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
                if principled:
                    tex = tree.nodes.new('ShaderNodeTexImage'); tex.image = image
                    tree.links.new(tex.outputs['Color'], principled.inputs['Base Color'])
                    tree.links.new(tex.outputs['Alpha'], principled.inputs['Alpha'])
                else:
                    # MMD node groups may have arbitrary sockets; retain the graph and mark the source explicitly.
                    tex = tree.nodes.new('ShaderNodeTexImage'); tex.image = image; tex.label = 'LoRA Base Color'
                material['mmd_lora_diffuse_image'] = image.name
                matched += 1
            self.report({'INFO'}, f'已匹配 {matched} 个材质。未匹配或重名时跳过；请在应用三渲二前操作。')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_preset(bpy.types.Operator):
    bl_idname = 'ml.preset'; bl_label = '表情 / 姿势'; bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[(v, v, '') for v in ('EXPRESSION', 'POSE', 'SAVE_EXPRESSION', 'SAVE_POSE', 'CAMERA', 'SAVE_CAMERA', 'RESET_EXPRESSION', 'RESET_POSE')])

    def execute(self, context):
        settings = context.scene.ml_settings
        try:
            meshes = model_meshes(settings)
            saved = custom_presets(settings)
            if self.action in {'EXPRESSION', 'RESET_EXPRESSION'}:
                if self.action == 'RESET_EXPRESSION':
                    settings.expression = 'neutral'
                changed = expression_apply(settings, meshes, 'neutral' if self.action == 'RESET_EXPRESSION' else settings.expression)
                self.report({'INFO'}, f'表情映射 {len(changed)} 个形态键；无映射时为中性。')
            elif self.action == 'POSE':
                changed = pose_apply(settings, settings.pose)
                self.report({'INFO'}, f'姿势映射 {len(changed)} 根骨骼；请检查 IK 和骨骼轴向。')
            elif self.action == 'RESET_POSE':
                for bone in settings.armature.pose.bones:
                    bone.matrix_basis = Matrix.Identity(4)
                settings.head_angles = (0, 0, 0)
            elif self.action == 'CAMERA':
                preset = saved['cameras'].get(settings.camera_preset)
                if preset:
                    camera_apply(settings, preset)
            elif self.action.startswith('SAVE'):
                category = {'SAVE_EXPRESSION': 'expressions', 'SAVE_POSE': 'poses', 'SAVE_CAMERA': 'cameras'}[self.action]
                prop = {'expressions': 'expression', 'poses': 'pose', 'cameras': 'camera_preset'}[category]
                name = settings.preset_name.strip() or getattr(settings, prop)
                if len(name) > 80:
                    raise ValueError('预设名称请控制在 80 字符以内。')
                if category == 'cameras' and name == 'AUTO':
                    raise ValueError('请为镜头填写一个新的预设名称。')
                saved[category][name] = capture_expression(settings, meshes) if category == 'expressions' else capture_pose(settings) if category == 'poses' else capture_camera(settings)
                store_presets(settings, saved)
                setattr(settings, prop, name)
                if category == 'expressions':
                    settings.expression_strength = 1
                if category == 'poses':
                    settings.pose_strength = 1; settings.hand_pose = 'KEEP'; settings.head_angles = (0, 0, 0)
            if self.action.startswith('SAVE'):
                self.report({'INFO'}, '已保存命名预设；可随 .blend 保存或导出 JSON。')
            context.view_layer.update()
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_export_presets(bpy.types.Operator, ExportHelper):
    bl_idname = 'ml.export_presets'; bl_label = '导出预设 JSON'
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            write_json(self.filepath, custom_presets(context.scene.ml_settings))
            self.report({'INFO'}, '预设已导出。'); return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_import_presets(bpy.types.Operator, ImportHelper):
    bl_idname = 'ml.import_presets'; bl_label = '导入预设 JSON'; bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            from .core import validate_library
            path = Path(self.filepath)
            if path.stat().st_size > 8*1024*1024:
                raise ValueError('预设文件不能超过 8 MiB。')
            incoming = validate_library(json.loads(path.read_text(encoding='utf-8-sig')))
            saved = custom_presets(context.scene.ml_settings)
            for category in ('expressions', 'poses', 'cameras', 'bone_map'):
                saved[category].update(incoming[category])
            store_presets(context.scene.ml_settings, saved)
            self.report({'INFO'}, '已导入预设；同名项目已更新。'); return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_vpd(bpy.types.Operator, ImportHelper):
    bl_idname = 'ml.import_vpd'; bl_label = '导入 VPD 并保存姿势'; bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.vpd'
    filter_glob: StringProperty(default='*.vpd', options={'HIDDEN'})
    scale: FloatProperty(name='MMD 姿势缩放', default=.08, min=.0001, max=10)

    def execute(self, context):
        settings = context.scene.ml_settings
        try:
            import importlib
            from .core import normalized_name
            meshes = model_meshes(settings)
            package = next((item.module for item in context.preferences.addons if item.module == 'mmd_tools' or item.module.endswith('.mmd_tools')), None)
            if not package:
                raise ValueError('请先启用 MMD Tools，才能导入 VPD。')
            if settings.armature.animation_data and settings.armature.animation_data.action:
                raise ValueError('请先在角色副本中解除活动 Action，再导入静态 VPD。')
            path = Path(self.filepath)
            if path.stat().st_size > 1024*1024:
                raise ValueError('VPD 文件不能超过 1 MiB。')
            data = importlib.import_module(package + '.core.vpd').File()
            data.load(filepath=str(path))
            converter = importlib.import_module(package + '.core.vmd.importer').BoneConverter
            indexed = {normalized_name(alias): bone for bone in settings.armature.pose.bones for alias in aliases(bone) if alias}
            matched = [(record, indexed[normalized_name(record.bone_name)]) for record in data.bones if normalized_name(record.bone_name) in indexed]
            if not matched:
                raise ValueError('VPD 中没有匹配当前骨架的骨骼。')
            matrices = []
            for record, bone in matched:
                convert = converter(bone, self.scale)
                matrix = Matrix.Translation(convert.convert_location(record.location)) @ convert.convert_rotation(record.rotation).to_matrix().to_4x4()
                if any(not math.isfinite(v) for row in matrix for v in row):
                    raise ValueError('VPD 骨骼数据包含非有限数值。')
                matrices.append((bone, matrix))
            if any(not math.isfinite(m.weight) or not -10 <= m.weight <= 10 for m in data.morphs):
                raise ValueError('VPD 表情权重无效。')
            name = settings.preset_name.strip() or path.stem[:80]
            if len(name) > 80:
                raise ValueError('预设名称请控制在 80 字符以内。')
            for bone in settings.armature.pose.bones:
                bone.matrix_basis = Matrix.Identity(4)
            for bone, matrix in matrices:
                bone.matrix_basis = matrix
            context.view_layer.update()
            saved = custom_presets(settings)
            saved['poses'][name] = capture_pose(settings)
            morphs = {normalized_name(m.morph_name): m.weight for m in data.morphs}
            matched_morphs = [(key, morphs[normalized_name(key.name)]) for _, keys in shape_blocks(meshes)
                              for key in keys[1:] if normalized_name(key.name) in morphs]
            if matched_morphs:
                expression_apply(settings, meshes, 'neutral')
                for key, weight in matched_morphs:
                    key.value = weight
                saved['expressions'][name] = {obj.name: {key.name: key.value for key in keys[1:] if normalized_name(key.name) in morphs}
                                               for obj, keys in shape_blocks(meshes)}
            store_presets(settings, saved)
            settings.pose = name; settings.pose_strength = 1; settings.hand_pose = 'KEEP'; settings.head_angles = (0, 0, 0)
            if matched_morphs:
                settings.expression = name; settings.expression_strength = 1
            self.report({'INFO'}, 'VPD 已导入并保存为：' + name)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_mapping(bpy.types.Operator):
    bl_idname = 'ml.mapping_report'; bl_label = '检查骨骼与表情映射'

    def execute(self, context):
        settings = context.scene.ml_settings
        try:
            from .core import EXPRESSION_LABELS
            meshes = model_meshes(settings)
            before = [(key, key.value) for _, keys in shape_blocks(meshes) for key in keys]
            old_strength = settings.expression_strength
            try:
                settings.expression_strength = 1
                expressions = {name: expression_apply(settings, meshes, name) for name in EXPRESSION_LABELS}
            finally:
                settings.expression_strength = old_strength
                for key, value in before:
                    key.value = value
            report = {'bones': {key: bone.name if (bone := find_bone(settings.armature, key, settings)) else None for key in BONES},
                      'expressions': expressions,
                      'constraints': [bone.name for bone in settings.armature.pose.bones if any(not c.mute for c in bone.constraints)]}
            settings.mapping_report = json.dumps(report, ensure_ascii=False)
            text = bpy.data.texts.get('AniForge Mapping Report') or bpy.data.texts.new('AniForge Mapping Report')
            text.clear(); text.write(json.dumps(report, ensure_ascii=False, indent=2))
            missing = [name for name, value in report['bones'].items() if not value]
            self.report({'INFO'}, '映射报告已写入文本编辑器；缺失骨骼：' + (', '.join(missing) or '无'))
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


def dataset_plan(settings):
    return current_recipe(settings) if settings.dataset_variation == 'CURRENT' else recipe()


def update_preview_camera(context, settings, meshes, shot):
    effective = ShotSettings(settings, shot)
    pose_apply(effective, shot.get('pose', settings.pose))
    expression_apply(effective, meshes, shot.get('expression', settings.expression))
    context.view_layer.update()
    camera = next((obj for obj in context.scene.objects if obj.type == 'CAMERA' and obj.get('aniforge_preview_for') == settings.armature.name), None)
    if not camera:
        camera = bpy.data.objects.new('AniForge Preview Camera', bpy.data.cameras.new('AniForge Preview Camera'))
        context.scene.collection.objects.link(camera); camera['aniforge_preview_for'] = settings.armature.name
    context.scene.render.pixel_aspect_x = context.scene.render.pixel_aspect_y = 1
    configure_camera(context, settings, meshes, camera, shot)
    context.scene.camera = camera
    if context.area and context.area.type == 'VIEW_3D':
        context.space_data.region_3d.view_perspective = 'CAMERA'
    return camera


class ML_OT_shot_plan(bpy.types.Operator):
    bl_idname = 'ml.shot_plan'; bl_label = '预览编排镜头（不出图）'; bl_options = {'REGISTER', 'UNDO'}
    report_only: BoolProperty(default=False)

    def execute(self, context):
        try:
            settings = context.scene.ml_settings
            plan = dataset_plan(settings)
            if self.report_only:
                text = bpy.data.texts.get('AniForge 36 Shot Plan') or bpy.data.texts.new('AniForge 36 Shot Plan')
                text.clear(); text.write(json.dumps({'shots': plan, 'coverage': dataset_coverage(plan), 'total': len(plan)}, ensure_ascii=False, indent=2))
                self.report({'INFO'}, '36 张拍摄清单已写入文本编辑器，没有渲染图片。')
            else:
                shot = next(s for s in plan if s['group'] == settings.preview_group and s['index'] == settings.plan_index)
                update_preview_camera(context, settings, model_meshes(settings), shot)
                self.report({'INFO'}, f'{shot["index"]}/9 {shot["title"]}：姿势、表情和镜头已联动，没有渲染图片。')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_physics_import(bpy.types.Operator, ImportHelper):
    bl_idname = 'ml.import_physics'; bl_label = '从原模型补入物理数据'; bl_options = {'REGISTER', 'UNDO'}
    filter_glob: StringProperty(default='*.pmx;*.pmd', options={'HIDDEN'})

    def execute(self, context):
        try:
            settings = context.scene.ml_settings
            info = import_physics(context, settings, self.filepath or settings.physics_source)
            self.report({'INFO'}, f'已补入 {info["bodies"]} 个刚体 / {info["joints"]} 个关节；当前网格和材质继续保留。')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_frame_camera(bpy.types.Operator):
    bl_idname = 'ml.frame_camera'; bl_label = '应用姿势表情并生成预览相机'; bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            settings = context.scene.ml_settings
            meshes = model_meshes(settings)
            shot = dict(group=settings.preview_group, azimuth=0)
            update_preview_camera(context, settings, meshes, shot)
            self.report({'INFO'}, '预览相机已更新，没有渲染图片。'); return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


def bounds(meshes, depsgraph):
    coords = [obj.evaluated_get(depsgraph).matrix_world @ Vector(corner)
              for obj in meshes if not obj.hide_render
              for corner in obj.evaluated_get(depsgraph).bound_box]
    if not coords:
        raise ValueError('没有可渲染的角色网格。')
    lo = Vector(tuple(min(v[i] for v in coords) for i in range(3)))
    hi = Vector(tuple(max(v[i] for v in coords) for i in range(3)))
    if hi.z - lo.z < 0.0001:
        raise ValueError('角色高度无效，请检查模型。')
    return lo, hi


def aim(obj, target):
    obj.rotation_euler = (target - obj.location).to_track_quat('-Z', 'Y').to_euler()


class RenderSession:
    def __init__(self, context, shots=None):
        self.scene = context.scene
        self.settings = self.scene.ml_settings
        self.plan = list(shots) if shots is not None else []
        self.meshes = model_meshes(self.settings)
        if self.settings.armature.animation_data and self.settings.armature.animation_data.action:
            raise ValueError('请先在副本中解除角色 Action；动画可能覆盖预设。')
        self.created = []; self.manifest = None; self.folder = None
        self.pose = [(b, b.rotation_mode, b.matrix_basis.copy()) for b in self.settings.armature.pose.bones]
        self.constraint_mutes = [(c, c.mute) for b in self.settings.armature.pose.bones for c in b.constraints]
        self.seated_state = self.settings.armature.get(SEATED_STATE)
        self.shapes = [(key, key.value) for _, keys in shape_blocks(self.meshes) for key in keys]
        r = self.scene.render
        self.saved = (self.scene.camera, r.engine, r.filepath, r.resolution_x, r.resolution_y,
                      r.resolution_percentage, r.image_settings.file_format, r.image_settings.color_mode,
                      r.film_transparent, self.scene.world, self.scene.view_settings.view_transform,
                      r.use_file_extension, r.use_border, r.use_compositing, r.use_sequencer, r.pixel_aspect_x, r.pixel_aspect_y)
        self.visibility = [(o, o.hide_render) for o in self.scene.objects if o.type in {'MESH', 'LIGHT'} and o not in self.meshes]
        self.supports = []
        self.physics = MMDPhysics(context, self.settings)

    def create_object(self, name, kind):
        data = bpy.data.cameras.new(name) if kind == 'CAMERA' else bpy.data.lights.new(name, 'AREA')
        obj = bpy.data.objects.new(name, data); self.scene.collection.objects.link(obj)
        self.created.append((obj, data, kind))
        return obj

    def setup(self, context):
        if not self.settings.output.strip():
            raise ValueError('输出目录不能为空。')
        root = Path(bpy.path.abspath(self.settings.output)).resolve()
        self.folder = root / (safe_name(self.settings.trigger) + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        self.folder.mkdir(parents=True, exist_ok=False)
        self.manifest = {'status': 'running', 'style': self.settings.style, 'blender': bpy.app.version_string,
                         'warning': 'Game styles are approximations. Review poses, crops and captions before training.',
                         'shots': [], 'recipe': self.plan, 'planned_coverage': dataset_coverage(self.plan),
                         'physics_settings': {name: getattr(self.settings, name) for name in ('physics_mode', 'physics_transition', 'physics_frames', 'physics_substeps', 'physics_damping', 'physics_joint_mode', 'physics_seat_collision', 'physics_tolerance', 'physics_strict')},
                         'camera_settings': capture_camera(self.settings),
                         'performance': {'pose_strength': self.settings.pose_strength, 'expression_strength': self.settings.expression_strength,
                                         'hand_pose': self.settings.hand_pose, 'head_angles': list(self.settings.head_angles)}}
        self.flush()
        for obj, _ in self.visibility:
            obj.hide_render = True
        self.camera = self.create_object('LoRA Camera', 'CAMERA'); self.camera.data.type = 'ORTHO'
        self.scene.camera = self.camera
        lo, hi = bounds(self.meshes, context.evaluated_depsgraph_get())
        center = (lo + hi) / 2; h = hi.z - lo.z
        for name, offset, energy, size in [('Key', (1, -1.5, 2), 900, 1.4), ('Fill', (-1, -0.5, 1), 350, 2)]:
            lamp = self.create_object('LoRA ' + name, 'LIGHT')
            lamp.location = center + Vector(offset) * h
            lamp.data.energy = energy * h * h / 4; lamp.data.shape = 'DISK'; lamp.data.size = size * h
            aim(lamp, center)
        self.world = bpy.data.worlds.new('LoRA Background'); self.world.use_nodes = True
        self.world.node_tree.nodes['Background'].inputs[0].default_value = (0.16, 0.16, 0.16, 1)
        self.world.node_tree.nodes['Background'].inputs[1].default_value = 0.4
        self.scene.world = self.world
        r = self.scene.render; r.engine = eevee_engine(self.scene)
        r.resolution_x, r.resolution_y = resolution(self.settings); r.resolution_percentage = 100
        r.pixel_aspect_x = r.pixel_aspect_y = 1
        r.image_settings.file_format = 'PNG'; r.image_settings.color_mode = 'RGB'; r.film_transparent = False
        r.use_file_extension = True; r.use_border = False; r.use_compositing = False; r.use_sequencer = False
        self.scene.view_settings.view_transform = 'Standard'

    def flush(self):
        write_json(self.folder / 'manifest.json', self.manifest)

    def reset_character(self):
        restore_seated(self.settings.armature)
        for bone, mode, matrix in self.pose:
            bone.rotation_mode = mode; bone.matrix_basis = matrix
        for constraint, muted in self.constraint_mutes:
            constraint.mute = muted
        if self.seated_state:
            self.settings.armature[SEATED_STATE] = self.seated_state
        for key, value in self.shapes:
            key.value = value

    def support_box(self, context, name, center, dimensions, render=True):
        data = bpy.data.meshes.new(name)
        vertices = [(x*.5, y*.5, z*.5) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        data.from_pydata(vertices, [], [(0, 4, 6, 2), (1, 3, 7, 5), (0, 1, 5, 4), (2, 6, 7, 3), (0, 2, 3, 1), (4, 5, 7, 6)])
        obj = bpy.data.objects.new(name, data); self.scene.collection.objects.link(obj)
        obj.location = center; obj.scale = dimensions; obj.hide_render = not render
        self.supports.append((obj, data)); self.created.append((obj, data, 'MESH'))
        if render:
            if not hasattr(self, 'support_material'):
                self.support_material = bpy.data.materials.new('AniForge Neutral Seat')
                self.support_material.use_nodes = True
                self.support_material.diffuse_color = (.12, .12, .12, 1)
                if self.support_material.node_tree:
                    for node in self.support_material.node_tree.nodes:
                        if node.type == 'BSDF_PRINCIPLED':
                            node.inputs['Base Color'].default_value = (.12, .12, .12, 1)
                            node.inputs['Roughness'].default_value = .8
            data.materials.append(self.support_material)
        return obj

    def prepare_supports(self, context, shot):
        for obj, data in self.supports:
            self.created.remove((obj, data, 'MESH'))
            bpy.data.objects.remove(obj, do_unlink=True); bpy.data.meshes.remove(data)
        self.supports.clear()
        lo, hi = bounds(self.meshes, context.evaluated_depsgraph_get())
        h = hi.z-lo.z
        ankles = [b for key in ('left_ankle', 'right_ankle') if (b := find_bone(self.settings.armature, key, self.settings))]
        ground = min((self.settings.armature.matrix_world @ b.tail).z for b in ankles) if ankles else lo.z
        if self.physics.model:
            self.support_box(context, 'AniForge Physics Ground', Vector((0, 0, ground-h*.02)), (h*5, h*5, h*.04), False)
        if shot.get('support') == 'seat' or shot['pose'] in {'sit_chair', 'sit_side'}:
            hips = find_bone(self.settings.armature, 'lower_body', self.settings)
            if not hips:
                raise ValueError('坐姿需要下半身骨骼来定位坐凳。')
            point = self.settings.armature.matrix_world @ hips.head
            angle = math.radians(self.settings.front)
            forward = Vector((math.sin(angle), -math.cos(angle), 0))
            seat_top = point.z-h*.06; thickness = h*.035
            center = point+forward*h*.025; center.z = seat_top-thickness*.5
            seat = self.support_box(context, 'AniForge Seat', center, (h*.32, h*.26, thickness))
            seat.rotation_euler.z = angle
            # Four slim legs keep clothing/leg silhouettes visible.
            side = Vector((math.cos(angle), math.sin(angle), 0))
            leg_height = max(seat_top-thickness-ground, h*.02)
            for x in (-1, 1):
                for y in (-1, 1):
                    p = center+side*h*.13*x+forward*h*.1*y; p.z = ground+leg_height*.5
                    self.support_box(context, 'AniForge Seat Leg', p, (h*.025, h*.025, leg_height))

    def prepare_shot(self, context, shot):
        self.physics.clear()
        self.reset_character()
        effective = ShotSettings(self.settings, shot)
        expression_keys = expression_apply(effective, self.meshes, shot['expression'])
        pose_bones = pose_apply(effective, shot['pose'])
        context.view_layer.update()
        self.prepare_supports(context, shot)
        context.view_layer.update()
        self.physics_meta = self.physics.settle(context, [o for o, _ in self.supports], self.meshes)
        group = shot['group']
        self.camera_meta = configure_camera(context, self.settings, self.physics.meshes or self.meshes, self.camera, shot)
        folder = self.folder / group; folder.mkdir(exist_ok=True)
        name = f'{safe_name(self.settings.trigger)}_{group}_{shot["index"]:02d}'
        image = folder / (name + '.png'); self.scene.render.filepath = str(image)
        context.view_layer.update()
        return image, expression_keys, pose_bones

    def render(self, context, shot):
        image, expression_keys, pose_bones = self.prepare_shot(context, shot)
        group = shot['group']
        bpy.ops.render.render(write_still=True)
        if not image.is_file():
            raise RuntimeError('渲染未生成目标 PNG。')
        # Do not label missing or merely approximate bone presets as verified poses.
        expression = shot['expression'] if expression_keys else 'neutral'
        pose_label = shot['pose'] if shot['pose'] in custom_presets(self.settings)['poses'] else ''
        posture = ''
        if shot['pose'] in SEATED_POSES:
            thighs = [find_bone(self.settings.armature, key, self.settings) for key in ('left_leg', 'right_leg')]
            if all(b is not None and abs((b.tail-b.head).normalized().z) < .55 for b in thighs):
                posture = 'sitting'
        angle = abs((self.camera_meta['azimuth']+180) % 360-180)
        view = 'from behind' if angle > 135 else 'side view' if angle >= 70 else ''
        extra = ', '.join(value for value in (posture, view, self.settings.tags) if value)
        tags = caption(self.settings.trigger, group, expression, pose_label, extra)
        image.with_suffix('.txt').write_text(tags + '\n', encoding='utf-8')
        self.manifest['shots'].append(dict(shot, image=str(image.relative_to(self.folder)), caption=tags,
                                           mapped_shape_keys=expression_keys, mapped_bones=pose_bones,
                                           pose_requires_review=True, mapped_posture=posture, camera=self.camera_meta, physics=self.physics_meta))
        self.flush()

    def close(self, status, error=''):
        try:
            self.physics.close()
            self.reset_character()
            r = self.scene.render
            (self.scene.camera, r.engine, r.filepath, r.resolution_x, r.resolution_y,
             r.resolution_percentage, r.image_settings.file_format, r.image_settings.color_mode,
             r.film_transparent, self.scene.world, self.scene.view_settings.view_transform,
             r.use_file_extension, r.use_border, r.use_compositing, r.use_sequencer, r.pixel_aspect_x, r.pixel_aspect_y) = self.saved
            for obj, hidden in self.visibility:
                obj.hide_render = hidden
            for obj, data, kind in reversed(self.created):
                bpy.data.objects.remove(obj, do_unlink=True)
                {'CAMERA': bpy.data.cameras, 'LIGHT': bpy.data.lights, 'MESH': bpy.data.meshes}[kind].remove(data)
            if hasattr(self, 'world'):
                bpy.data.worlds.remove(self.world)
            if hasattr(self, 'support_material'):
                bpy.data.materials.remove(self.support_material)
        finally:
            self.settings.running = False
            if self.manifest is not None:
                self.manifest['status'] = status; self.manifest['error'] = error; self.flush()


class ML_OT_render(bpy.types.Operator):
    bl_idname = 'ml.render_dataset'; bl_label = '渲染三视图测试'
    test_only: BoolProperty(name='仅三视图测试', default=True)
    use_plan_shot: BoolProperty(name='测试当前编排镜头', default=False)
    _timer = None
    _session = None

    @classmethod
    def poll(cls, context):
        return not context.scene.ml_settings.running

    def finish(self, context, status, error=''):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer); self._timer = None
        if self._session:
            self._session.close(status, error)
        context.scene.ml_settings.progress = f'{status}: {self.index}/{len(self.shots)}'
        context.window_manager.progress_end()

    def execute(self, context):
        self.index = 0
        settings = context.scene.ml_settings
        self.shots = three_view_recipe(settings.preview_group, settings.pose, settings.expression) if self.test_only else dataset_plan(settings)
        if self.test_only and self.use_plan_shot:
            base = next(s for s in dataset_plan(settings) if s['group'] == settings.preview_group and s['index'] == settings.plan_index)
            self.shots = [dict(base, index=i+1, azimuth=angle, view=view) for i, (angle, view) in enumerate(((0, 'front'), (90, 'side'), (180, 'back')))]
        try:
            self._session = RenderSession(context, self.shots); self._session.setup(context)
            context.scene.ml_settings.running = True
            context.window_manager.progress_begin(0, len(self.shots))
            if bpy.app.background:
                for shot in self.shots:
                    self._session.render(context, shot); self.index += 1
                self.finish(context, 'complete'); return {'FINISHED'}
            self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.finish(context, 'failed', str(exc)); self.report({'ERROR'}, str(exc)); return {'CANCELLED'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self.finish(context, 'cancelled'); return {'CANCELLED'}
        if event.type == 'TIMER' and event.timer == self._timer:
            try:
                shot = self.shots[self.index]
                context.scene.ml_settings.progress = f'{self.index + 1}/{len(self.shots)} {shot["group"]}'
                self._session.render(context, shot); self.index += 1
                context.window_manager.progress_update(self.index)
                if self.index == len(self.shots):
                    folder = str(self._session.folder)
                    self.finish(context, 'complete'); self.report({'INFO'}, '完成：' + folder)
                    return {'FINISHED'}
                if context.area:
                    context.area.tag_redraw()
            except Exception as exc:
                self.finish(context, 'failed', str(exc)); self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self.finish(context, 'cancelled')


class ML_OT_preview(bpy.types.Operator):
    bl_idname = 'ml.preview'; bl_label = '渲染一张正面预览'
    use_plan_shot: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return not context.scene.ml_settings.running

    def execute(self, context):
        session = None
        try:
            session = RenderSession(context); session.setup(context)
            settings = context.scene.ml_settings
            shot = dict(group=settings.preview_group, index=1, azimuth=0, expression=settings.expression, pose=settings.pose)
            if self.use_plan_shot:
                shot = next(s for s in dataset_plan(settings) if s['group'] == settings.preview_group and s['index'] == settings.plan_index)
            session.render(context, shot)
            session.close('preview')
            self.report({'INFO'}, '预览已写入：' + str(session.folder))
            return {'FINISHED'}
        except Exception as exc:
            if session:
                session.close('failed', str(exc))
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_PT_panel(bpy.types.Panel):
    bl_label = 'AniForge · 姿势与渲染'; bl_idname = 'ML_PT_panel'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'MMD LoRA'

    def draw(self, context):
        settings = context.scene.ml_settings
        layout = self.layout; col = layout.column(); col.enabled = not settings.running
        col.prop(settings, 'armature')
        col.prop(settings, 'ui_section', expand=True)
        section = settings.ui_section
        if section == 'MATERIAL':
            col.prop(settings, 'style')
            col.label(text='先用 MMD Tools 导入 PMX / PMD', icon='INFO')
            col.prop(settings, 'textures'); col.operator('ml.textures'); col.operator('ml.assign_textures')
            col.prop(settings, 'material_profiles')
            if settings.material_profiles:
                col.prop(settings, 'hair_highlight_strength'); col.prop(settings, 'face_fill'); col.prop(settings, 'face_shadow_strength')
            col.operator('ml.materials')
        elif section == 'PERFORMANCE':
            col.prop(settings, 'front'); col.operator('ml.mapping_report')
            col.prop(settings, 'preset_name')
            for prop, apply, save in [('expression', 'EXPRESSION', 'SAVE_EXPRESSION'), ('pose', 'POSE', 'SAVE_POSE')]:
                box = col.box(); box.prop(settings, prop)
                box.prop(settings, 'expression_strength' if prop == 'expression' else 'pose_strength')
                if prop == 'expression':
                    box.prop(settings, 'capture_all_keys')
                else:
                    box.prop(settings, 'head_angles'); box.prop(settings, 'hand_pose')
                row = box.row(); row.operator('ml.preset', text='应用').action = apply
                row.operator('ml.preset', text='保存当前').action = save
                box.operator('ml.preset', text='清空表情' if prop == 'expression' else '恢复静置姿势').action = 'RESET_EXPRESSION' if prop == 'expression' else 'RESET_POSE'
            col.operator('ml.import_vpd')
            row = col.row(); row.operator('ml.import_presets', text='导入 JSON'); row.operator('ml.export_presets', text='导出 JSON')
            col.operator('ml.frame_camera')
        elif section == 'CAMERA':
            col.prop(settings, 'preview_group')
            if settings.preview_group == 'portrait':
                col.prop(settings, 'portrait_focus')
            col.prop(settings, 'preset_name'); col.prop(settings, 'camera_preset')
            row = col.row(); row.operator('ml.preset', text='应用镜头预设').action = 'CAMERA'
            row.operator('ml.preset', text='保存镜头预设').action = 'SAVE_CAMERA'
            col.prop(settings, 'camera_projection')
            if settings.camera_projection == 'PERSP':
                col.prop(settings, 'camera_lens')
            for name in ('camera_aspect', 'front', 'margin', 'camera_azimuth', 'camera_elevation', 'camera_roll', 'camera_zoom', 'camera_shift_x', 'camera_shift_y', 'camera_target_offset'):
                col.prop(settings, name)
            col.operator('ml.frame_camera')
        elif section == 'PHYSICS':
            col.prop(settings, 'physics_mode')
            info = physics_info(settings.armature)
            col.label(text=f'刚体 {info["bodies"]} · 动态 {info["dynamic"]} · 关节 {info["joints"]}')
            col.prop(settings, 'physics_source'); col.prop(settings, 'physics_scale')
            op = col.operator('ml.import_physics'); op.filepath = settings.physics_source
            col.prop(settings, 'physics_joint_mode')
            for name in ('physics_transition', 'physics_frames', 'physics_damping', 'physics_seat_collision', 'physics_strict', 'physics_tolerance'):
                col.prop(settings, name)
            if settings.physics_joint_mode == 'AUTHORED':
                col.prop(settings, 'physics_substeps')
            col.label(text='每张：缓慢摆姿 → 物理稳定 → 构图出图')
            col.label(text='无出图镜头预览仅检查姿势 / 构图')
        else:
            col.prop(settings, 'trigger'); col.prop(settings, 'tags')
            col.prop(settings, 'resolution'); col.prop(settings, 'output')
            col.prop(settings, 'preview_group'); col.prop(settings, 'camera_aspect')
            col.prop(settings, 'pose'); col.prop(settings, 'expression')
            col.operator('ml.preview', text='单张预览（当前选择）')
            col.separator(); col.prop(settings, 'dataset_variation')
            box = col.box(); box.label(text='肖像 9 · 半身 9 · 全身 9 · 服装 9')
            box.prop(settings, 'plan_index')
            shot = next(s for s in dataset_plan(settings) if s['group'] == settings.preview_group and s['index'] == settings.plan_index)
            box.label(text=shot['title'])
            box.label(text=f'{POSE_LABELS.get(shot["pose"], shot["pose"])} / {EXPRESSION_LABELS.get(shot["expression"], shot["expression"])}')
            box.operator('ml.shot_plan')
            box.operator('ml.preview', text='渲染此镜头 1 张（含物理）').use_plan_shot = True
            box.operator('ml.shot_plan', text='查看 36 张拍摄清单').report_only = True
            col.operator('ml.render_dataset', text='生成角色数据集：共 36 张 + 标签', icon='RENDER_STILL').test_only = False
            col.prop(settings, 'developer_tools')
            if settings.developer_tools:
                col.operator('ml.render_dataset', text='开发检查：正 / 侧 / 背').test_only = True
        layout.label(text=settings.progress)
        if settings.running:
            layout.label(text='Esc 在单张渲染结束后停止')


CLASSES = (MLSettings, ML_OT_materials, ML_OT_textures, ML_OT_assign_textures, ML_OT_preset,
           ML_OT_export_presets, ML_OT_import_presets, ML_OT_vpd, ML_OT_mapping, ML_OT_frame_camera, ML_OT_shot_plan, ML_OT_physics_import,
           ML_OT_render, ML_OT_preview, ML_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ml_settings = PointerProperty(type=MLSettings)


def unregister():
    if any(scene.ml_settings.running for scene in bpy.data.scenes):
        raise RuntimeError('请先结束批量渲染再禁用插件。')
    del bpy.types.Scene.ml_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
