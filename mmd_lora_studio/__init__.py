bl_info = {
    'name': 'AniForge - MMD LoRA Studio', 'author': 'AniForge', 'version': (0, 10, 0),
    'blender': (4, 2, 0), 'location': 'View3D > Sidebar > MMD LoRA',
    'description': 'MMD toon materials and 16 T-pose views',
    'category': 'Render',
}

import json
from datetime import datetime
from pathlib import Path

import bpy
from bpy.props import StringProperty, EnumProperty, PointerProperty, IntProperty, FloatProperty, BoolProperty, FloatVectorProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Vector

from .core import STYLES, GROUPS, GROUP_LABELS, BONES, recipe, three_view_recipe, dataset_coverage, caption, safe_name, write_json, validate_camera_library
from .material_profiles import material_role, alpha_image, face_light, hair_color
from .static_pose import find_bone, shape_blocks, apply_tpose, neutralize_face, CharacterState, has_animation
from .camera import configure_camera, capture_camera, camera_apply, resolution


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


def clamp_plan_index(settings, _context=None):
    if settings.plan_index > 4:
        settings.plan_index = 4


_CAMERA_ENUM_CACHE = {}


def camera_library(settings):
    return validate_camera_library(json.loads(settings.camera_presets_json)) if settings.camera_presets_json else {'schema_version': 4, 'cameras': {}}


def camera_items(settings, context):
    names = tuple(camera_library(settings)['cameras'])
    if names not in _CAMERA_ENUM_CACHE:
        _CAMERA_ENUM_CACHE[names] = [('AUTO', '当前相机参数', '')]+[(n, n, '') for n in names if n != 'AUTO']
    return _CAMERA_ENUM_CACHE[names]


class MLSettings(bpy.types.PropertyGroup):
    armature: PointerProperty(name='角色骨架', type=bpy.types.Object, poll=lambda self, obj: obj.type == 'ARMATURE')
    style: EnumProperty(name='游戏风格', items=[(k, v[0], '风格近似预设，非官方着色器') for k, v in STYLES.items()])
    output: StringProperty(name='输出目录', subtype='DIR_PATH', default='//lora_dataset')
    textures: StringProperty(name='贴图目录', subtype='DIR_PATH')
    trigger: StringProperty(name='角色触发词', default='my_character')
    tags: StringProperty(name='附加标签', default='anime style, simple background')
    resolution: IntProperty(name='分辨率', default=1024, min=256, max=4096)
    preview_group: EnumProperty(name='取景范围', items=[(v, label, '') for v, label in GROUP_LABELS.items()])
    plan_index: IntProperty(name='视角（1正 / 2左 / 3右 / 4背）', default=1, min=1, max=4, update=clamp_plan_index)
    front: FloatProperty(name='角色正面角度', default=0, min=-180, max=180, description='0 表示朝向世界 -Y；90 表示朝向 +X')
    margin: FloatProperty(name='构图留白', default=1.18, min=1.02, max=2)
    camera_projection: EnumProperty(name='投影', items=[('ORTHO', '正交', ''), ('PERSP', '透视', '')], default='ORTHO')
    camera_lens: FloatProperty(name='焦距 (mm)', default=85, min=20, max=200)
    camera_azimuth: FloatProperty(name='环绕偏移 (°)', default=0, min=-180, max=180)
    camera_elevation: FloatProperty(name='俯仰角度 (°)', default=0, min=-60, max=60)
    camera_roll: FloatProperty(name='画面倾斜 (°)', default=0, min=-45, max=45)
    camera_zoom: FloatProperty(name='构图放大', default=1, min=.5, max=2)
    camera_shift_x: FloatProperty(name='水平移轴', default=0, min=-.4, max=.4)
    camera_shift_y: FloatProperty(name='垂直移轴', default=0, min=-.4, max=.4)
    camera_target_offset: FloatVectorProperty(name='对焦偏移（身高比例）', size=3, default=(0, 0, 0), min=-.5, max=.5)
    camera_aspect: EnumProperty(name='画幅', items=[('SQUARE', '1:1 方形', ''), ('PORTRAIT', '3:4 竖幅', ''), ('LANDSCAPE', '4:3 横幅', '')], default='SQUARE')
    portrait_focus: EnumProperty(name='肖像范围', items=[('HEAD', '头肩（按脸部定位）', ''), ('FACE', '脸部特写', '')], default='HEAD')
    camera_name: StringProperty(name='镜头预设名称', maxlen=80)
    camera_presets_json: StringProperty(default='')
    camera_preset: EnumProperty(name='镜头预设', items=camera_items)
    material_profiles: BoolProperty(name='头发 / 脸部优化', default=True)
    hair_highlight_strength: FloatProperty(name='头发高光层强度', default=.22, min=0, max=1)
    face_fill: FloatProperty(name='脸部柔和补光', default=.92, min=.5, max=1)
    face_shadow_strength: FloatProperty(name='脸部阴影层强度', default=.3, min=0, max=1)
    running: BoolProperty(default=False, options={'SKIP_SAVE'})
    progress: StringProperty(default='等待开始', options={'SKIP_SAVE'})
    ui_section: EnumProperty(name='功能', items=[('MATERIAL', '材质', '', 0), ('CAMERA', '镜头', '', 2), ('RENDER', '输出', '', 3), ('SHOTS', '四视图', '', 5)], default='SHOTS')


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


