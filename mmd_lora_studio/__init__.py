bl_info = {
    'name': 'AniForge - MMD LoRA Studio', 'author': 'AniForge', 'version': (0, 11, 0),
    'blender': (4, 2, 0), 'location': 'View3D > Sidebar > MMD LoRA',
    'description': 'MMD toon materials and 16 T-pose views',
    'category': 'Render',
}

import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import bpy
from bpy.props import StringProperty, EnumProperty, PointerProperty, IntProperty, FloatProperty, BoolProperty, FloatVectorProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Vector

from .core import STYLES, GROUPS, GROUP_LABELS, BONES, recipe, three_view_recipe, dataset_coverage, caption, safe_name, write_json, validate_camera_library
from .material_profiles import material_role, alpha_image, face_light, hair_color
from .static_pose import find_bone, shape_blocks, apply_tpose, neutralize_face, CharacterState, has_animation
from .camera import configure_camera, capture_camera, camera_apply, resolution, ensure_camera, remember_camera, restore_camera, white_world, white_view
from .output import save_white_crop, show_output
from . import ui, captioning


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
    soft_hair = style == 'NEVERNESS' and role == 'HAIR'
    soft_skin = style == 'NEVERNESS' and role == 'SKIN'
    if role == 'FACE' or soft_hair or soft_skin:
        # Mingyin's bright hair/neck expose the generic binary light threshold
        # as broad disconnected brown bands. Keep the painted albedo and use
        # smooth normal lighting consistently with the already softened face.
        tree.links.new(face_light(tree, settings, minimum=.82 if soft_hair else None,
                                  role='Hair' if soft_hair else 'Skin' if soft_skin else 'Face'), multiply.inputs[2])
        mat['mmd_lora_soft_light'] = True
        tree.nodes.remove(rgb)
        tree.nodes.remove(diffuse)
        tree.nodes.remove(ramp)
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


_PENDING_PREVIEWS = {}


def schedule_preview(settings, _context=None):
    if settings.armature is None:
        return
    scene = settings.id_data
    _PENDING_PREVIEWS[scene.as_pointer()] = scene
    if not bpy.app.timers.is_registered(refresh_previews):
        bpy.app.timers.register(refresh_previews, first_interval=.15)


def resolution_changed(settings, context):
    settings['resolution_default_4k'] = True
    schedule_preview(settings, context)


def caption_mode_changed(settings, _context):
    if settings.api_caption:
        settings.ui_tags = True


def refresh_previews():
    for key, scene in list(_PENDING_PREVIEWS.items()):
        try:
            settings = scene.ml_settings
            if settings.running:
                continue
            _PENDING_PREVIEWS.pop(key, None)
            if not settings.armature:
                continue
            with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
                update_preview_camera(bpy.context, settings, model_meshes(settings), selected_shot(settings))
            settings.camera_status = '自动相机已就绪 · 白底'
        except (ReferenceError, AttributeError):
            _PENDING_PREVIEWS.pop(key, None)
        except Exception as exc:
            settings.camera_status = str(exc)
    return .25 if _PENDING_PREVIEWS else None


