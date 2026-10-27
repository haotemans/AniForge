"""Dataset recipes. No Blender dependency so planning can be tested separately."""
import json
import re
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
POSES = ('relaxed', 'hand_hip', 'wave', 'open_arms', 'relaxed', 'hand_hip', 'wave', 'open_arms', 'relaxed')
MORPHS = {
    'smile': [('笑い', '笑', 'smile', 'Smile', 'にこり')],
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
}


def safe_name(value):
    value = re.sub(r'[^\w-]+', '_', value, flags=re.UNICODE).strip('_')[:80]
    return value or 'character'


def recipe():
    return [dict(group=g, index=i + 1, azimuth=angle,
                 expression=EXPRESSIONS[i], pose=POSES[i])
            for g in GROUPS for i, angle in enumerate(ANGLES)]


def three_view_recipe(group='portrait'):
    if group not in GROUPS:
        raise ValueError('Unknown framing group: ' + group)
    return [dict(group=group, index=i+1, azimuth=angle, expression='neutral', pose='relaxed', view=view)
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