def dataset_plan(settings):
    return recipe()


def selected_shot(settings):
    return next(s for s in recipe() if s['group'] == settings.preview_group and s['index'] == settings.plan_index)


def update_preview_camera(context, settings, meshes, shot):
    if has_animation(settings.armature, meshes):
        raise ValueError('请先在角色副本中解除动画，避免动画覆盖 T Pose。')
    apply_tpose(settings)
    neutralize_face(settings.armature, meshes)
    context.view_layer.update()
    camera = next((o for o in context.scene.objects if o.type == 'CAMERA' and o.get('aniforge_preview_for') == settings.armature.name), None)
    if not camera:
        camera = bpy.data.objects.new('AniForge Preview Camera', bpy.data.cameras.new('AniForge Preview Camera'))
        context.scene.collection.objects.link(camera)
        camera['aniforge_preview_for'] = settings.armature.name
    context.scene.render.pixel_aspect_x = context.scene.render.pixel_aspect_y = 1
    configure_camera(context, settings, meshes, camera, shot)
    context.scene.camera = camera
    if context.area and context.area.type == 'VIEW_3D':
        context.space_data.region_3d.view_perspective = 'CAMERA'
    return camera


class ML_OT_tpose(bpy.types.Operator):
    bl_idname = 'ml.tpose'; bl_label = '应用唯一 T Pose / 清除面部表情'; bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            settings = context.scene.ml_settings
            meshes = model_meshes(settings)
            if has_animation(settings.armature, meshes):
                raise ValueError('请先在角色副本中解除动画，避免动画覆盖 T Pose。')
            apply_tpose(settings)
            neutralize_face(settings.armature, meshes)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_camera_preset(bpy.types.Operator):
    bl_idname = 'ml.camera_preset'; bl_label = '镜头预设'; bl_options = {'REGISTER', 'UNDO'}
    save: BoolProperty(default=False)

    def execute(self, context):
        try:
            settings = context.scene.ml_settings; library = camera_library(settings)
            if self.save:
                name = settings.camera_name.strip() or settings.camera_preset
                if not name or name == 'AUTO':
                    raise ValueError('请填写镜头预设名称。')
                library['cameras'][name] = capture_camera(settings)
                settings.camera_presets_json = json.dumps(validate_camera_library(library), ensure_ascii=False)
                settings.camera_preset = name
            elif settings.camera_preset != 'AUTO':
                camera_apply(settings, library['cameras'][settings.camera_preset])
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_export_cameras(bpy.types.Operator, ExportHelper):
    bl_idname = 'ml.export_cameras'; bl_label = '导出镜头 JSON'
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            write_json(self.filepath, camera_library(context.scene.ml_settings))
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_import_cameras(bpy.types.Operator, ImportHelper):
    bl_idname = 'ml.import_cameras'; bl_label = '导入镜头 JSON'; bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            path = Path(self.filepath)
            if path.stat().st_size > 8*1024*1024:
                raise ValueError('镜头文件不能超过 8 MiB。')
            incoming = validate_camera_library(json.loads(path.read_text(encoding='utf-8-sig')))
            settings = context.scene.ml_settings; library = camera_library(settings)
            library['cameras'].update(incoming['cameras'])
            settings.camera_presets_json = json.dumps(library, ensure_ascii=False)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_shot_plan(bpy.types.Operator):
    bl_idname = 'ml.shot_plan'; bl_label = '预览选中视图（不出图）'; bl_options = {'REGISTER', 'UNDO'}
    report_only: BoolProperty(default=False)

    def execute(self, context):
        try:
            settings = context.scene.ml_settings
            if self.report_only:
                text = bpy.data.texts.get('AniForge 16 View Plan') or bpy.data.texts.new('AniForge 16 View Plan')
                text.clear(); text.write(json.dumps({'shots': recipe(), 'coverage': dataset_coverage(recipe())}, ensure_ascii=False, indent=2))
            else:
                update_preview_camera(context, settings, model_meshes(settings), selected_shot(settings))
            self.report({'INFO'}, '已更新，没有渲染图片。')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_frame_camera(bpy.types.Operator):
    bl_idname = 'ml.frame_camera'; bl_label = '更新选中视图相机（不出图）'; bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return bpy.ops.ml.shot_plan()


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
        if has_animation(self.settings.armature, self.meshes):
            raise ValueError('请先在角色副本中解除动画，避免动画覆盖 T Pose。')
        self.created = []; self.manifest = None; self.folder = None
        self.closed = False
        self.character_state = CharacterState(self.settings.armature, self.meshes)
        r = self.scene.render
        self.saved = (self.scene.camera, r.engine, r.filepath, r.resolution_x, r.resolution_y,
                      r.resolution_percentage, r.image_settings.file_format, r.image_settings.color_mode,
                      r.film_transparent, self.scene.world, self.scene.view_settings.view_transform,
                      r.use_file_extension, r.use_border, r.use_compositing, r.use_sequencer, r.pixel_aspect_x, r.pixel_aspect_y)
        self.visibility = [(o, o.hide_render) for o in self.scene.objects if o.type in {'MESH', 'LIGHT'} and o not in self.meshes]
        self.rigidbody_world = self.scene.rigidbody_world
        self.rigidbody_enabled = self.rigidbody_world.enabled if self.rigidbody_world else None
        self.simulation_modifiers = [(m, m.show_viewport, m.show_render) for obj in self.meshes for m in obj.modifiers
                                     if m.type in {'CLOTH', 'SOFT_BODY'}]

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
                         'deformation_mode': 'ARMATURE_STATIC',
                         'camera_settings': capture_camera(self.settings),
                         'pose': 'tpose', 'expression': 'neutral'}
        self.flush()
        if self.rigidbody_world:
            self.rigidbody_world.enabled = False
        for modifier, _, _ in self.simulation_modifiers:
            modifier.show_viewport = modifier.show_render = False
        apply_tpose(self.settings)
        neutralize_face(self.settings.armature, self.meshes)
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
        self.world.node_tree.nodes['Background'].inputs[0].default_value = (1.0, 1.0, 1.0, 1)
        self.world.node_tree.nodes['Background'].inputs[1].default_value = 1.0
        self.scene.world = self.world
        r = self.scene.render; r.engine = eevee_engine(self.scene)
        r.resolution_x, r.resolution_y = resolution(self.settings); r.resolution_percentage = 100
        r.pixel_aspect_x = r.pixel_aspect_y = 1
        r.image_settings.file_format = 'PNG'; r.image_settings.color_mode = 'RGB'; r.film_transparent = False
        r.use_file_extension = True; r.use_border = False; r.use_compositing = False; r.use_sequencer = False
        self.scene.view_settings.view_transform = 'Standard'

    def flush(self):
        write_json(self.folder / 'manifest.json', self.manifest)

    def reset_character(self, static=True):
        self.character_state.restore()

    def prepare_shot(self, context, shot):
        # Ignore any removed pose/expression fields supplied by old scripts.
        apply_tpose(self.settings)
        cleared = neutralize_face(self.settings.armature, self.meshes)
        context.view_layer.update()
        self.camera_meta = configure_camera(context, self.settings, self.meshes, self.camera, shot)
        folder = self.folder / shot['group']; folder.mkdir(exist_ok=True)
        name = f'{safe_name(self.settings.trigger)}_{shot["group"]}_{shot["index"]:02d}'
        image = folder / (name + '.png'); self.scene.render.filepath = str(image)
        return image, cleared, [b.name for b in self.settings.armature.pose.bones]

    def render(self, context, shot):
        image, expression_keys, pose_bones = self.prepare_shot(context, shot)
        group = shot['group']
        bpy.ops.render.render(write_still=True)
        if not image.is_file():
            raise RuntimeError('渲染未生成目标 PNG。')
        tags = caption(self.settings.trigger, group, shot.get('view', 'front'), self.settings.tags)
        image.with_suffix('.txt').write_text(tags + '\n', encoding='utf-8')
        self.manifest['shots'].append(dict(shot, pose='tpose', expression='neutral', image=str(image.relative_to(self.folder)), caption=tags,
                                           cleared_face_keys=expression_keys, camera=self.camera_meta, deformation_mode='ARMATURE_STATIC'))
        self.flush()

    def close(self, status, error=''):
        if self.closed:
            return
        self.closed = True
        try:
            self.reset_character(static=False)
            if self.rigidbody_world:
                self.rigidbody_world.enabled = self.rigidbody_enabled
            for modifier, viewport, render in self.simulation_modifiers:
                modifier.show_viewport = viewport; modifier.show_render = render
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
        finally:
            self.settings.running = False
            if self.manifest is not None:
                self.manifest['status'] = status; self.manifest['error'] = error; self.flush()