class MLSettings(bpy.types.PropertyGroup):
    armature: PointerProperty(name='角色骨架', type=bpy.types.Object, poll=lambda self, obj: obj.type == 'ARMATURE', update=schedule_preview)
    style: EnumProperty(name='游戏风格', items=[(k, v[0], '风格近似预设，非官方着色器') for k, v in STYLES.items()])
    output: StringProperty(name='输出目录', subtype='DIR_PATH', default='//lora_dataset')
    textures: StringProperty(name='贴图目录', subtype='DIR_PATH')
    trigger: StringProperty(name='角色触发词', default='my_character')
    tags: StringProperty(name='附加标签', default='anime style, simple background')
    api_caption: BoolProperty(name='API 自动打标', default=False, update=caption_mode_changed)
    caption_running: BoolProperty(default=False, options={'SKIP_SAVE'})
    caption_status: StringProperty(default='', options={'SKIP_SAVE'})
    caption_error: StringProperty(default='', options={'SKIP_SAVE'})
    caption_folder: StringProperty(name='已有图片目录', subtype='DIR_PATH', default='')
    last_render_folder: StringProperty(default='', subtype='DIR_PATH')
    resolution: IntProperty(name='分辨率', default=4096, min=256, max=4096, update=resolution_changed)
    preview_group: EnumProperty(name='取景范围', items=[(v, label, '') for v, label in GROUP_LABELS.items()], update=schedule_preview)
    plan_index: IntProperty(name='视角', default=1, min=1, max=4, update=schedule_preview)
    preview_view: EnumProperty(name='观察方向', items=[('FRONT', '正面', '0°', 1), ('LEFT', '左侧', '90°', 2),
                                                     ('RIGHT', '右侧', '-90°', 3), ('BACK', '背面', '180°', 4)],
                               get=lambda self: min(4, max(1, self.plan_index)),
                               set=lambda self, value: setattr(self, 'plan_index', value), options={'SKIP_SAVE'})
    front: FloatProperty(name='角色正面角度', default=0, min=-180, max=180, description='0 表示朝向世界 -Y；90 表示朝向 +X', update=schedule_preview)
    margin: FloatProperty(name='构图留白', default=1.18, min=1.02, max=2, update=schedule_preview)
    camera_projection: EnumProperty(name='投影', items=[('ORTHO', '正交', ''), ('PERSP', '透视', '')], default='ORTHO', update=schedule_preview)
    camera_lens: FloatProperty(name='焦距 (mm)', default=85, min=20, max=200, update=schedule_preview)
    camera_azimuth: FloatProperty(name='环绕偏移 (°)', default=0, min=-180, max=180, update=schedule_preview)
    camera_elevation: FloatProperty(name='俯仰角度 (°)', default=0, min=-60, max=60, update=schedule_preview)
    camera_roll: FloatProperty(name='画面倾斜 (°)', default=0, min=-45, max=45, update=schedule_preview)
    camera_zoom: FloatProperty(name='构图放大', default=1, min=.5, max=2, update=schedule_preview)
    camera_shift_x: FloatProperty(name='水平移轴', default=0, min=-.4, max=.4, update=schedule_preview)
    camera_shift_y: FloatProperty(name='垂直移轴', default=0, min=-.4, max=.4, update=schedule_preview)
    camera_target_offset: FloatVectorProperty(name='对焦偏移（身高比例）', size=3, default=(0, 0, 0), min=-.5, max=.5, update=schedule_preview)
    camera_aspect: EnumProperty(name='画幅', items=[('SQUARE', '1:1 方形', ''), ('PORTRAIT', '3:4 竖幅', ''), ('LANDSCAPE', '4:3 横幅', '')], default='SQUARE', update=schedule_preview)
    portrait_focus: EnumProperty(name='肖像范围', items=[('HEAD', '头肩（按脸部定位）', ''), ('FACE', '脸部特写', '')], default='HEAD', update=schedule_preview)
    camera_name: StringProperty(name='镜头预设名称', maxlen=80)
    camera_status: StringProperty(default='', options={'SKIP_SAVE'})
    camera_presets_json: StringProperty(default='')
    camera_preset: EnumProperty(name='镜头预设', items=camera_items)
    material_profiles: BoolProperty(name='头发 / 脸部优化', default=True)
    hair_highlight_strength: FloatProperty(name='头发高光层强度', default=.22, min=0, max=1)
    face_fill: FloatProperty(name='脸部柔和补光', default=.92, min=.5, max=1)
    face_shadow_strength: FloatProperty(name='脸部阴影层强度', default=.3, min=0, max=1)
    running: BoolProperty(default=False, options={'SKIP_SAVE'})
    progress: StringProperty(default='等待开始', options={'SKIP_SAVE'})
    auto_crop: BoolProperty(name='自动裁掉白边', default=True)
    crop_padding: FloatProperty(name='输出留白 (%)', default=3, min=0, max=20,
                               description='根据角色透明轮廓裁剪，边缘保留少量白色空间')
    ui_textures: BoolProperty(name='贴图修复', default=False)
    ui_material: BoolProperty(name='材质微调', default=False)
    ui_camera: BoolProperty(name='高级镜头', default=False)
    ui_presets: BoolProperty(name='镜头预设', default=False)
    ui_tags: BoolProperty(name='附加训练标签', default=False)
    ui_developer: BoolProperty(name='开发检查', default=False)



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
    camera = ensure_camera(context.scene)
    white_world(context.scene)
    white_view(context.scene)
    context.scene.render.pixel_aspect_x = context.scene.render.pixel_aspect_y = 1
    configure_camera(context, settings, meshes, camera, shot)
    context.scene.camera = camera
    if context.area and context.area.type == 'VIEW_3D':
        context.space_data.region_3d.view_perspective = 'CAMERA'
        context.space_data.shading.background_type = 'WORLD'
        context.space_data.shading.use_scene_world = True
    settings.camera_status = '自动相机已就绪 · 白底'
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
        if self.settings.running:
            raise ValueError('已有渲染任务正在进行。')
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
        self.color_depth = r.image_settings.color_depth
        view = self.scene.view_settings
        self.color_view = {key: getattr(view, key) for key in ('view_transform', 'look', 'exposure', 'gamma', 'use_curve_mapping')}
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
        self.settings.last_render_folder = str(self.folder)
        self.settings.caption_status = '等待渲染完成后 API 打标' if self.settings.api_caption else ''
        self.manifest = {'status': 'running', 'style': self.settings.style, 'blender': bpy.app.version_string,
                         'dataset_status': 'RENDERING',
                         'warning': 'Game styles are approximations. Review poses, crops and captions before training.',
                         'shots': [], 'recipe': self.plan, 'planned_coverage': dataset_coverage(self.plan),
                         'deformation_mode': 'ARMATURE_STATIC',
                         'camera_settings': capture_camera(self.settings),
                         'pose': 'tpose', 'expression': 'neutral'}
        self.manifest['output_processing'] = {'background': 'white', 'auto_crop': self.settings.auto_crop,
                                              'padding_percent': self.settings.crop_padding}
        self.flush()
        self.settings.running = True
        if self.rigidbody_world:
            self.rigidbody_world.enabled = False
        for modifier, _, _ in self.simulation_modifiers:
            modifier.show_viewport = modifier.show_render = False
        apply_tpose(self.settings)
        neutralize_face(self.settings.armature, self.meshes)
        for obj, _ in self.visibility:
            obj.hide_render = True
        self.camera_preexisting = any(o.type == 'CAMERA' and (o.get('aniforge_camera') or o.get('aniforge_preview_for')) for o in self.scene.objects)
        self.camera = ensure_camera(self.scene)
        self.camera_state = remember_camera(self.camera) if self.camera_preexisting else None
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
        r.image_settings.color_depth = '8'
        r.use_file_extension = True; r.use_border = False; r.use_compositing = False; r.use_sequencer = False
        white_view(self.scene)

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
        r = self.scene.render
        with TemporaryDirectory(prefix='.aniforge_', dir=self.folder) as temporary:
            raw = Path(temporary) / 'character_rgba.png'
            r.filepath = str(raw)
            r.image_settings.color_mode = 'RGBA'; r.image_settings.color_depth = '16'; r.film_transparent = True
            try:
                bpy.ops.render.render(write_still=True)
                r.image_settings.color_mode = 'RGB'; r.image_settings.color_depth = '8'; r.film_transparent = False
                self.output_meta = save_white_crop(self.scene, raw, image, self.settings.crop_padding, self.settings.auto_crop)
            finally:
                r.filepath = str(image)
                r.image_settings.color_mode = 'RGB'; r.image_settings.color_depth = '8'; r.film_transparent = False
        if not image.is_file():
            raise RuntimeError('渲染未生成目标 PNG。')
        tags = '' if self.settings.api_caption else caption(self.settings.trigger, group, shot.get('view', 'front'), self.settings.tags)
        if not self.settings.api_caption:
            image.with_suffix('.txt').write_text(tags + '\n', encoding='utf-8')
        self.manifest['shots'].append(dict(shot, pose='tpose', expression='neutral', image=str(image.relative_to(self.folder)), caption=tags,
                                           caption_status='API_PENDING' if self.settings.api_caption else 'BASIC',
                                           cleared_face_keys=expression_keys, camera=self.camera_meta, output=self.output_meta, deformation_mode='ARMATURE_STATIC'))
        self.flush()
        try:
            show_output(context, image)
        except (RuntimeError, ReferenceError):
            pass

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
            r.image_settings.color_depth = self.color_depth
            for key, value in self.color_view.items():
                setattr(self.scene.view_settings, key, value)
            if getattr(self, 'camera_state', None) is not None:
                restore_camera(self.camera, self.camera_state)
            if hasattr(self, 'camera') and self.scene.camera is None:
                self.scene.camera = self.camera
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
                self.manifest['dataset_status'] = ('CAPTION_PENDING' if self.settings.api_caption else 'READY') if status in {'complete', 'preview', 'three_views'} else 'INCOMPLETE'
                self.manifest['status'] = status; self.manifest['error'] = error; self.flush()


