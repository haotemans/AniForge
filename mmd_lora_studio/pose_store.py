"""Local per-model pose results, independent of any Blender project file."""
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile


def slot_filename(slot):
    if not isinstance(slot, str) or slot != 'neutral' and not re.fullmatch(r'(?:portrait|half_body):(?:[5-9]|1[0-2])', slot):
        raise ValueError('无效的动作位置。')
    return slot.replace(':', '_')+'.json'


def model_directory(root, key):
    if not re.fullmatch(r'[0-9a-f]{24}', key):
        raise ValueError('无效的模型识别码。')
    return Path(root)/key


def validate_result(value, key, slot=None):
    if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('rig') != key:
        raise ValueError('动作文件版本或模型不匹配。')
    asset, pose = value.get('asset'), value.get('pose')
    if not isinstance(asset, dict) or asset.get('rig') != key or asset.get('provider') != 'AnimCharTools':
        raise ValueError('动作来源记录无效。')
    slot_filename(asset.get('slot', ''))
    if slot is not None and asset['slot'] != slot:
        raise ValueError('动作文件的位置不匹配。')
    if not isinstance(asset.get('pose'), str) or not asset['pose'] or not isinstance(asset.get('reviewed'), bool):
        raise ValueError('动作名称或验收状态无效。')
    if not isinstance(asset.get('reference'), str) or not isinstance(asset.get('review_note', ''), str):
        raise ValueError('动作说明无效。')
    if not isinstance(pose, dict) or not pose or len(pose) > 4096:
        raise ValueError('动作文件没有骨骼数据。')
    for name, matrix in pose.items():
        if not isinstance(name, str) or len(name) > 256 or not isinstance(matrix, list) or len(matrix) != 4:
            raise ValueError('动作骨骼矩阵无效。')
        if any(not isinstance(row, list) or len(row) != 4 or any(
                not isinstance(n, (int, float)) or not math.isfinite(n) for n in row) for row in matrix):
            raise ValueError('动作骨骼矩阵无效。')
    if value.get('pose_options') not in ({'leg_mode': 'FK'}, {'leg_mode': 'KEEP'}):
        raise ValueError('动作控制选项无效。')
    return value


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.'+path.name, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def save_result(root, key, asset, pose, options, model_name=''):
    """Write one slot atomically, keeping its previous valid saved version."""
    document = dict(schema_version=1, rig=key, model_name=model_name,
                    asset=copy.deepcopy(asset), pose=copy.deepcopy(pose), pose_options=copy.deepcopy(options))
    validate_result(document, key)
    path = model_directory(root, key)/slot_filename(asset['slot'])
    data = json.dumps(document, ensure_ascii=False, indent=2).encode('utf8')
    if path.is_file():
        previous = path.read_bytes()
        if previous == data:
            return path
        try:
            validate_result(json.loads(previous), key, asset['slot'])
        except (ValueError, TypeError):
            pass  # Do not replace a good backup with a damaged file.
        else:
            _atomic_write(path.with_suffix('.json.bak'), previous)
    _atomic_write(path, data)
    return path


def load_results(root, key):
    results, issues = {}, []
    directory = model_directory(root, key)
    if not directory.is_dir():
        return results, issues
    for path in sorted(directory.glob('*.json')):
        try:
            if path.stat().st_size > 8*1024*1024:
                raise ValueError('动作文件过大。')
            document = validate_result(json.loads(path.read_text(encoding='utf8')), key)
            if slot_filename(document['asset']['slot']) != path.name:
                raise ValueError('动作文件名称不匹配。')
        except (OSError, ValueError, TypeError) as exc:
            try:
                document = validate_result(json.loads(path.with_suffix('.json.bak').read_text(encoding='utf8')), key)
                if slot_filename(document['asset']['slot']) != path.name:
                    raise ValueError('备份位置不匹配。')
            except (OSError, ValueError, TypeError):
                issues.append(path.name+'：'+str(exc))
                continue
            issues.append(path.name+'：已使用上一份有效备份。')
        results[document['asset']['slot']] = document
    return results, issues
