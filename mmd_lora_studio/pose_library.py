"""Read local pose packs without renaming, copying or distributing their assets."""
import hashlib
import importlib
import math
import re
from pathlib import Path

from .core import normalized_name

REFERENCE_SLOTS = {
    'idol_peace': ('偶像·脸侧比耶', 'portrait:5', ['right_wrist']),
    'idol_turn': ('偶像·回身叉腰', 'full_body:7', []),
    'idol_one_leg': ('偶像·单腿元气', 'full_body:5', []),
    'heart_face': ('比心·脸前', 'portrait:8', ['left_wrist', 'right_wrist']),
    'heart_chest': ('比心·胸前', 'half_body:3', ['left_wrist', 'right_wrist']),
    'heart_arms': ('比心·双臂', 'half_body:6', ['left_wrist', 'right_wrist']),
}
REFERENCE_CAMERA_OFFSETS = {'idol_peace': 10, 'idol_turn': -15, 'idol_one_leg': 10,
                            'heart_face': 0, 'heart_chest': 0, 'heart_arms': 0}


def display_filename(value):
    # Common CP932 ZIP filenames decoded as GBK. Fix the display only.
    if sum(value.count(token) for token in ('偄', '僘', '億', '僴', '傐', '乕', '偲', '傾')) >= 2:
        try:
            return value.encode('cp936').decode('cp932')
        except UnicodeError:
            pass
    return value


def humanoid_control(name):
    """MMD body/hand/IK controls; source-specific hair and cloth stay local."""
    return bool(re.fullmatch(r'(?:全ての親|センター|グルーブ|腰|下半身|上半身[23]?|首|頭|両目|'
                             r'[左右](?:目|肩[pc]?|肩キャンセル|腕(?:捩[1-3]?)?|ひじ|肘|手捩[1-3]?|手首|'
                             r'(?:親指|人指|人差指|中指|薬指|小指)[0-3]|足(?:d|ik親|ik|先ex)?|ひざd?|足首d?|つま先ik))',
                             normalized_name(name)))


def scan_pose_folder(directory):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError('本地姿势目录不存在。')
    files = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() == '.vpd')
    if len(files) > 256:
        raise ValueError('单次最多读取 256 个 VPD，请选择具体姿势包目录。')
    entries = []
    for path in files:
        relative = display_filename(path.relative_to(root).as_posix())
        stem = display_filename(path.stem)
        paired = any(token in relative.casefold() for token in ('ふたり', '二人', '双人', '2人', 'couple', 'two_person'))
        role = None
        if 'アイドル' in relative:
            role = {'ぽい1': 'idol_peace', 'ぽい2': 'idol_turn', 'ぽい3': 'idol_one_leg'}.get(stem)
        elif '天使の' in relative:
            role = 'heart_chest' if '(胸)' in stem else 'heart_face'
        elif '腕で' in relative:
            role = 'heart_arms'
        title = REFERENCE_SLOTS[role][0] if role else (display_filename(path.parent.name)+' / '+stem)[:72]
        preview = next((p for p in sorted(path.parent.glob('*.png')) if display_filename(p.stem) in {stem, stem.replace('ポーズ', ''), stem.replace('羽ハート', 'ハート')}), None)
        entries.append({'path': str(path), 'relative': relative, 'name': '本地·'+title, 'role': role,
                        'paired': paired, 'preview': str(preview) if preview else None})
    return entries


