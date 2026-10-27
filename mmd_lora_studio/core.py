"""Dataset recipes. No Blender dependency so planning can be tested separately."""
import json
import re
import math
import unicodedata
from pathlib import Path

STYLES = {
    'HSR': ('崩坏：星穹铁道', 0.48, 0.62),
    'ZZZ': ('绝区零', 0.55, 0.48),
    'GENSHIN': ('原神', 0.42, 0.68),
    'WUWA': ('鸣潮', 0.46, 0.72),
    'ENDFIELD': ('明日方舟：终末地', 0.50, 0.75),
    'NEVERNESS': ('异环', 0.44, 0.64),
}
GROUPS = ('portrait', 'half_body', 'full_body', 'outfit_detail')
# Azimuth is relative to the model's configured front. All views show the face.
ANGLES = (-55, -40, -25, -10, 0, 10, 25, 40, 55)
EXPRESSIONS = ('neutral', 'smile', 'happy', 'blink', 'wink', 'angry', 'sad', 'surprised', 'neutral')
POSES = ('relaxed', 'hands_front', 'hand_hip', 'presenting', 'relaxed', 'wave', 'open_arms', 'hands_front', 'relaxed')
MORPHS = {
    'smile': [('口角上げ', 'にやり', 'jawOpen_Smile_01_CLO', 'smile', 'Smile', 'にこり')],
    'happy': [('笑い', 'smile', 'Smile'), ('あ', 'a', 'A')],
    'blink': [('まばたき', '瞬き', 'blink', 'Blink')],
    'wink': [('ウィンク', 'ウインク', 'wink', 'Wink')],
    'angry': [('怒り', '怒', 'angry', 'Angry', '怒り目', '怒り目２')],
    'sad': [('困る', '悲しい', 'sad', 'Sad', '悲しむ', '悲し', '悲しい目')],
    'surprised': [('びっくり', '驚き', 'surprised', 'Surprised'), ('お', 'o', 'O')],
}
BONES = {
    'left_arm': ('左腕', 'arm_L', 'upper_arm.L', 'UpperArm_L'),
    'right_arm': ('右腕', 'arm_R', 'upper_arm.R', 'UpperArm_R'),
    'left_elbow': ('左ひじ', '左肘', 'elbow_L', 'forearm.L'),
    'right_elbow': ('右ひじ', '右肘', 'elbow_R', 'forearm.R'),
    'head': ('頭', 'head', 'Head'),
    'left_wrist': ('左手首', 'wrist_L', 'hand.L', 'LeftHand'),
    'right_wrist': ('右手首', 'wrist_R', 'hand.R', 'RightHand'),
}
# Local XYZ Euler offsets, intended as starting points for standard MMD rigs.
POSE_ROTATIONS = {
    'relaxed': {'left_arm': (0, 0, -65), 'right_arm': (0, 0, 65)},
    'hand_hip': {'left_arm': (0, 0, -48), 'right_arm': (0, 0, 65), 'left_elbow': (0, -65, 0)},
    'wave': {'left_arm': (0, 0, -65), 'right_arm': (0, 0, -20), 'right_elbow': (0, 0, -100)},
    'open_arms': {'left_arm': (0, 0, -30), 'right_arm': (0, 0, 30)},
}

# Desired limb directions in character coordinates: X = character left,
# Y = forward, Z = up. Solving a swing from each bone's current axis works
# across both T/A poses and differing MMD bone rolls.
POSE_DIRECTIONS = {
    'relaxed': {'left_arm': (.15, .03, -1), 'right_arm': (-.15, .03, -1),
                'left_elbow': (.08, .04, -1), 'right_elbow': (-.08, .04, -1)},
    'hand_hip': {'left_arm': (.6, .03, -.8), 'right_arm': (-.15, .03, -1),
                 'left_elbow': (-.8, .35, -.2), 'right_elbow': (-.08, .04, -1)},
    'wave': {'left_arm': (.15, .03, -1), 'right_arm': (-.85, .03, .53),
             'left_elbow': (.08, .04, -1), 'right_elbow': (-.08, .12, 1)},
    'open_arms': {'left_arm': (.9, .1, -.43), 'right_arm': (-.9, .1, -.43),
                  'left_elbow': (.9, .1, -.43), 'right_elbow': (-.9, .1, -.43)},
    'hands_front': {'left_arm': (.2, .25, -1), 'right_arm': (-.2, .25, -1),
                    'left_elbow': (-.25, .55, -.65), 'right_elbow': (.25, .55, -.65)},
    'presenting': {'left_arm': (.15, .03, -1), 'right_arm': (-.8, .1, -.6),
                   'left_elbow': (.08, .04, -1), 'right_elbow': (-.6, .65, .3)},
}

EXPRESSION_LABELS = {'neutral': '中性', 'smile': '微笑', 'happy': '开心', 'blink': '闭眼',
                     'wink': '眨单眼', 'angry': '生气', 'sad': '悲伤', 'surprised': '惊讶'}
POSE_LABELS = {'relaxed': '自然站姿', 'hand_hip': '单手叉腰', 'wave': '招手', 'open_arms': '张开双臂',
              'hands_front': '双手身前', 'presenting': '单手展示'}
