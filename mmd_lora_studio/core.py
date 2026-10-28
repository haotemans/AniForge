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
    'peace_face': {'left_arm': (.18, .02, -1), 'right_arm': (-.65, .15, -.65),
                   'left_elbow': (.08, .04, -1), 'right_elbow': (.2, .3, 1)},
    'thinking': {'left_arm': (.2, .08, -1), 'right_arm': (-.3, .25, -.9),
                 'left_elbow': (-.2, .5, -.6), 'right_elbow': (.25, .6, 1)},
    'shy': {'left_arm': (.22, .18, -1), 'right_arm': (-.22, .18, -1),
            'left_elbow': (-.28, .7, .6), 'right_elbow': (.28, .7, .6)},
    'cheer': {'left_arm': (.6, .12, .8), 'right_arm': (-.6, .12, .8),
              'left_elbow': (-.15, .08, 1), 'right_elbow': (.15, .08, 1)},
    'cat_paws': {'left_arm': (.45, .18, -.7), 'right_arm': (-.45, .18, -.7),
                 'left_elbow': (-.3, .7, .8), 'right_elbow': (.3, .7, .8)},
    'salute': {'left_arm': (.15, .03, -1), 'right_arm': (-1, .12, .15),
               'left_elbow': (.08, .04, -1), 'right_elbow': (.55, .25, .8)},
    'hands_behind': {'left_arm': (.12, -.25, -1), 'right_arm': (-.12, -.25, -1),
                     'left_elbow': (-.35, -.4, -.8), 'right_elbow': (.35, -.4, -.8)},
    'elegant': {'left_arm': (.25, .12, -1), 'right_arm': (-.2, .08, -1),
                'left_elbow': (-.35, .5, -.7), 'right_elbow': (.35, .4, -.8)},
    'arms_clear': {'left_arm': (.6, .02, -.8), 'right_arm': (-.6, .02, -.8),
                   'left_elbow': (.3, .06, -1), 'right_elbow': (-.3, .06, -1)},
    'sit_chair': {'left_arm': (.12, .18, -1), 'right_arm': (-.12, .18, -1),
                  'left_elbow': (-.12, .8, -.35), 'right_elbow': (.12, .8, -.35)},
    'sit_side': {'left_arm': (.15, .2, -1), 'right_arm': (-.15, .2, -1),
                 'left_elbow': (-.2, .8, -.2), 'right_elbow': (.2, .8, -.2)},
}

SEATED_POSES = {'sit_chair', 'sit_side'}
POSE_LEGS = {
    'sit_chair': {'left_leg': (.04, 1, -.04), 'right_leg': (-.04, 1, -.04),
                  'left_knee': (0, .08, -1), 'right_knee': (0, .08, -1)},
    'sit_side': {'left_leg': (.25, 1, -.06), 'right_leg': (.22, 1, -.06),
                 'left_knee': (.18, .06, -1), 'right_knee': (.18, .06, -1)},
}

# Mild character-space rotations: pitch, yaw, roll. Legs/foot IK are untouched.
# Opposing chest/hip angles add a silhouette; existing foot IK is not edited.
POSE_BODY = {
    'hand_hip': {'upper_body': (0, -5, -4), 'lower_body': (0, 2, 2)},
    'wave': {'upper_body': (0, 3, 3)},
    'peace_face': {'upper_body': (2, -4, -4), 'upper_body2': (0, 2, 1)},
    'thinking': {'upper_body': (3, -6, 2)},
    'shy': {'upper_body': (5, 0, 3)},
    'cheer': {'upper_body': (-3, 0, -2)},
    'cat_paws': {'upper_body': (4, 0, -4)},
    'salute': {'upper_body': (-1, -5, 2)},
    'hands_behind': {'upper_body': (4, 0, -3)},
    'elegant': {'upper_body': (0, -9, -3), 'lower_body': (0, 4, 2)},
    'presenting': {'upper_body': (0, -5, 2)},
    'sit_chair': {'upper_body': (3, 0, 0)},
    'sit_side': {'upper_body': (2, -8, 3)},
}
POSE_HEAD = {'peace_face': (0, 0, -9), 'thinking': (3, -6, 6), 'shy': (7, 0, 7),
             'cheer': (-4, 0, -4), 'cat_paws': (2, 0, -8), 'salute': (-2, 0, 3),
             'hands_behind': (2, 0, 5), 'elegant': (0, 7, -5)}