class ML_OT_render(bpy.types.Operator):
    bl_idname = 'ml.render_dataset'; bl_label = '渲染 T Pose 视图'
    test_only: BoolProperty(name='仅三视图测试', default=True)
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
        self._session = None
        settings = context.scene.ml_settings
        self.shots = three_view_recipe(settings.preview_group) if self.test_only else dataset_plan(settings)
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
    bl_idname = 'ml.preview'; bl_label = '渲染选中视图 1 张'

    @classmethod
    def poll(cls, context):
        return not context.scene.ml_settings.running

    def execute(self, context):
        session = None
        try:
            settings = context.scene.ml_settings
            shot = selected_shot(settings)
            session = RenderSession(context, [shot]); session.setup(context)
            session.render(context, shot)
            session.close('preview')
            self.report({'INFO'}, '预览已写入：' + str(session.folder))
            return {'FINISHED'}
        except Exception as exc:
            if session:
                session.close('failed', str(exc))
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_PT_panel(bpy.types.Panel):
    bl_label = 'AniForge · T Pose 四视图渲染'; bl_idname = 'ML_PT_panel'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'MMD LoRA'

    def draw(self, context):
        settings = context.scene.ml_settings
        layout = self.layout; col = layout.column(); col.enabled = not settings.running
        col.prop(settings, 'armature'); col.prop(settings, 'ui_section', expand=True)
        section = settings.ui_section
        if section == 'MATERIAL':
            col.prop(settings, 'style')
            col.label(text='先用 MMD Tools 导入 PMX / PMD', icon='INFO')
            col.prop(settings, 'textures'); col.operator('ml.textures'); col.operator('ml.assign_textures')
            col.prop(settings, 'material_profiles')
            if settings.material_profiles:
                for name in ('hair_highlight_strength', 'face_fill', 'face_shadow_strength'):
                    col.prop(settings, name)
            col.operator('ml.materials')
        elif section == 'CAMERA':
            col.prop(settings, 'camera_name'); col.prop(settings, 'camera_preset')
            row = col.row(); row.operator('ml.camera_preset', text='应用镜头'); row.operator('ml.camera_preset', text='保存镜头').save = True
            row = col.row(); row.operator('ml.import_cameras'); row.operator('ml.export_cameras')
            col.prop(settings, 'preview_group'); col.prop(settings, 'plan_index')
            if settings.preview_group == 'portrait':
                col.prop(settings, 'portrait_focus')
            col.prop(settings, 'camera_projection')
            if settings.camera_projection == 'PERSP':
                col.prop(settings, 'camera_lens')
            for name in ('camera_aspect', 'front', 'margin', 'camera_azimuth', 'camera_elevation', 'camera_roll', 'camera_zoom', 'camera_shift_x', 'camera_shift_y', 'camera_target_offset'):
                col.prop(settings, name)
            col.operator('ml.frame_camera')
        else:
            col.label(text='唯一 T Pose · 自然面部', icon='ARMATURE_DATA')
            col.label(text='肖像 4 · 半身 4 · 全身 4 · 下半身 4')
            col.label(text='每组：正面 / 左侧 / 右侧 / 背面')
            col.operator('ml.tpose')
            col.prop(settings, 'preview_group'); col.prop(settings, 'plan_index')
            col.label(text=selected_shot(settings)['title'])
            col.operator('ml.shot_plan')
            col.operator('ml.preview', text='渲染选中视图 1 张')
            if section == 'RENDER':
                for name in ('trigger', 'tags', 'resolution', 'output'):
                    col.prop(settings, name)
                col.operator('ml.render_dataset', text='生成 16 张四视图 + 标签', icon='RENDER_STILL').test_only = False
                col.operator('ml.render_dataset', text='开发测试：正 / 侧 / 背 3 张').test_only = True
            else:
                col.operator('ml.shot_plan', text='查看 16 张清单').report_only = True
        layout.label(text=settings.progress)
        if settings.running:
            layout.label(text='Esc 在单张渲染结束后停止')


