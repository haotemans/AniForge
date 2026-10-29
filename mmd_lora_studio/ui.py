"""Compact native Blender panels for the character → preview → output workflow."""
from pathlib import Path

import bpy

from .camera import resolution
from .core import GROUP_LABELS
from . import captioning


def ready(context):
    settings = context.scene.ml_settings
    return bool(settings.armature and settings.armature.name in context.scene.objects)


def controls(layout, settings):
    col = layout.column()
    col.enabled = not (settings.running or settings.caption_running)
    col.use_property_split = True
    col.use_property_decorate = False
    return col


def fold(layout, settings, prop, title):
    row = layout.row()
    row.use_property_split = False
    row.prop(settings, prop, text=title, icon='TRIA_DOWN' if getattr(settings, prop) else 'TRIA_RIGHT', emboss=False)
    return layout.box().column() if getattr(settings, prop) else None


def status(settings):
    text = settings.progress
    for old, new in (('complete:', '已完成 ·'), ('preview:', '预览完成 ·'),
                     ('cancelled:', '已停止 ·'), ('failed:', '渲染失败 ·')):
        text = text.replace(old, new)
    for name, label in GROUP_LABELS.items():
        text = text.replace(name, label)
    return text


class ML_OT_pick_character(bpy.types.Operator):
    bl_idname = 'ml.pick_character'
    bl_label = '识别当前选中的角色'
    bl_description = '从选中的网格或骨架识别角色；场景只有一个骨架时可直接选择'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return not context.scene.ml_settings.running

    def execute(self, context):
        obj = context.active_object
        rig = obj if obj and obj.type == 'ARMATURE' else obj.find_armature() if obj else None
        if rig is None:
            candidates = [o for o in context.scene.objects if o.type == 'ARMATURE']
            if len(candidates) == 1:
                rig = candidates[0]
        if rig is None:
            self.report({'ERROR'}, '请选择角色网格或骨架，再点击识别。')
            return {'CANCELLED'}
        context.scene.ml_settings.armature = rig
        self.report({'INFO'}, '已选择角色：'+rig.name)
        return {'FINISHED'}


class ML_OT_open_output(bpy.types.Operator):
    bl_idname = 'ml.open_output'
    bl_label = '打开输出目录'
    bl_description = '在文件管理器中打开当前输出目录'

    def execute(self, context):
        value = context.scene.ml_settings.output.strip()
        directory = Path(bpy.path.abspath(value)) if value else None
        if directory is None or not directory.is_dir():
            self.report({'ERROR'}, '输出目录尚不存在，请先渲染或选择已有目录。')
            return {'CANCELLED'}
        return bpy.ops.wm.path_open(filepath=str(directory))


class ML_PT_panel(bpy.types.Panel):
    bl_label = 'AniForge'; bl_idname = 'ML_PT_panel'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'MMD LoRA'

    def draw(self, context):
        settings = context.scene.ml_settings
        self.layout.label(text='T Pose · 16 张标准视图', icon='RENDER_STILL')
        self.layout.label(text='自动相机 · 白底 · 主体裁剪', icon='VIEW_CAMERA')
        if settings.running:
            box = self.layout.box()
            box.label(text=status(settings), icon='TIME')
            box.label(text='Esc：当前图片完成后停止')
        elif settings.progress != '等待开始':
            self.layout.label(text=status(settings), icon='ERROR' if settings.progress.startswith('failed:') else 'INFO')
        if settings.caption_status:
            self.layout.label(text=settings.caption_status, icon='INFO')
        if settings.caption_error:
            self.layout.label(text=settings.caption_error, icon='ERROR')
        if settings.caption_running:
            self.layout.operator('ml.cancel_caption', text='当前批次后停止打标', icon='CANCEL')


class ML_PT_character(bpy.types.Panel):
    bl_label = '1  角色与材质'; bl_idname = 'ML_PT_character'
    bl_parent_id = 'ML_PT_panel'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'MMD LoRA'
    bl_order = 0

    def draw(self, context):
        settings = context.scene.ml_settings
        col = controls(self.layout, settings)
        row = col.row(align=True)
        row.prop(settings, 'armature', text='角色')
        row.operator('ml.pick_character', text='', icon='EYEDROPPER')
        if not ready(context):
            col.label(text='导入 PMX 后选择角色骨架', icon='INFO')
        col.prop(settings, 'style', text='游戏风格')
        actions = col.row(align=True); actions.enabled = ready(context); actions.scale_y = 1.2
        actions.operator('ml.materials', text='应用三渲二', icon='MATERIAL')
        actions.operator('ml.tpose', text='T Pose', icon='ARMATURE_DATA')
        textures = fold(col, settings, 'ui_textures', '贴图修复')
        if textures is not None:
            textures.prop(settings, 'textures', text='贴图目录')
            actions = textures.column(align=True); actions.enabled = ready(context)
            actions.operator('ml.textures', text='重连丢失贴图', icon='FILE_REFRESH')
            actions.operator('ml.assign_textures', text='按材质名匹配贴图', icon='IMAGE_DATA')
        material = fold(col, settings, 'ui_material', '材质微调')
        if material is not None:
            material.prop(settings, 'material_profiles', text='头发与脸部优化')
            values = material.column(); values.enabled = settings.material_profiles
            for prop, label in (('hair_highlight_strength', '高光强度'), ('face_fill', '脸部补光'), ('face_shadow_strength', '阴影强度')):
                values.prop(settings, prop, text=label, slider=True)
            material.label(text='调整后重新应用三渲二', icon='INFO')