POSE_HANDS = {'peace_face': {'right': 'PEACE', 'left': 'RELAXED'},
              'thinking': {'right': 'POINT', 'left': 'RELAXED'},
              'shy': {'left': 'RELAXED', 'right': 'RELAXED'},
              'cheer': {'left': 'FIST', 'right': 'FIST'},
              'cat_paws': {'left': 'FIST', 'right': 'FIST'},
              'salute': {'right': 'OPEN', 'left': 'RELAXED'}}

EXPRESSION_LABELS = {'neutral': '中性', 'smile': '微笑', 'happy': '开心', 'blink': '闭眼',
                     'wink': '眨单眼', 'angry': '生气', 'sad': '悲伤', 'surprised': '惊讶'}
POSE_LABELS = {'relaxed': '自然站姿', 'hand_hip': '单手叉腰', 'wave': '招手', 'open_arms': '张开双臂',
              'hands_front': '双手身前', 'presenting': '单手展示', 'peace_face': '脸侧剪刀手',
              'thinking': '托腮思考', 'shy': '害羞收手', 'cheer': '元气举手', 'cat_paws': '猫爪手势',
              'salute': '俏皮敬礼', 'hands_behind': '背手前倾', 'elegant': '侧身立绘', 'arms_clear': '服装展示',
              'sit_chair': '端坐', 'sit_side': '侧坐'}
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
    return {'schema_version': 1, 'expressions': {}, 'poses': {}, 'cameras': {}, 'bone_map': {}, 'pose_options': {}, 'shot_overrides': {}}


def validate_library(value):
    if not isinstance(value, dict) or value.get('schema_version', 1) != 1:
        raise ValueError('不支持的预设文件版本。')
    result = empty_library()
    for category in ('expressions', 'poses', 'cameras', 'bone_map', 'pose_options', 'shot_overrides'):
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
    for name, options in result['pose_options'].items():
        if name not in result['poses'] or not isinstance(options, dict) or set(options) != {'leg_mode'}:
            raise ValueError('姿势选项必须引用已有姿势并指定腿部控制。')
        if not isinstance(options['leg_mode'], str) or options['leg_mode'] not in {'KEEP', 'FK'}:
            raise ValueError('腿部控制必须保留模型 IK 或使用 FK。')
    for slot, override in result['shot_overrides'].items():
        if not any(re.fullmatch(re.escape(group)+r':[1-9]', slot) for group in GROUPS):
            raise ValueError('镜头位置必须为有效分组和 1–9 序号。')
        if not isinstance(override, dict) or set(override)-{'pose', 'expression', 'include_hands', 'title', 'support', 'azimuth'}:
            raise ValueError('镜头替换包含不支持的参数。')
        if not isinstance(override.get('pose'), str) or override['pose'] not in set(POSE_LABELS) | set(result['poses']):
            raise ValueError('镜头引用了不存在的姿势预设。')
        if 'expression' in override and (not isinstance(override['expression'], str) or override['expression'] not in set(EXPRESSION_LABELS) | set(result['expressions'])):
            raise ValueError('镜头引用了不存在的表情预设。')
        if 'include_hands' in override and (not isinstance(override['include_hands'], list) or
                                          any(not isinstance(name, str) or name not in {'left_wrist', 'right_wrist'} for name in override['include_hands'])):
            raise ValueError('手势构图只能包含左 / 右手首。')
        if 'title' in override and (not isinstance(override['title'], str) or not 1 <= len(override['title']) <= 80):
            raise ValueError('镜头名称须为 1–80 字符。')
        if not isinstance(override.get('support', 'floor'), str) or override.get('support', 'floor') not in {'floor', 'seat'}:
            raise ValueError('镜头支撑物无效。')
        if 'azimuth' in override and (not isinstance(override['azimuth'], (int, float)) or not -180 <= override['azimuth'] <= 180):
            raise ValueError('镜头水平角须为 -180 到 180 度。')
    return result