class ML_OT_render(bpy.types.Operator):
    bl_idname = 'ml.render_dataset'; bl_label = '渲染 T Pose 视图'
    test_only: BoolProperty(name='仅三视图测试', default=True)
    _timer = None
    _session = None
    _in_step = False
    _finished = False
    _next_step = 0.0

    @classmethod
    def poll(cls, context):
        return not context.scene.ml_settings.running and not captioning.busy()

    def finish(self, context, status, error=''):
        if self._finished:
            return True
        self._finished = True
        timer, self._timer = self._timer, None
        session, self._session = self._session, None
        cleanup_error = ''
        try:
            if timer is not None:
                try:
                    context.window_manager.event_timer_remove(timer)
                except (ReferenceError, RuntimeError, ValueError):
                    pass  # The window may already have removed its timer.
            if session is not None:
                session.close(status, error)
        except Exception as exc:
            cleanup_error = str(exc)
            status = 'failed'
        finally:
            context.scene.ml_settings.running = False
            context.scene.ml_settings.progress = f'{status}: {self.index}/{len(self.shots)}'
            context.window_manager.progress_end()
        if cleanup_error:
            self.report({'ERROR'}, '渲染状态清理失败：'+cleanup_error)
        elif status == 'complete' and context.scene.ml_settings.api_caption:
            try:
                captioning.start(context, session.folder)
            except Exception as exc:
                context.scene.ml_settings.caption_status = '图片已保存，API 打标未完成：'+str(exc)
                self.report({'WARNING'}, context.scene.ml_settings.caption_status)
        return not cleanup_error

    def execute(self, context):
        self.index = 0
        self._timer = None
        self._session = None
        self._finished = self._in_step = False
        self._next_step = 0.0
        settings = context.scene.ml_settings
        self.shots = three_view_recipe(settings.preview_group) if self.test_only else dataset_plan(settings)
        try:
            captioning.preflight(context)
            self._session = RenderSession(context, self.shots); self._session.setup(context)
            context.scene.ml_settings.running = True
            context.window_manager.progress_begin(0, len(self.shots))
            if bpy.app.background:
                for shot in self.shots:
                    self._session.render(context, shot); self.index += 1
                return {'FINISHED'} if self.finish(context, 'complete') else {'CANCELLED'}
            self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
            self._next_step = monotonic()+.25
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.finish(context, 'failed', str(exc)); self.report({'ERROR'}, str(exc)); return {'CANCELLED'}

    def modal(self, context, event):
        if self._finished:
            return {'CANCELLED'}
        if event.type == 'ESC':
            self.finish(context, 'cancelled'); return {'CANCELLED'}
        # Blender Event has no timer property. Gate timer notifications by our
        # own cadence so other add-ons' timers cannot accelerate this batch.
        if event.type == 'TIMER' and self._timer is not None and not self._in_step:
            if monotonic() < self._next_step:
                return {'RUNNING_MODAL'}
            self._in_step = True
            try:
                shot = self.shots[self.index]
                context.scene.ml_settings.progress = f'{self.index + 1}/{len(self.shots)} {shot["group"]}'
                self._session.render(context, shot); self.index += 1
                context.window_manager.progress_update(self.index)
                if self.index == len(self.shots):
                    folder = str(self._session.folder)
                    if not self.finish(context, 'complete'):
                        return {'CANCELLED'}
                    self.report({'INFO'}, '完成：' + folder)
                    return {'FINISHED'}
                if context.area:
                    context.area.tag_redraw()
            except Exception as exc:
                self.finish(context, 'failed', str(exc)); self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
            finally:
                self._in_step = False
                self._next_step = monotonic()+.25
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self.finish(context, 'cancelled')


