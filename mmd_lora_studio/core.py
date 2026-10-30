"""One A pose, four framings, four views. No animation or expression library."""
import hashlib
import json
import re
import unicodedata
import math
from pathlib import Path

STYLES = {
    'HSR': ('崩坏：星穹铁道', 0.48, 0.62),
    # Preserve cel boundaries on clothing without crushing its rear details.
    'ZZZ': ('绝区零', 0.55, 0.68),
    'GENSHIN': ('原神', 0.42, 0.68),
    'WUWA': ('鸣潮', 0.46, 0.72),
    'ENDFIELD': ('明日方舟：终末地', 0.50, 0.75),
    'NEVERNESS': ('异环', 0.44, 0.64),
}
BONES = {
    'upper_body': ('上半身', 'upper body', 'spine', 'spine.001', 'Spine'),
    'upper_body2': ('上半身2', '上半身２', 'upper body2', 'chest', 'spine.002', 'Chest'),
    'lower_body': ('下半身', 'lower body', 'hips', 'pelvis', 'Hips'),
    'left_arm': ('左腕', 'arm_L', 'upper_arm.L', 'UpperArm_L'),
    'right_arm': ('右腕', 'arm_R', 'upper_arm.R', 'UpperArm_R'),
    'left_elbow': ('左ひじ', '左肘', 'elbow_L', 'forearm.L'),
    'right_elbow': ('右ひじ', '右肘', 'elbow_R', 'forearm.R'),
    'head': ('頭', 'head', 'Head'),
    'left_wrist': ('左手首', 'wrist_L', 'hand.L', 'LeftHand'),
    'right_wrist': ('右手首', 'wrist_R', 'hand.R', 'RightHand'),
    'left_shoulder': ('左肩', 'shoulder_L', 'shoulder.L', 'LeftShoulder'),
    'right_shoulder': ('右肩', 'shoulder_R', 'shoulder.R', 'RightShoulder'),
    'center': ('センター', 'center', 'Center', 'root', 'Root'),
    'left_leg': ('左足', 'leg_L', 'thigh.L', 'UpperLeg_L', 'LeftUpLeg'),
    'right_leg': ('右足', 'leg_R', 'thigh.R', 'UpperLeg_R', 'RightUpLeg'),
    'left_knee': ('左ひざ', '左膝', 'knee_L', 'shin.L', 'LeftLeg'),
    'right_knee': ('右ひざ', '右膝', 'knee_R', 'shin.R', 'RightLeg'),
    'left_ankle': ('左足首', 'ankle_L', 'foot.L', 'LeftFoot'),
    'right_ankle': ('右足首', 'ankle_R', 'foot.R', 'RightFoot'),
}

GROUP_LABELS = {'portrait': '肖像', 'half_body': '半身', 'full_body': '全身', 'lower_body': '下半身'}
GROUPS = tuple(GROUP_LABELS)
VIEWS = (('front', '正面', 0), ('left', '左侧', 90), ('right', '右侧', -90), ('back', '背面', 180))


def normalized_name(name):
    return unicodedata.normalize('NFKC', name).strip().casefold()


def safe_name(value):
    value = re.sub(r'[^\w-]+', '_', value, flags=re.UNICODE).strip('_')[:80]
    return value or 'character'


def recipe():
    return [dict(group=group, index=index, title=GROUP_LABELS[group]+'·'+label,
                 view=view, azimuth=angle, elevation=0, pose='apose', expression='neutral')
            for group in GROUPS for index, (view, label, angle) in enumerate(VIEWS, 1)]


def three_view_recipe(group='portrait'):
    if group not in GROUPS:
        raise ValueError('Unknown framing group: '+group)
    return [dict(shot, index=i) for i, shot in enumerate(
        (s for s in recipe() if s['group'] == group and s['view'] != 'right'), 1)]


def dataset_coverage(shots):
    return {'total': len(shots), 'per_group': {g: sum(s['group'] == g for s in shots) for g in GROUPS},
            'pose': 'apose', 'expression': 'neutral'}