class ML_PT_framing(bpy.types.Panel):
    bl_label = '2  构图与预览'; bl_idname = 'ML_PT_framing'
    bl_parent_id = 'ML_PT_panel'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'MMD LoRA'
    bl_order = 1

    def draw(self, context):
        settings = context.scene.ml_settings
        col = controls(self.layout, settings)
        col.label(text='取景范围')
        grid = col.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
        for group, label in GROUP_LABELS.items():
            grid.prop_enum(settings, 'preview_group', group, text=label)
        col.label(text='观察方向')
        grid = col.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
        for view, label in (('FRONT', '正面'), ('LEFT', '左侧'), ('RIGHT', '右侧'), ('BACK', '背面')):
            grid.prop_enum(settings, 'preview_view', view, text=label)
        col.prop(settings, 'camera_aspect', text='画幅')
        col.prop(settings, 'camera_projection', text='投影')
        if settings.camera_projection == 'PERSP':
            col.prop(settings, 'camera_lens', text='焦距 (mm)')
        if settings.preview_group == 'portrait':
            col.prop(settings, 'portrait_focus', text='肖像范围')
        actions = col.column(align=True); actions.enabled = ready(context)
        actions.scale_y = 1.15
        actions.operator('ml.shot_plan', text='刷新自动相机 · 不出图', icon='VIEW_CAMERA')
        actions.operator('ml.preview', text='渲染当前视图 · 1 张', icon='RENDER_STILL')
        advanced = fold(col, settings, 'ui_camera', '高级镜头')
        if advanced is not None:
            for prop, label in (('front', '角色正面'), ('margin', '留白'), ('camera_zoom', '缩放'),
                                ('camera_azimuth', '环绕偏移'), ('camera_elevation', '俯仰'), ('camera_roll', '倾斜'),
                                ('camera_shift_x', '水平移轴'), ('camera_shift_y', '垂直移轴'), ('camera_target_offset', '对焦偏移')):
                advanced.prop(settings, prop, text=label)
            advanced.prop(settings, 'crop_padding', text='输出留白 (%)')
        presets = fold(col, settings, 'ui_presets', '镜头预设')
        if presets is not None:
            presets.prop(settings, 'camera_preset', text='已有预设')
            presets.prop(settings, 'camera_name', text='保存名称')
            row = presets.row(align=True)
            row.operator('ml.camera_preset', text='应用', icon='CHECKMARK')
            row.operator('ml.camera_preset', text='保存', icon='FILE_TICK').save = True
            row = presets.row(align=True)
            row.operator('ml.import_cameras', text='导入 JSON', icon='IMPORT')
            row.operator('ml.export_cameras', text='导出 JSON', icon='EXPORT')


class ML_PT_output(bpy.types.Panel):
    bl_label = '3  渲染与输出'; bl_idname = 'ML_PT_output'
    bl_parent_id = 'ML_PT_panel'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'MMD LoRA'
    bl_order = 2

    def draw(self, context):
        settings = context.scene.ml_settings
        col = controls(self.layout, settings)
        col.prop(settings, 'trigger', text='角色名')
        col.prop(settings, 'resolution', text='渲染长边 (px)')
        col.prop(settings, 'auto_crop', text='自动裁掉白边')
        col.prop(settings, 'api_caption', text='渲染后 API 打标')
        row = col.row(align=True)
        row.prop(settings, 'output', text='输出到')
        row.operator('ml.open_output', text='', icon='FILE_FOLDER')
        width, height = resolution(settings)
        summary = col.box()
        summary.label(text=f'渲染画布 {width} × {height} px', icon='IMAGE_DATA')
        summary.label(text='白底 · 裁剪后尺寸随主体变化' if settings.auto_crop else '白底 · PNG + 标签')
        counts = summary.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        for label in GROUP_LABELS.values():
            counts.label(text=label+' 4 张')
        actions = col.column(); actions.enabled = ready(context) and bool(settings.output.strip())
        actions.scale_y = 1.5
        actions.operator('ml.render_dataset', text='开始渲染 · 全部 16 张', icon='RENDER_STILL').test_only = False
        tags = fold(col, settings, 'ui_tags', 'API 图片打标')
        if tags is not None:
            tags.label(text='@3d, 角色名, 图片详细描述')
            captioning.draw_config(tags, context)
            tags.separator()
            tags.prop(settings, 'caption_folder', text='补打标目录')
            tags.operator('ml.caption_images', text='打标已有图片 / 重试失败项', icon='FILE_REFRESH')
        developer = fold(col, settings, 'ui_developer', '开发检查')
        if developer is not None:
            actions = developer.column(align=True); actions.enabled = ready(context)
            actions.operator('ml.render_dataset', text='正 / 侧 / 背 · 输出 3 张', icon='RENDER_STILL').test_only = True
            developer.operator('ml.shot_plan', text='查看 16 张清单', icon='TEXT').report_only = True


CLASSES = (ML_OT_pick_character, ML_OT_open_output, ML_PT_panel, ML_PT_character, ML_PT_framing, ML_PT_output)