class ML_OT_preview(bpy.types.Operator):
    bl_idname = 'ml.preview'; bl_label = '渲染选中视图 1 张'

    @classmethod
    def poll(cls, context):
        return not context.scene.ml_settings.running and not captioning.busy()

    def execute(self, context):
        session = None
        try:
            settings = context.scene.ml_settings
            captioning.preflight(context)
            shot = selected_shot(settings)
            session = RenderSession(context, [shot]); session.setup(context)
            session.render(context, shot)
            session.close('preview')
            if settings.api_caption:
                try:
                    captioning.start(context, session.folder)
                except Exception as exc:
                    settings.caption_status = '图片已保存，API 打标未完成：'+str(exc)
                    self.report({'WARNING'}, settings.caption_status)
            self.report({'INFO'}, '预览已写入：' + str(session.folder))
            return {'FINISHED'}
        except Exception as exc:
            if session:
                session.close('failed', str(exc))
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


CLASSES = (MLSettings, ML_OT_materials, ML_OT_textures, ML_OT_assign_textures,
           ML_OT_camera_preset, ML_OT_import_cameras, ML_OT_export_cameras,
           ML_OT_tpose, ML_OT_frame_camera, ML_OT_shot_plan, ML_OT_render, ML_OT_preview, *captioning.CLASSES, *ui.CLASSES)