CLASSES = (MLSettings, ML_OT_materials, ML_OT_textures, ML_OT_assign_textures,
           ML_OT_camera_preset, ML_OT_import_cameras, ML_OT_export_cameras,
           ML_OT_tpose, ML_OT_frame_camera, ML_OT_shot_plan, ML_OT_render, ML_OT_preview, ML_PT_panel)

# Removed RNA/ID properties must not reactivate old workflows in saved projects.
REMOVED_SETTINGS = ('presets_json', 'pose', 'expression', 'pose_strength', 'expression_strength', 'head_angles',
                    'hand_pose', 'hip_hand', 'hip_clearance', 'hip_offset', 'free_hand_offset', 'preset_name',
                    'capture_all_keys', 'dataset_variation', 'pose_folder', 'pose_import_scale',
                    'pose_folder_report', 'slot_left_hand', 'slot_right_hand', 'slot_seat', 'developer_tools',
                    'mapping_report', 'physics_mode', 'physics_frames', 'animchar_reference', 'animchar_prompt',
                    'animchar_rebuild', 'animchar_review_note', 'animchar_status')


@bpy.app.handlers.persistent
def migrate_settings(_=None):
    for scene in getattr(bpy.data, 'scenes', ()):
        settings = scene.ml_settings
        if settings.get('presets_json') and not settings.camera_presets_json:
            try:
                settings.camera_presets_json = json.dumps(validate_camera_library(json.loads(settings['presets_json'])), ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        for name in REMOVED_SETTINGS:
            if name in settings:
                del settings[name]
        if settings.get('ui_section') in {1, 4}:
            settings.ui_section = 'SHOTS'
        clamp_plan_index(settings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ml_settings = PointerProperty(type=MLSettings)
    if migrate_settings not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(migrate_settings)
    migrate_settings()


def unregister():
    if any(scene.ml_settings.running for scene in getattr(bpy.data, 'scenes', ())):
        raise RuntimeError('请先结束批量渲染再禁用插件。')
    if migrate_settings in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate_settings)
    del bpy.types.Scene.ml_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