def read_vpd(settings, meshes, filepath, name=None, scale=.08, humanoid_only=False):
    """Validate all records before editing, then save one static local preset."""
    import bpy
    from mathutils import Matrix, Vector
    from .mmd_compat import mmd_api
    from .performance import aliases, find_bone, restore_seated, remember_pose_state, capture_pose, custom_presets, store_presets, shape_blocks, expression_apply
    rig = settings.armature
    if rig.animation_data and rig.animation_data.action:
        raise ValueError('请先在角色副本中解除活动 Action，再导入静态 VPD。')
    path = Path(filepath)
    if not path.is_file() or path.stat().st_size > 1024*1024:
        raise ValueError('VPD 不存在或超过 1 MiB。')
    package = mmd_api().__package__.rsplit('.core', 1)[0]
    data = importlib.import_module(package+'.core.vpd').File(); data.load(filepath=str(path))
    converter = importlib.import_module(package+'.core.vmd.importer').BoneConverter
    # Japanese MMD names take precedence over ambiguous English helper aliases.
    indexed = {}
    for bone in rig.pose.bones:
        for alias in aliases(bone):
            if alias:
                indexed.setdefault(normalized_name(alias), bone)
    for bone in rig.pose.bones:
        japanese = getattr(getattr(bone, 'mmd_bone', None), 'name_j', '')
        if japanese:
            indexed[normalized_name(japanese)] = bone
    matrices = []; missing = []; missing_active = []; skipped_secondary = []
    for record in data.bones:
        values = tuple(record.location)+tuple(record.rotation)
        if not all(math.isfinite(value) for value in values):
            raise ValueError('VPD 骨骼数据包含非有限数值。')
        if humanoid_only and not humanoid_control(record.bone_name):
            skipped_secondary.append(record.bone_name)
            continue
        bone = indexed.get(normalized_name(record.bone_name))
        if bone is None:
            missing.append(record.bone_name)
            if any(abs(value) > 1e-5 for value in tuple(record.location)+tuple(record.rotation)[:3]):
                missing_active.append(record.bone_name)
            continue
        convert = converter(bone, scale)
        matrix = Matrix.Translation(convert.convert_location(record.location)) @ convert.convert_rotation(record.rotation).to_matrix().to_4x4()
        if not all(math.isfinite(value) for row in matrix for value in row):
            raise ValueError('VPD 转换产生了无效矩阵。')
        matrices.append((bone, matrix))
    if not matrices:
        raise ValueError('VPD 没有匹配当前骨架的骨骼。')
    if any(not math.isfinite(m.weight) or not -10 <= m.weight <= 10 for m in data.morphs):
        raise ValueError('VPD 表情权重无效。')
    name = name or display_filename(path.stem)
    if not 1 <= len(name) <= 80:
        raise ValueError('预设名称须为 1–80 字符。')
    restore_seated(rig)
    remember_pose_state(rig, list(rig.pose.bones))
    for bone in rig.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
    for bone, matrix in matrices:
        bone.matrix_basis = matrix
    bpy.context.view_layer.update()
    library = custom_presets(settings)
    library['poses'][name] = capture_pose(settings)
    # Native VPDs author leg rotations together with foot/toe IK targets.
    # A large thigh angle alone is not evidence that IK should be disabled.
    library['pose_options'][name] = {'leg_mode': 'KEEP'}
    morphs = {normalized_name(m.morph_name): m.weight for m in data.morphs}
    matched = [(key, morphs[normalized_name(key.name)]) for _, keys in shape_blocks(meshes)
               for key in keys[1:] if normalized_name(key.name) in morphs]
    if matched:
        expression_apply(settings, meshes, 'neutral')
        for key, weight in matched:
            key.value = weight
        library['expressions'][name] = {obj.name: {key.name: key.value for key in keys[1:] if normalized_name(key.name) in morphs}
                                       for obj, keys in shape_blocks(meshes)}
    store_presets(settings, library)
    head_yaw = 0
    head = find_bone(rig, 'head', settings)
    if head:
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        front = math.radians(settings.front)
        forward = rig.matrix_world.to_3x3().inverted() @ Vector((math.sin(front), -math.cos(front), 0))
        rotation = evaluated.pose.bones[head.name].matrix.to_quaternion() @ head.bone.matrix_local.to_quaternion().inverted()
        facing = rig.matrix_world.to_3x3() @ (rotation @ forward)
        if math.hypot(facing.x, facing.y) > .00001:
            head_yaw = (math.degrees(math.atan2(facing.x, -facing.y))-settings.front+180) % 360-180
    return {'name': name, 'bones_total': len(data.bones), 'bones_mapped': len(matrices), 'missing': missing,
            'missing_active': missing_active, 'has_expression': bool(matched),
            'skipped_secondary': skipped_secondary,
            'head_yaw': head_yaw,
            'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
