bl_info = {
    'name': 'AniForge - MMD LoRA Studio', 'author': 'AniForge', 'version': (0, 4, 0),
    'blender': (4, 2, 0), 'location': 'View3D > Sidebar > MMD LoRA',
    'description': 'MMD toon materials and 36-shot LoRA dataset rendering',
    'category': 'Render',
}

import json
import math
from datetime import datetime
from pathlib import Path

import bpy
from bpy.props import StringProperty, EnumProperty, PointerProperty, IntProperty, FloatProperty, BoolProperty
from mathutils import Vector, Matrix

from .core import STYLES, GROUPS, MORPHS, BONES, POSE_ROTATIONS, POSE_DIRECTIONS, recipe, three_view_recipe, caption, safe_name, write_json
from .material_profiles import material_role, alpha_image, face_light, hair_color


def model_meshes(settings):
    rig = settings.armature
    if not rig:
        raise ValueError('请先指定角色骨架。')
    result = []
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
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


def aliases(item):
    names = [item.name]
    for attr in ('mmd_bone',):
        mmd = getattr(item, attr, None)
        if mmd:
            names.extend([getattr(mmd, 'name_j', ''), getattr(mmd, 'name_e', '')])
    return names


def find_bone(rig, key):
    return next((b for b in rig.pose.bones if set(aliases(b)) & set(BONES[key])), None)


def shape_blocks(meshes):
    return [(obj, obj.data.shape_keys.key_blocks) for obj in meshes if obj.data.shape_keys]


def custom_presets(settings):
    if not settings.presets_json:
        return {'expressions': {}, 'poses': {}}
    return json.loads(settings.presets_json)


def expression_apply(settings, meshes, name):
    blocks = shape_blocks(meshes)
    saved = custom_presets(settings)['expressions']
    controlled = {alias for groups in MORPHS.values() for group in groups for alias in group}
    for obj, keys in blocks:
        custom_keys = {key for preset in saved.values() for key in preset.get(obj.name, {})}
        for key in keys:
            if key != keys[0] and key.name in controlled | custom_keys:
                key.value = 0
    custom = saved.get(name)
    changed = []
    if custom is not None:
        for obj, keys in blocks:
            for key_name, value in custom.get(obj.name, {}).items():
                if key_name in keys:
                    keys[key_name].value = value
                    changed.append(obj.name + ':' + key_name)
    else:
        for candidates in MORPHS.get(name, []):
            for obj, keys in blocks:
                key = next((keys.get(n) for n in candidates if keys.get(n) is not None), None)
                if key:
                    key.value = 0.35 if key.name in ('あ', 'a', 'A', 'お', 'o', 'O') else 1
                    changed.append(obj.name + ':' + key.name)
    return changed