def safe_name(value):
    value = re.sub(r'[^\w-]+', '_', value, flags=re.UNICODE).strip('_')[:80]
    return value or 'character'


# Each row is a designed photo, not a Cartesian product of presets.
# title, pose, expression, azimuth, elevation, head pitch/yaw/roll, focus, hands.
SHOT_PLANS = {
    'portrait': (
        ('正面识别照', 'relaxed', 'neutral', 0, 0, (0, 0, 0), 'HEAD', ()),
        ('微笑侧颜', 'elegant', 'smile', -20, 2, (0, -8, -4), 'HEAD', ()),
        ('脸侧比耶', 'peace_face', 'wink', 18, 3, (0, 5, -8), 'HEAD', ('right_wrist',)),
        ('开心闭眼', 'hands_front', 'happy', -12, 4, (0, -4, 4), 'FACE', ()),
        ('害羞低头', 'shy', 'smile', 12, 6, (6, 2, 6), 'HEAD', ()),
        ('思考侧脸', 'thinking', 'neutral', 30, 0, (2, 10, 5), 'HEAD', ('right_wrist',)),
        ('惊讶反应', 'relaxed', 'surprised', -28, 1, (-2, -8, 0), 'FACE', ()),
        ('认真神情', 'hand_hip', 'angry', 24, -2, (-2, 8, -3), 'FACE', ()),
        ('安静侧颜', 'elegant', 'sad', -38, 2, (3, -12, 2), 'HEAD', ()),
    ),
    'half_body': (
        ('自然半身', 'relaxed', 'neutral', 0, 0, (0, 0, 0), 'UPPER', ()),
        ('俏皮招手', 'wave', 'smile', -18, 2, (0, -5, 4), 'UPPER', ('right_wrist',)),
        ('叉腰自信', 'hand_hip', 'smile', 25, 0, (0, 8, -4), 'UPPER', ('left_wrist',)),
        ('剪刀手半身', 'peace_face', 'wink', 15, 3, (0, 4, -8), 'UPPER', ('right_wrist',)),
        ('双手身前', 'hands_front', 'smile', -12, 3, (2, -4, 3), 'UPPER', ('left_wrist', 'right_wrist')),
        ('猫爪卖萌', 'cat_paws', 'happy', 8, 4, (2, 2, -7), 'UPPER', ('left_wrist', 'right_wrist')),
        ('俏皮敬礼', 'salute', 'smile', -25, 0, (-2, -8, 3), 'UPPER', ('right_wrist',)),
        ('单手介绍', 'presenting', 'neutral', 32, 0, (0, 10, 0), 'UPPER', ('right_wrist',)),
        ('侧坐半身', 'sit_side', 'smile', -32, 4, (2, -10, 5), 'UPPER', ()),
    ),
    'full_body': (
        ('正面立绘', 'relaxed', 'neutral', 0, 0, (0, 0, 0), 'FULL', ()),
        ('端坐全身', 'sit_chair', 'smile', -25, 3, (0, -8, -3), 'FULL', ()),
        ('叉腰立绘', 'hand_hip', 'smile', 30, -2, (0, 10, -4), 'FULL', ()),
        ('侧坐全身', 'sit_side', 'neutral', 35, 2, (0, 10, -4), 'FULL', ()),
        ('元气举手', 'cheer', 'happy', -15, -2, (-4, -4, -4), 'FULL', ()),
        ('张臂立绘', 'open_arms', 'smile', 22, 0, (0, 7, 3), 'FULL', ()),
        ('背手立绘', 'hands_behind', 'smile', -40, 1, (2, -12, 5), 'FULL', ()),
        ('侧面轮廓', 'arms_clear', 'neutral', 90, 0, (0, 0, 0), 'FULL', ()),
        ('背面服饰', 'arms_clear', 'neutral', 180, 0, (0, 0, 0), 'FULL', ()),
    ),
    'outfit_detail': (
        ('领口与颈饰', 'arms_clear', 'neutral', 0, 3, (0, 0, 0), 'COLLAR', ()),
        ('胸前与肩饰', 'arms_clear', 'neutral', -25, 0, (0, 0, 0), 'CHEST', ()),
        ('左袖与手套', 'arms_clear', 'neutral', 38, 0, (0, 0, 0), 'SLEEVE_LEFT', ()),
        ('右袖与手套', 'arms_clear', 'neutral', -38, 0, (0, 0, 0), 'SLEEVE_RIGHT', ()),
        ('腰带与腰饰', 'arms_clear', 'neutral', 15, 2, (0, 0, 0), 'WAIST', ()),
        ('下装与侧饰', 'arms_clear', 'neutral', -45, 0, (0, 0, 0), 'HIPS', ()),
        ('下摆与腿部', 'arms_clear', 'neutral', 0, -2, (0, 0, 0), 'HEM', ()),
        ('背部上装', 'arms_clear', 'neutral', 180, 0, (0, 0, 0), 'BACK_UPPER', ()),
        ('鞋靴与足饰', 'arms_clear', 'neutral', 25, 4, (0, 0, 0), 'SHOES', ()),
    ),
}