def dataset_metadata_dir(folder):
    """Keep manifests, caches and temporary files beside the training folder."""
    folder = Path(folder).resolve()
    key = hashlib.sha256(str(folder).encode('utf-8')).hexdigest()[:12]
    return folder.parent / '.aniforge' / (safe_name(folder.name) + '_' + key)


def padded_crop(width, height, bounds, padding_percent=3):
    """Half-open pixel rectangle, origin at the bottom left; never cut foreground."""
    if width < 1 or height < 1 or not 0 <= padding_percent <= 20:
        raise ValueError('裁剪尺寸或留白比例无效。')
    left, bottom, right, top = bounds
    if not (0 <= left < right <= width and 0 <= bottom < top <= height):
        raise ValueError('角色轮廓为空或超出图像。')
    pad = math.ceil(max(right-left, top-bottom)*padding_percent/100)
    return max(0, left-pad), max(0, bottom-pad), min(width, right+pad), min(height, top+pad)


class ShotSettings:
    """Camera overlay: the UI stays untouched while fitting a dataset view."""
    def __init__(self, settings, shot):
        self._settings = settings
        self._overrides = shot.get('camera', {})

    def __getattr__(self, name):
        return self._overrides[name] if name in self._overrides else getattr(self._settings, name)


def caption(trigger, group, view):
    framing = {'portrait': 'portrait, head focus', 'half_body': 'upper body',
               'full_body': 'full body', 'lower_body': 'lower body, legs, feet'}
    direction = {'front': 'front view', 'left': 'left side view', 'right': 'right side view', 'back': 'from behind', 'side': 'side view'}
    # Cropped portraits do not show the arms and should not teach a visible A pose.
    pose = 'a-pose, arms angled down' if group in {'half_body', 'full_body'} else ''
    return ', '.join(x for x in ('@3d', trigger.strip(), '1person', framing[group], direction[view], pose) if x)


def validate_camera_library(value):
    """Retain camera presets from old libraries, discarding all pose/face data."""
    if not isinstance(value, dict) or not isinstance(value.get('cameras', {}), dict):
        raise ValueError('镜头预设必须包含 cameras 字典。')
    enums = {'camera_projection': {'ORTHO', 'PERSP'}, 'camera_aspect': {'SQUARE', 'PORTRAIT', 'LANDSCAPE'},
             'portrait_focus': {'HEAD', 'FACE'}, 'preview_group': set(GROUPS)}
    ranges = {'camera_lens': (20, 200), 'camera_azimuth': (-180, 180), 'camera_elevation': (-60, 60),
              'camera_roll': (-45, 45), 'camera_zoom': (.5, 2), 'camera_shift_x': (-.4, .4),
              'camera_shift_y': (-.4, .4), 'margin': (1.02, 2)}
    result = {'schema_version': 4, 'cameras': {}}
    for name, preset in value.get('cameras', {}).items():
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or not isinstance(preset, dict):
            raise ValueError('镜头预设名称或参数无效。')
        camera = dict(preset)
        if camera.get('preview_group') == 'outfit_detail':
            camera['preview_group'] = 'lower_body'
        for key, data in camera.items():
            if key in enums:
                if not isinstance(data, str) or data not in enums[key]:
                    raise ValueError('镜头选项无效：'+key)
            elif key in ranges:
                low, high = ranges[key]
                if not isinstance(data, (int, float)) or not math.isfinite(data) or not low <= data <= high:
                    raise ValueError('镜头参数超出范围：'+key)
            elif key == 'camera_target_offset':
                if not isinstance(data, (list, tuple)) or len(data) != 3 or any(
                        not isinstance(v, (int, float)) or not math.isfinite(v) or not -.5 <= v <= .5 for v in data):
                    raise ValueError('对焦偏移无效。')
            else:
                raise ValueError('未知镜头参数：'+key)
        result['cameras'][name] = camera
    return result


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