def pose_apply(settings, name):
    rig = settings.armature
    custom = custom_presets(settings)['poses'].get(name)
    changed = []
    if custom is not None:
        for bone_name, values in custom.items():
            bone = rig.pose.bones.get(bone_name)
            if bone:
                bone.matrix_basis = Matrix(values)
                changed.append(bone_name)
    else:
        # A previous preset must not leave an elbow offset in the next preset.
        managed = {key for values in POSE_ROTATIONS.values() for key in values}
        for key in managed:
            bone = find_bone(rig, key)
            if bone:
                bone.rotation_mode = 'XYZ'
                bone.rotation_euler = (0, 0, 0)
        bpy.context.view_layer.update()
        front = math.radians(settings.front)
        side = Vector((math.cos(front), math.sin(front), 0))
        forward = Vector((math.sin(front), -math.cos(front), 0))
        to_rig = rig.matrix_world.to_3x3().inverted()
        for key, direction in POSE_DIRECTIONS.get(name, {}).items():
            bone = find_bone(rig, key)
            if bone:
                x, y, z = direction
                target = (to_rig @ (side * x + forward * y + Vector((0, 0, z)))).normalized()
                current = bone.matrix.to_quaternion() @ Vector((0, 1, 0))
                swing = current.rotation_difference(target)
                matrix = bone.matrix.copy()
                bone.matrix = Matrix.LocRotScale(matrix.translation, swing @ matrix.to_quaternion(), matrix.to_scale())
                bpy.context.view_layer.update()
                changed.append(bone.name)
    return changed


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
    expression: EnumProperty(name='表情', items=[(n, n, '') for n in dict.fromkeys(['neutral'] + list(MORPHS))])
    pose: EnumProperty(name='姿势', items=[(n, n, '') for n in POSE_ROTATIONS])
    material_profiles: BoolProperty(name='头发 / 脸部优化', default=True)
    hair_highlight_strength: FloatProperty(name='头发高光层强度', default=.22, min=0, max=1)
    face_fill: FloatProperty(name='脸部柔和补光', default=.92, min=.5, max=1)
    face_shadow_strength: FloatProperty(name='脸部阴影层强度', default=.3, min=0, max=1)
    presets_json: StringProperty(default='')
    running: BoolProperty(default=False, options={'SKIP_SAVE'})
    progress: StringProperty(default='等待开始', options={'SKIP_SAVE'})


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
    action: EnumProperty(items=[(v, v, '') for v in ('EXPRESSION', 'POSE', 'SAVE_EXPRESSION', 'SAVE_POSE')])

    def execute(self, context):
        settings = context.scene.ml_settings
        try:
            meshes = model_meshes(settings)
            saved = custom_presets(settings)
            if self.action == 'EXPRESSION':
                changed = expression_apply(settings, meshes, settings.expression)
                self.report({'INFO'}, f'表情映射 {len(changed)} 个形态键；无映射时为中性。')
            elif self.action == 'POSE':
                changed = pose_apply(settings, settings.pose)
                self.report({'INFO'}, f'姿势映射 {len(changed)} 根骨骼；请检查 IK 和骨骼轴向。')
            elif self.action == 'SAVE_EXPRESSION':
                saved['expressions'][settings.expression] = {obj.name: {key.name: key.value for key in keys[1:]}
                                                            for obj, keys in shape_blocks(meshes)}
            else:
                saved['poses'][settings.pose] = {bone.name: [list(row) for row in bone.matrix_basis]
                                                 for bone in settings.armature.pose.bones}
            if self.action.startswith('SAVE'):
                settings.presets_json = json.dumps(saved, ensure_ascii=False)
                self.report({'INFO'}, '当前状态已保存为该名称的自定义预设，随 .blend 保存。')
            return {'FINISHED'}
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
        self.shapes = [(key, key.value) for _, keys in shape_blocks(self.meshes) for key in keys]
        r = self.scene.render
        self.saved = (self.scene.camera, r.engine, r.filepath, r.resolution_x, r.resolution_y,
                      r.resolution_percentage, r.image_settings.file_format, r.image_settings.color_mode,
                      r.film_transparent, self.scene.world, self.scene.view_settings.view_transform,
                      r.use_file_extension, r.use_border, r.use_compositing, r.use_sequencer)
        self.visibility = [(o, o.hide_render) for o in self.scene.objects if o.type in {'MESH', 'LIGHT'} and o not in self.meshes]

    def create_object(self, name, kind):
        data = bpy.data.cameras.new(name) if kind == 'CAMERA' else bpy.data.lights.new(name, 'AREA')
        obj = bpy.data.objects.new(name, data); self.scene.collection.objects.link(obj)
        self.created.append((obj, data, kind))
        return obj

    def setup(self, context):
        if not self.settings.output.strip():
            raise ValueError('输出目录不能为空。')
        root = Path(bpy.path.abspath(self.settings.output))
        self.folder = root / (safe_name(self.settings.trigger) + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        self.folder.mkdir(parents=True, exist_ok=False)
        self.manifest = {'status': 'running', 'style': self.settings.style, 'blender': bpy.app.version_string,
                         'warning': 'Game styles are approximations. Review poses, crops and captions before training.',
                         'shots': [], 'recipe': self.plan}
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
        r.resolution_x = r.resolution_y = self.settings.resolution; r.resolution_percentage = 100
        r.image_settings.file_format = 'PNG'; r.image_settings.color_mode = 'RGB'; r.film_transparent = False
        r.use_file_extension = True; r.use_border = False; r.use_compositing = False; r.use_sequencer = False
        self.scene.view_settings.view_transform = 'Standard'

    def flush(self):
        write_json(self.folder / 'manifest.json', self.manifest)

    def reset_character(self):
        for bone, mode, matrix in self.pose:
            bone.rotation_mode = mode; bone.matrix_basis = matrix
        for key, value in self.shapes:
            key.value = value

    def prepare_shot(self, context, shot):
        self.reset_character()
        expression_keys = expression_apply(self.settings, self.meshes, shot['expression'])
        pose_bones = pose_apply(self.settings, shot['pose'])
        context.view_layer.update()
        lo, hi = bounds(self.meshes, context.evaluated_depsgraph_get())
        h = hi.z - lo.z; center = (lo + hi) / 2
        group = shot['group']
        fraction, vertical = {'portrait': (0.29, 0.86), 'half_body': (0.62, 0.70),
                              'full_body': (1.0, 0.50), 'outfit_detail': (0.48, 0.49)}[group]
        target = Vector((center.x, center.y, lo.z + h * vertical))
        head = find_bone(self.settings.armature, 'head')
        if group == 'portrait' and head:
            head_pos = self.settings.armature.matrix_world @ head.head
            target.x = head_pos.x; target.y = head_pos.y
        angle = math.radians(self.settings.front + shot['azimuth'])
        direction = Vector((math.sin(angle), -math.cos(angle), 0))
        self.camera.location = target + direction * h * 3
        aim(self.camera, target)
        scale = h * fraction * self.settings.margin
        if group in {'full_body', 'half_body'}:
            right = self.camera.rotation_euler.to_matrix() @ Vector((1, 0, 0))
            corners = [obj.evaluated_get(context.evaluated_depsgraph_get()).matrix_world @ Vector(c)
                       for obj in self.meshes if not obj.hide_render
                       for c in obj.evaluated_get(context.evaluated_depsgraph_get()).bound_box]
            width = max((c - target).dot(right) for c in corners) - min((c - target).dot(right) for c in corners)
            scale = max(scale, width * self.settings.margin)
        self.camera.data.ortho_scale = scale
        self.camera.data.clip_start = max(h * 0.001, 0.0001); self.camera.data.clip_end = h * 20
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
        tags = caption(self.settings.trigger, group, expression, pose_label, self.settings.tags)
        image.with_suffix('.txt').write_text(tags + '\n', encoding='utf-8')
        self.manifest['shots'].append(dict(shot, image=str(image.relative_to(self.folder)), caption=tags,
                                           mapped_shape_keys=expression_keys, mapped_bones=pose_bones,
                                           pose_requires_review=True))
        self.flush()

    def close(self, status, error=''):
        try:
            self.reset_character()
            r = self.scene.render
            (self.scene.camera, r.engine, r.filepath, r.resolution_x, r.resolution_y,
             r.resolution_percentage, r.image_settings.file_format, r.image_settings.color_mode,
             r.film_transparent, self.scene.world, self.scene.view_settings.view_transform,
             r.use_file_extension, r.use_border, r.use_compositing, r.use_sequencer) = self.saved
            for obj, hidden in self.visibility:
                obj.hide_render = hidden
            for obj, data, kind in reversed(self.created):
                bpy.data.objects.remove(obj, do_unlink=True)
                (bpy.data.cameras if kind == 'CAMERA' else bpy.data.lights).remove(data)
            if hasattr(self, 'world'):
                bpy.data.worlds.remove(self.world)
        finally:
            self.settings.running = False
            if self.manifest is not None:
                self.manifest['status'] = status; self.manifest['error'] = error; self.flush()


class ML_OT_render(bpy.types.Operator):
    bl_idname = 'ml.render_dataset'; bl_label = '渲染三视图测试'
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
        self.shots = three_view_recipe(context.scene.ml_settings.preview_group) if self.test_only else recipe()
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

    @classmethod
    def poll(cls, context):
        return not context.scene.ml_settings.running

    def execute(self, context):
        session = None
        try:
            session = RenderSession(context); session.setup(context)
            shot = next(s for s in recipe() if s['group'] == context.scene.ml_settings.preview_group and s['index'] == 5)
            session.render(context, shot)
            session.close('preview')
            self.report({'INFO'}, '预览已写入：' + str(session.folder))
            return {'FINISHED'}
        except Exception as exc:
            if session:
                session.close('failed', str(exc))
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_PT_panel(bpy.types.Panel):
    bl_label = 'MMD LoRA Studio'; bl_idname = 'ML_PT_panel'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'MMD LoRA'

    def draw(self, context):
        settings = context.scene.ml_settings
        layout = self.layout; col = layout.column(); col.enabled = not settings.running
        col.prop(settings, 'armature'); col.prop(settings, 'style')
        col.label(text='先用 MMD Tools 导入 PMX / PMD', icon='INFO')
        col.prop(settings, 'textures'); col.operator('ml.textures'); col.operator('ml.assign_textures'); col.operator('ml.materials')
        box = col.box(); box.prop(settings, 'material_profiles')
        if settings.material_profiles:
            box.prop(settings, 'hair_highlight_strength'); box.prop(settings, 'face_fill'); box.prop(settings, 'face_shadow_strength')
            box.label(text='参数修改后重新应用材质')
        col.label(text='材质为风格近似；先检查预览')
        for prop, apply, save in [('expression', 'EXPRESSION', 'SAVE_EXPRESSION'), ('pose', 'POSE', 'SAVE_POSE')]:
            box = col.box(); box.prop(settings, prop)
            row = box.row(); row.operator('ml.preset', text='应用').action = apply
            row.operator('ml.preset', text='保存当前为预设').action = save
        col.prop(settings, 'front'); col.prop(settings, 'margin')
        col.prop(settings, 'trigger'); col.prop(settings, 'tags')
        col.prop(settings, 'resolution'); col.prop(settings, 'output')
        col.prop(settings, 'preview_group'); col.operator('ml.preview')
        col.label(text='肖像 / 半身 / 全身 / 衣服细节，各 9 张')
        col.operator('ml.render_dataset', text='三视图测试：正 / 侧 / 背', icon='RENDER_STILL').test_only = True
        col.operator('ml.render_dataset', text='完整数据集：36 张 + 标签').test_only = False
        layout.label(text=settings.progress)
        if settings.running:
            layout.label(text='Esc 在单张渲染结束后停止')


CLASSES = (MLSettings, ML_OT_materials, ML_OT_textures, ML_OT_assign_textures, ML_OT_preset, ML_OT_render, ML_OT_preview, ML_PT_panel)


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