def recipe():
    shots = []
    for group in GROUPS:
        for index, row in enumerate(SHOT_PLANS[group], 1):
            title, pose, expression, azimuth, elevation, head, focus, hands = row
            shots.append(dict(group=group, index=index, title=title, pose=pose, expression=expression,
                              support='seat' if pose in {'sit_chair', 'sit_side'} else 'floor',
                              azimuth=azimuth, elevation=elevation, focus=focus, include_hands=list(hands),
                              performance={'head_angles': list(head), 'hand_pose': 'AUTO', 'pose_strength': 1,
                                           'expression_strength': .85 if expression != 'neutral' else 1},
                              camera={'camera_projection': 'PERSP', 'camera_lens': 85 if group == 'portrait' else 70,
                                      'camera_aspect': 'PORTRAIT', 'camera_azimuth': 0, 'camera_elevation': 0,
                                      'camera_roll': 0, 'camera_zoom': 1, 'camera_shift_x': 0, 'camera_shift_y': 0,
                                      'camera_target_offset': [0, 0, 0], 'margin': 1.12}))
    return shots


def current_recipe(settings):
    """Keep the 9+9+9+9 shot targets, with the user's own performance/lens."""
    return [dict({key: value for key, value in shot.items() if key not in {'camera', 'performance', 'include_hands', 'elevation'}},
                 pose=settings.pose, expression=settings.expression,
                 support='seat' if settings.pose in {'sit_chair', 'sit_side'} else 'floor') for shot in recipe()]


def dataset_coverage(shots):
    """Planned coverage, not a claim that pixels/occlusion have been verified."""
    return {'total': len(shots), 'per_group': {group: sum(s['group'] == group for s in shots) for group in GROUPS},
            'poses': sorted({s['pose'] for s in shots}), 'expressions': sorted({s['expression'] for s in shots}),
            'seated_photos': sum(s['pose'] in SEATED_POSES for s in shots),
            'full_body_angles': [s['azimuth'] for s in shots if s['group'] == 'full_body'],
            'clothing_regions': [s.get('focus', '') for s in shots if s['group'] == 'outfit_detail'],
            'scope': 'planned; review actual occlusion, deformation and captions before training'}


def apply_shot_overrides(shots, library):
    result = []
    for original in shots:
        override = library['shot_overrides'].get(f'{original["group"]}:{original["index"]}')
        shot = dict(original)
        if override:
            shot.update(override)
            if shot['pose'] in library['poses']:
                shot['performance'] = dict(shot.get('performance', {}), head_angles=[0, 0, 0], hand_pose='KEEP', pose_strength=1)
            if shot.get('expression') in library['expressions']:
                shot['performance'] = dict(shot.get('performance', {}), expression_strength=1)
            shot['pose_overridden'] = True
        result.append(shot)
    return result


class ShotSettings:
    """Read-only settings overlay; per-photo values never alter the UI settings."""
    def __init__(self, settings, shot):
        self._settings = settings
        self._overrides = {**shot.get('performance', {}), **shot.get('camera', {})}

    def __getattr__(self, name):
        return self._overrides[name] if name in self._overrides else getattr(self._settings, name)


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