# Removed RNA/ID properties must not reactivate old workflows in saved projects.
REMOVED_SETTINGS = ('presets_json', 'pose', 'expression', 'pose_strength', 'expression_strength', 'head_angles',
                    'hand_pose', 'hip_hand', 'hip_clearance', 'hip_offset', 'free_hand_offset', 'preset_name',
                    'capture_all_keys', 'dataset_variation', 'pose_folder', 'pose_import_scale',
                    'pose_folder_report', 'slot_left_hand', 'slot_right_hand', 'slot_seat', 'developer_tools',
                    'mapping_report', 'physics_mode', 'physics_frames', 'animchar_reference', 'animchar_prompt',
                    'animchar_rebuild', 'animchar_review_note', 'animchar_status', 'ui_section')


@bpy.app.handlers.persistent
def migrate_settings(_=None):
    _PENDING_PREVIEWS.clear()
    for scene in getattr(bpy.data, 'scenes', ()):
        settings = scene.ml_settings
        if not settings.get('resolution_default_4k'):
            settings['resolution_default_4k'] = True
            settings.resolution = 4096
        if settings.get('presets_json') and not settings.camera_presets_json:
            try:
                settings.camera_presets_json = json.dumps(validate_camera_library(json.loads(settings['presets_json'])), ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        for name in REMOVED_SETTINGS:
            if name in settings:
                del settings[name]
        clamp_plan_index(settings)
        schedule_preview(settings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ml_settings = PointerProperty(type=MLSettings)
    if migrate_settings not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(migrate_settings)
    if captioning.on_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(captioning.on_load)
    migrate_settings()


def unregister():
    if any(scene.ml_settings.running for scene in getattr(bpy.data, 'scenes', ())):
        raise RuntimeError('请先结束批量渲染再禁用插件。')
    captioning.shutdown()
    if captioning.on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(captioning.on_load)
    if migrate_settings in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate_settings)
    if bpy.app.timers.is_registered(refresh_previews):
        bpy.app.timers.unregister(refresh_previews)
    _PENDING_PREVIEWS.clear()
    del bpy.types.Scene.ml_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