EXPRESSION_CHANNELS = {
    'smile': [(MORPHS['smile'][0], .7)],
    'happy': [(('口角上げ', 'にやり', 'smile', 'jawOpen_Happy_01_CLO'), .8),
              (('笑い', 'EL_Happy_l_CLO', 'eyeSmile'), .55), (('あ', 'あ2', 'Mouth_A01', 'jawOpen_a'), .18)],
    'blink': [(MORPHS['blink'][0], 1)], 'wink': [(MORPHS['wink'][0], 1)],
    'angry': [(MORPHS['angry'][0], .65), (('怒り目', 'EL_Angry_01_OP'), .45)],
    'sad': [(MORPHS['sad'][0], .65), (('口角下げ',), .3)],
    'surprised': [(MORPHS['surprised'][0], .7), (('お', 'o', 'O', 'Mouth_Open01', 'jawOpen_o'), .3)],
}


def normalized_name(name):
    return unicodedata.normalize('NFKC', name).strip().casefold()


def empty_library():
    return {'schema_version': 1, 'expressions': {}, 'poses': {}, 'cameras': {}, 'bone_map': {}}


def validate_library(value):
    if not isinstance(value, dict) or value.get('schema_version', 1) != 1:
        raise ValueError('不支持的预设文件版本。')
    result = empty_library()
    for category in ('expressions', 'poses', 'cameras', 'bone_map'):
        data = value.get(category, {})
        if not isinstance(data, dict):
            raise ValueError('预设分类格式错误：' + category)
        result[category] = data
    def numbers(item):
        if isinstance(item, dict):
            if not all(isinstance(k, str) and len(k) <= 256 for k in item):
                raise ValueError('预设键名无效。')
            for v in item.values():
                numbers(v)
        elif isinstance(item, (list, tuple)):
            for v in item:
                numbers(v)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError('预设不能包含非有限数值。')
    numbers(result)
    for preset in result['expressions'].values():
        if not isinstance(preset, dict):
            raise ValueError('表情预设必须按网格记录形态键。')
        for weights in preset.values():
            if not isinstance(weights, dict) or any(not isinstance(w, (int, float)) or not -10 <= w <= 10 for w in weights.values()):
                raise ValueError('表情权重格式错误。')
    for preset in result['poses'].values():
        if not isinstance(preset, dict):
            raise ValueError('姿势预设格式错误。')
        for matrix in preset.values():
            if not isinstance(matrix, list) or len(matrix) != 4 or any(not isinstance(row, list) or len(row) != 4 or any(not isinstance(v, (int, float)) for v in row) for row in matrix):
                raise ValueError('骨骼矩阵必须为 4×4 数值。')
    camera_enums = {'camera_projection': {'ORTHO', 'PERSP'}, 'camera_aspect': {'SQUARE', 'PORTRAIT', 'LANDSCAPE'},
                    'portrait_focus': {'HEAD', 'FACE'}, 'preview_group': set(GROUPS)}
    camera_ranges = {'camera_lens': (20, 200), 'camera_azimuth': (-180, 180), 'camera_elevation': (-60, 60),
                     'camera_roll': (-45, 45), 'camera_zoom': (.5, 2), 'camera_shift_x': (-.4, .4),
                     'camera_shift_y': (-.4, .4), 'margin': (1.02, 2)}
    for preset in result['cameras'].values():
        if not isinstance(preset, dict):
            raise ValueError('镜头预设必须为参数字典。')
        for key, value in preset.items():
            if key in camera_enums:
                if not isinstance(value, str) or value not in camera_enums[key]:
                    raise ValueError('镜头选项无效：' + key)
            elif key in camera_ranges:
                low, high = camera_ranges[key]
                if not isinstance(value, (int, float)) or not low <= value <= high:
                    raise ValueError('镜头参数超出范围：' + key)
            elif key == 'camera_target_offset':
                if not isinstance(value, (list, tuple)) or len(value) != 3 or any(not isinstance(v, (int, float)) or not -.5 <= v <= .5 for v in value):
                    raise ValueError('对焦偏移必须为三个 -0.5 到 0.5 之间的数值。')
            else:
                raise ValueError('未知镜头参数：' + key)
    if any(not isinstance(name, str) for name in result['bone_map'].values()):
        raise ValueError('骨骼映射必须为名称字符串。')
    return result


def safe_name(value):
    value = re.sub(r'[^\w-]+', '_', value, flags=re.UNICODE).strip('_')[:80]
    return value or 'character'


def recipe():
    return [dict(group=g, index=i + 1, azimuth=angle,
                 expression=EXPRESSIONS[i], pose=POSES[i])
            for g in GROUPS for i, angle in enumerate(ANGLES)]


def three_view_recipe(group='portrait', pose='relaxed', expression='neutral'):
    if group not in GROUPS:
        raise ValueError('Unknown framing group: ' + group)
    return [dict(group=group, index=i+1, azimuth=angle, expression=expression, pose=pose, view=view)
            for i, (view, angle) in enumerate((('front', 0), ('side', 90), ('back', 180)))]


def caption(trigger, shot, expression, pose, extra=''):
    framing = {'portrait': 'portrait, face focus', 'half_body': 'upper body',
               'full_body': 'full body', 'outfit_detail': 'outfit detail, clothing focus'}
    return ', '.join(x for x in (trigger.strip(), '1person', framing[shot],
                                expression.replace('_', ' '), pose.replace('_', ' '), extra.strip()) if x)


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
