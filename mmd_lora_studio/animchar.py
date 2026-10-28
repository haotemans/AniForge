"""Call the user's installed AnimChar Tools. No bundled vendor code or poses."""
import hashlib
import importlib
import json
import functools
import threading
import pickle
from pathlib import Path

import bpy

from .core import require_shot_asset
from . import pose_store
from .performance import aliases, find_bone, finger_bones, bone_side, custom_presets, store_presets

_JOB = None
_EDITOR = None
_CAPTURE_REPORTS = {}
_RESTORE_STAMPS = {}


def rig_key(rig):
    if not rig:
        return ''
    # Object/data-block names and collection order change on reimport. Rest
    # axes and parenting identify compatible skeletons across project files.
    data = sorted([(b.name, b.parent.name if b.parent else '',
                    [round(v, 6) for row in b.matrix_local for v in row]) for b in rig.data.bones], key=lambda item: item[0])
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()[:24]


def legacy_rig_key(rig):
    data = [(b.name, b.parent.name if b.parent else '',
             [round(v, 6) for row in b.matrix_local for v in row]) for b in rig.data.bones]
    data.append(rig.data.name)
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()[:24]


def result_directory():
    configured = bpy.utils.user_resource('CONFIG')
    base = Path(configured) if configured else Path(bpy.utils.resource_path('USER'))/'config'
    if not base.is_absolute():
        raise ValueError('找不到 Blender 用户配置目录，未将动作写入工程目录。')
    return base/'aniforge'/'pose_results'


def _migrate_model_keys(library, key, old_key):
    migrated = []
    for identifier, asset in list(library['shot_assets'].items()):
        if asset['rig'] != old_key or old_key == key:
            continue
        replacement = dict(asset, rig=key)
        library['shot_assets'].setdefault(key+'|'+asset['slot'], replacement)
        del library['shot_assets'][identifier]
        migrated.append(asset['slot'])
    return migrated


def persist_results(settings, library=None, slots=None):
    if not settings.armature:
        return 0
    library = library if library is not None else custom_presets(settings)
    key = rig_key(settings.armature)
    _migrate_model_keys(library, key, legacy_rig_key(settings.armature))
    saved = 0
    for asset in library['shot_assets'].values():
        if asset['rig'] != key or slots is not None and asset['slot'] not in slots:
            continue
        pose_store.save_result(result_directory(), key, asset, library['poses'][asset['pose']],
                               library['pose_options'].get(asset['pose'], {'leg_mode': 'FK'}), settings.armature.data.name)
        saved += 1
    return saved


def _restore_stamp(settings, key):
    directory = pose_store.model_directory(result_directory(), key)
    files = []
    for path in sorted(directory.glob('*.json*')):
        if path.name.startswith('.'):
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        files.append((path.name, info.st_mtime_ns, info.st_size))
    return key, hash(settings.presets_json), tuple(files)


def restore_results(settings, force=True):
    """Load compatible saved slots without replacing this project's edits."""
    rig = settings.armature
    if not rig:
        return dict(restored=0, migrated=0, total=0, issues=[])
    key = rig_key(rig)
    identity = settings.as_pointer()
    stamp = _restore_stamp(settings, key)
    if not force and _RESTORE_STAMPS.get(identity) == stamp:
        return None
    library = custom_presets(settings)
    migrated = _migrate_model_keys(library, key, legacy_rig_key(rig))
    # Seed old scene-only results once; never overwrite a newer disk result
    # merely because an older .blend was opened.
    for slot in migrated:
        target = pose_store.model_directory(result_directory(), key)/pose_store.slot_filename(slot)
        if not target.exists():
            persist_results(settings, library, [slot])
    results, issues = pose_store.load_results(result_directory(), key)
    restored = 0
    for slot, document in results.items():
        identifier = key+'|'+slot
        if identifier in library['shot_assets']:
            continue
        asset = dict(document['asset'])
        name = asset['pose']
        if name in library['poses'] and library['poses'][name] != document['pose']:
            base = name+' [本地库]'; suffix = 1; name = base
            while name in library['poses']:
                suffix += 1; name = base+str(suffix)
            asset['pose'] = name
        library['poses'][name] = document['pose']
        library['pose_options'][name] = document['pose_options']
        library['shot_assets'][identifier] = asset
        restored += 1
    if migrated or restored:
        store_presets(settings, library)
        settings.animchar_status = f'已从本地动作库恢复 {restored} 份，迁移旧结果 {len(migrated)} 份；无需重新生成。'
    if issues:
        settings.animchar_status = '本地动作库：'+issues[0]
    _RESTORE_STAMPS[identity] = _restore_stamp(settings, key)
    return dict(restored=restored, migrated=len(migrated), total=sum(a['rig'] == key for a in library['shot_assets'].values()), issues=issues)


def restore_scene_results(settings):
    try:
        adopt_single_armature(settings)
        return restore_results(settings)
    except Exception as exc:
        settings.animchar_status = '本地动作读取失败：'+str(exc)
        return None


def adopt_single_armature(settings):
    if settings.armature:
        return
    owner = getattr(settings, 'id_data', None)
    candidates = [o for o in getattr(owner, 'objects', ()) if o.type == 'ARMATURE' and not o.name.startswith('AniForge')]
    if len(candidates) == 1:
        settings.armature = candidates[0]


def restore_tick():
    for scene in getattr(bpy.data, 'scenes', ()):
        try:
            settings = scene.ml_settings
            if settings.running or busy() or editing():
                continue
            adopt_single_armature(settings)
            restore_results(settings, force=False)
        except Exception as exc:
            scene.ml_settings.animchar_status = '本地动作读取失败：'+str(exc)
    return 2.0


def modules():
    if 'AnimCharTools' not in bpy.context.preferences.addons or not hasattr(bpy.context.scene, 'animchartools_props'):
        raise ValueError('请先安装并启用 AnimChar Tools；AniForge 不内置第三方插件。')
    return (importlib.import_module('AnimCharTools.addons.AnimCharTools.AddonOperators'),
            importlib.import_module('AnimCharTools.addons.AnimCharTools.bone_mapping'))


def available():
    return 'AnimCharTools' in bpy.context.preferences.addons and hasattr(bpy.context.scene, 'animchartools_props')


def mapping(settings):
    rig = settings.armature
    if not rig:
        raise ValueError('请先指定角色骨架。')
    result = {}
    roles = {'Hips': 'lower_body', 'Spine': 'upper_body', 'Spine1': 'upper_body2', 'Head': 'head'}
    for side, title in (('left', 'Left'), ('right', 'Right')):
        roles.update({title+suffix: side+'_'+part for suffix, part in (
            ('Shoulder', 'shoulder'), ('Arm', 'arm'), ('ForeArm', 'elbow'), ('Hand', 'wrist'),
            ('UpLeg', 'leg'), ('Leg', 'knee'), ('Foot', 'ankle'))})
    for canonical, role in roles.items():
        b = find_bone(rig, role, settings)
        if b:
            result[canonical] = b.name
    for canonical, names in {'Neck': ('首', 'neck'), 'Spine2': ('上半身3', '上半身３', 'spine2', 'upperchest'),
                             'LeftToe': ('左つま先', '左足先EX', 'toe.l'), 'RightToe': ('右つま先', '右足先EX', 'toe.r')}.items():
        b = next((b for b in rig.pose.bones if any(n.casefold() in {a.casefold() for a in aliases(b)} for n in names)), None)
        if b and b.name not in result.values():
            result[canonical] = b.name
    fingers = list(finger_bones(rig))
    for side, title in (('left', 'Left'), ('right', 'Right')):
        for kind in ('thumb', 'index', 'middle', 'ring', 'pinky'):
            chain = sorted([(i, b) for b, k, i in fingers if k == kind and bone_side(b) == side], key=lambda v: v[0])
            for i, (_, b) in enumerate(chain[:3], 1):
                result[f'{title}Hand{kind.title()}{i}'] = b.name
    return result


def configure(settings, target=None, canonical=False):
    _, native_mapping = modules()
    props = bpy.context.scene.animchartools_props
    props.apply_to_armature = target or settings.armature
    native_mapping.ensure_bone_mapping(props)
    values = ({key: 'mixamorig:'+key for key in native_mapping.MIXAMO_BONES
               if 'mixamorig:'+key in target.pose.bones} if canonical else mapping(settings))
    for item in props.bone_mapping:
        item.target = values.get(item.name.split(':')[-1], '')
    props.bone_mapping_upper_body_mode = props.bone_mapping_lower_body_mode = 'FK'
    valid, errors = native_mapping.validate_bone_mapping(props)
    if not valid:
        raise ValueError('AnimChar 骨骼映射未完成：'+'; '.join(errors))
    if hasattr(bpy.context.scene, 'pose_editor_props'):
        editor = bpy.context.scene.pose_editor_props
        editor.edit_armature = target or settings.armature
        for canonical, name in values.items():
            if hasattr(editor, 'map_'+canonical):
                setattr(editor, 'map_'+canonical, name)
    return values


def bind(settings, shot, rig=None, reference='Pose Editor'):
    rig = rig or settings.armature
    key = rig_key(settings.armature)
    slot = shot.get('asset_slot')
    if not slot:
        raise ValueError('当前镜头没有 AnimChar 动作位置。')
    library = custom_presets(settings)
    name = f'AnimChar {key[:8]} {slot}'
    managed = set(mapping(settings).values())
    mapped = set(managed)
    for mapped_name in list(mapped):
        parent = settings.armature.data.bones[mapped_name].parent
        while parent:
            managed.add(parent.name)
            parent = parent.parent
    # Only mapped body/finger bones are recorded; hair/cloth/accessory controls
    # retain the character's authored state. Pose Editor result is not reposed.
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = rig.evaluated_get(graph)
    destination = settings.armature
    to_destination = destination.matrix_world.inverted() @ rig.matrix_world
    pose = {}
    for bone_name in managed:
        b = rig.pose.bones.get(bone_name)
        if b:
            eb = evaluated.pose.bones[bone_name]
            target = destination.data.bones[bone_name]
            kwargs = {}
            if target.parent:
                kwargs = dict(parent_matrix=to_destination @ evaluated.pose.bones[target.parent.name].matrix,
                              parent_matrix_local=target.parent.matrix_local)
            matrix = target.convert_local_to_pose(to_destination @ eb.matrix, target.matrix_local, invert=True, **kwargs)
            pose[bone_name] = [list(row) for row in matrix]
    if not pose:
        raise ValueError('AnimChar 结果没有可保存的映射骨骼。')
    library['poses'][name] = pose
    library['pose_options'][name] = {'leg_mode': 'FK'}
    library['shot_assets'][f'{key}|{slot}'] = dict(pose=name, rig=key, slot=slot, provider='AnimCharTools', reference=reference, reviewed=False)
    replaced = [f'{group}:{i}' for group in ('portrait', 'half_body') for i in range(1, 5)] if slot == 'neutral' else [slot]
    for position in replaced:
        if position in library['shot_overrides']:
            library['legacy_shot_overrides'][position] = library['shot_overrides'].pop(position)
    persist_results(settings, library, [slot])
    store_presets(settings, library)
    _CAPTURE_REPORTS[name] = {'expected_joints': {n: [list(rig.matrix_world @ evaluated.pose.bones[n].head),
                                                    list(rig.matrix_world @ evaluated.pose.bones[n].tail)] for n in mapped},
                              'mapped_bones': len(mapped), 'ancestor_bones': len(managed-mapped)}
    return name


def capture_report(name):
    return _CAPTURE_REPORTS.get(name, {})


def mark_reviewed(settings, shot, value=True):
    library = custom_presets(settings)
    asset = library['shot_assets'].get(f'{rig_key(settings.armature)}|{shot.get("asset_slot")}')
    if not asset:
        raise ValueError('请先完成当前模型的 AnimChar 动作。')
    asset['reviewed'] = value
    if value:
        asset.pop('review_note', None)
    persist_results(settings, library, [asset['slot']])
    store_presets(settings, library)


def mark_needs_fix(settings, shot, note):
    library = custom_presets(settings)
    asset = library['shot_assets'].get(f'{rig_key(settings.armature)}|{shot.get("asset_slot")}')
    if not asset:
        raise ValueError('请先完成当前模型的 AnimChar 动作。')
    asset['reviewed'] = False
    asset['review_note'] = note.strip()[:256] or '手型、穿模或构图需要修正。'
    persist_results(settings, library, [asset['slot']])
    store_presets(settings, library)


def busy():
    return _JOB is not None


def editing():
    return _EDITOR is not None


def has_action_channels(rig):
    action = rig.animation_data.action if rig.animation_data else None
    if not action:
        return False
    if hasattr(action, 'fcurves'):
        return bool(action.fcurves)
    return any(bag.fcurves for layer in action.layers for strip in layer.strips
               for bag in getattr(strip, 'channelbags', ()))


def start_editor(context, settings, shot):
    global _EDITOR
    native, _ = modules()
    if busy() or editing():
        raise ValueError('请先完成当前 AnimChar 任务。')
    existing = getattr(context.scene.pose_editor_props, 'edit_armature', None)
    if existing and any(b.name.startswith('POSE_EDITOR_BONE_') for b in existing.data.bones):
        raise ValueError('请先在 AnimChar 完成已有的姿势编辑。')
    from . import model_meshes
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    original = settings.armature
    before_objects = set(bpy.data.objects)
    proxy = native.import_gltf(native.default_character_path, remove_glTF_not_exported=True)
    native.init_armature_animation(proxy)
    proxy.name = 'AniForge AnimChar Editor'; proxy.show_in_front = True
    copies = [o for o in bpy.data.objects if o not in before_objects]
    for obj in copies:
        obj.hide_render = True
    hidden = []
    for obj in [original]+model_meshes(settings):
        hidden.append((obj, obj.hide_get()))
        obj.hide_set(True)
    _EDITOR = dict(proxy=proxy, copies=copies, hidden=hidden, shot=dict(shot), helper_preexisting=bpy.data.objects.get('POSE_EDITOR_Sphere') in before_objects)
    try:
        configure(settings, proxy, canonical=True)
        cache = _editor_cache(settings, shot)
        if cache.is_file():
            from mathutils import Matrix
            values = json.loads(cache.read_text(encoding='utf8'))
            for name, matrix in values.items():
                if name in proxy.pose.bones:
                    proxy.pose.bones[name].matrix_basis = Matrix(matrix)
        else:
            source_file = cached_source(settings, shot)
            if source_file:
                native.apply_result(str(source_file), context.scene.frame_current, proxy)
            elif shot.get('asset_slot') == 'neutral':
                editor = importlib.import_module('AnimCharTools.addons.PoseEditor.AddonOperators')
                editor.load_pose(context, 'body', str(Path(native.addon_assets_path)/'preset_poses/body_stand.pkl'))
        proxy.hide_set(False)
        if bpy.ops.poseeditor.create_controls() != {'FINISHED'}:
            raise ValueError('AnimChar Pose Editor 未创建控制器。')
        settings.animchar_status = '使用 AnimChar 标准角色编辑；完成后由 AnimChar 适配当前 MMD。'
    except Exception:
        _close_editor(context, settings)
        raise


def _close_editor(context, settings, keep=False):
    global _EDITOR
    state = _EDITOR
    if not state:
        return
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in reversed(state['copies']):
        if keep:
            obj.hide_set(True)
            continue
        if obj.name in bpy.data.objects:
            data = obj.data; kind = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            if kind == 'ARMATURE' and data.users == 0:
                bpy.data.armatures.remove(data)
            elif kind == 'MESH' and data.users == 0:
                bpy.data.meshes.remove(data)
    for obj, hidden in state['hidden']:
        obj.hide_set(hidden)
    helper = bpy.data.objects.get('POSE_EDITOR_Sphere')
    if not keep and helper and not state['helper_preexisting']:
        bpy.data.objects.remove(helper, do_unlink=True)
    context.view_layer.objects.active = settings.armature
    settings.armature.select_set(True)
    _EDITOR = None
    configure(settings)


def finish_editor(context, settings):
    if not _EDITOR:
        raise ValueError('当前没有 AniForge 创建的 AnimChar 编辑副本。')
    if bpy.ops.poseeditor.write_keyframe_and_remove_controls() != {'FINISHED'}:
        raise ValueError('请先在 AnimChar 完成当前编辑。')
    state = _EDITOR
    cache = _editor_cache(settings, state['shot']); cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({b.name: [list(row) for row in b.matrix_basis] for b in state['proxy'].pose.bones}), encoding='utf8')
    _close_editor(context, settings, keep=True)
    start_capture(context, settings, state['shot'], 'AnimChar Pose Editor', source_rig=state['proxy'], source_objects=state['copies'])


def _editor_cache(settings, shot):
    prefs = importlib.import_module('AnimCharTools.addons.AnimCharTools.AddonPreferences')
    return Path(prefs.get_save_folder())/'aniforge_editor'/rig_key(settings.armature)/(shot['asset_slot'].replace(':', '_')+'.json')


def cached_source(settings, shot, text_prompt=None):
    prefs = importlib.import_module('AnimCharTools.addons.AnimCharTools.AddonPreferences')
    root = Path(prefs.get_save_folder())
    from .workflow import reference_path, PORTRAIT_PROMPTS
    if shot.get('asset_slot') == 'neutral':
        return None
    if shot['group'] == 'portrait':
        if text_prompt is None:
            asset = custom_presets(settings)['shot_assets'].get(f'{rig_key(settings.armature)}|{shot.get("asset_slot")}')
            saved = asset['reference'] if asset else ''
            text_prompt = saved.removeprefix('AnimChar text: ') if saved.startswith('AnimChar text: ') else settings.animchar_prompt or PORTRAIT_PROMPTS.get(shot['index'], '')
        token = text_prompt.encode()
        inputs = (root/'motion-generation').glob('*.pkl')
        matches = [p.with_suffix('.glb') for p in inputs if token and token in p.read_bytes() and p.with_suffix('.glb').is_file()]
    else:
        path = reference_path(settings, shot)
        asset = custom_presets(settings)['shot_assets'].get(f'{rig_key(settings.armature)}|{shot.get("asset_slot")}')
        if not settings.animchar_reference and asset and Path(asset['reference']).is_file():
            path = Path(asset['reference'])
        if not path.is_file():
            return None
        digest = hashlib.sha256(path.read_bytes()).digest()
        inputs = list((root/'pose-capture').glob('*'+path.suffix))+list((root/'pose-capture-general').glob('*'+path.suffix))
        matches = [p.with_suffix('.glb') for p in inputs if p.with_suffix('.glb').is_file() and hashlib.sha256(p.read_bytes()).digest() == digest]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def _finish_job(error=''):
    global _JOB
    job = _JOB
    if not job:
        return
    scene, settings = job['scene'], job['settings']
    try:
        if not error:
            scene.frame_set(job['frame'])
            bind(settings, job['shot'], job['proxy'], job['reference'])
            settings.animchar_status = 'AnimChar 动作已保存；请预览手部与构图后验收。'
        else:
            settings.animchar_status = error
    except Exception as exc:
        settings.animchar_status = 'AnimChar 保存失败：'+str(exc)
    finally:
        props = scene.animchartools_props
        props.apply_to_armature = job['previous_target']
        props.import_input = job['previous_import']
        props.use_pose_optimization = job['previous_optimization']
        props.frame_start = job['previous_start']
        for item in props.bone_mapping:
            item.target = job['previous_mapping'].get(item.name, '')
        if hasattr(scene, 'pose_editor_props'):
            scene.pose_editor_props.edit_armature = job['previous_editor']
        for obj in job['temporary']:
            if obj.name in bpy.data.objects:
                data, kind = obj.data, obj.type
                bpy.data.objects.remove(obj, do_unlink=True)
                if data and data.users == 0 and kind == 'ARMATURE':
                    bpy.data.armatures.remove(data)
                elif data and data.users == 0 and kind == 'MESH':
                    bpy.data.meshes.remove(data)
        scene.frame_set(job['previous_frame'])
        rig = settings.armature
        if not job['had_animation_data']:
            rig.animation_data_clear()
        elif rig.animation_data:
            rig.animation_data.action = job['previous_action']
        _JOB = None


def start_capture(context, settings, shot, reference, text_prompt='', source_rig=None, source_objects=()):
    """Native image capture + native retarget, isolated on a skeleton copy."""
    global _JOB
    if not source_rig and not settings.animchar_rebuild and not busy() and not editing():
        restore_results(settings)
        saved = custom_presets(settings)['shot_assets'].get(f'{rig_key(settings.armature)}|{shot.get("asset_slot")}')
        props = getattr(context.scene, 'animchartools_props', None)
        alternate = bool(props and (props.use_general_model or props.use_pose_optimization))
        expected = 'AnimChar text: '+text_prompt if text_prompt else 'AnimCharTools:body_stand' if shot.get('asset_slot') == 'neutral' else str(reference)
        if saved and saved['reference'] == expected and not alternate:
            settings.animchar_status = '此镜头已有保存结果，已恢复；没有重复调用生成。'
            return
    native, _ = modules()
    prefs = importlib.import_module('AnimCharTools.addons.AnimCharTools.AddonPreferences')
    if not prefs.get_api_key():
        raise ValueError('请在 AnimChar Tools 偏好设置填写 Key。')
    if busy() or editing() or any(native.is_running.values()):
        raise ValueError('AnimChar 正在处理其他任务，请等待完成。')
    neutral = shot.get('asset_slot') == 'neutral' and not Path(reference).is_file() and not text_prompt
    if not source_rig and not neutral and not text_prompt and not Path(reference).is_file():
        raise ValueError('请指定此镜头的有效参考图。')
    scene = context.scene
    proxy = settings.armature.copy(); proxy.data = settings.armature.data.copy()
    proxy.name = 'AniForge AnimChar Work'; proxy.parent = None
    proxy.matrix_world = settings.armature.matrix_world.copy()
    proxy.animation_data_clear(); proxy.hide_render = True
    scene.collection.objects.link(proxy)
    for b in proxy.pose.bones:
        for c in list(b.constraints):
            b.constraints.remove(c)
    props = scene.animchartools_props
    _JOB = dict(scene=scene, settings=settings, shot=dict(shot), proxy=proxy, temporary=[proxy], reference=str(reference),
                frame=scene.frame_current, previous_frame=scene.frame_current, previous_target=props.apply_to_armature,
                previous_import=props.import_input, previous_optimization=props.use_pose_optimization,
                previous_start=props.frame_start, previous_mapping={i.name: i.target for i in props.bone_mapping},
                had_animation_data=bool(settings.armature.animation_data),
                previous_action=settings.armature.animation_data.action if settings.armature.animation_data else None,
                previous_editor=scene.pose_editor_props.edit_armature if hasattr(scene, 'pose_editor_props') else None)
    try:
        # The installed vendor supports these HTTPS endpoints itself. Avoid
        # its legacy unencrypted controller/worker fallback for account keys.
        user_prefs = bpy.context.preferences.addons['AnimCharTools'].preferences
        if not user_prefs.api_base_url:
            user_prefs.api_base_url = 'https://api.animchar.com/'
        if not user_prefs.worker_base_url:
            user_prefs.worker_base_url = 'https://hk001.animchar.com'
        if native.worker_fallback_addr.get('addr', '').startswith('http://'):
            native.worker_fallback_addr['addr'] = ''
        configure(settings, proxy)
        props.import_input = False
        props.frame_start = scene.frame_current
        native.message_to_show['message_pose_capture_image'] = ''
        job = _JOB
        job['completed'] = False
        def completed():
            job['completed'] = True
            native.is_running['pose_capture_image'] = False
        if source_rig:
            job['temporary'].extend(source_objects or [source_rig])
            native.RunRetarget.run_retarget(source_rig, proxy, [scene.frame_current], completed, from_operator='pose_capture_image')
        elif neutral:
            source = native.import_gltf(native.default_skeleton_path, remove_glTF_not_exported=True)
            source.hide_render = True
            job['temporary'].append(source)
            native.init_armature_animation(source)
            scene.pose_editor_props.edit_armature = source
            editor = importlib.import_module('AnimCharTools.addons.PoseEditor.AddonOperators')
            stand = Path(native.addon_assets_path)/'preset_poses/body_stand.pkl'
            editor.load_pose(context, 'body', str(stand))
            scene.pose_editor_props.edit_armature = proxy
            job['reference'] = 'AnimCharTools:body_stand'
            native.RunRetarget.run_retarget(source, proxy, [scene.frame_current], completed, from_operator='pose_capture_image')
        elif text_prompt:
            source = native.import_gltf(native.default_skeleton_path, remove_glTF_not_exported=True)
            source.hide_render = True
            job['temporary'].append(source)
            native.init_armature_animation(source)
            input_folder = Path(prefs.get_save_folder())/'aniforge_inputs'
            input_folder.mkdir(parents=True, exist_ok=True)
            payload = dict(bones_rest=native.collect_rest_data(source),
                           bones_pose=native.collect_keyframes_pose_data(source, [], scene.frame_current),
                           keyframes=[], frame_length=30, text_prompt=text_prompt)
            input_path = input_folder/(hashlib.sha256(text_prompt.encode()).hexdigest()+'.pkl')
            with input_path.open('wb') as stream:
                pickle.dump(payload, stream)
            job['reference'] = 'AnimChar text: '+text_prompt
            job['frame'] = scene.frame_current+15
            callback = functools.partial(native.process_result_file, apply_frame_start=scene.frame_current,
                                         message_attr='pose_capture_image', on_finish_callback=completed)
            cached = None if settings.animchar_rebuild else cached_source(settings, shot, text_prompt=text_prompt)
            if cached:
                callback(filepath=str(cached))
            else:
                threading.Thread(target=native.TaskRequest.run_task,
                                 args=('motion-generation', str(input_path), prefs.get_save_folder(), callback, 'pose_capture_image'), daemon=True).start()
        else:
            reference = Path(reference)
            if reference.suffix.lower() == '.webp':
                raise ValueError('AnimChar 参考图请使用 PNG 或 JPEG；随插件附带的 PR 参考图已有 PNG 副本。')
            callback = functools.partial(native.process_result_file, apply_frame_start=scene.frame_current,
                                         message_attr='pose_capture_image', on_finish_callback=completed)
            api = 'pose-capture-general' if props.use_general_model else 'pose-capture'
            cache = Path(prefs.get_save_folder())/api
            digest = hashlib.sha256(reference.read_bytes()).digest()
            inputs = list(cache.glob('*'+reference.suffix))
            alternate = Path(prefs.get_save_folder())/('pose-capture' if api == 'pose-capture-general' else 'pose-capture-general')
            inputs += list(alternate.glob('*'+reference.suffix))
            cached = None if props.use_pose_optimization or settings.animchar_rebuild else next((p.with_suffix('.glb') for p in sorted(inputs, key=lambda p: p.stat().st_mtime, reverse=True)
                           if p.with_suffix('.glb').is_file() and hashlib.sha256(p.read_bytes()).digest() == digest), None)
            if cached:
                # Native saved inputs and results are paired by filename.
                # Reuse the image capture across character skeletons, while
                # asking AnimChar itself to perform each target retarget.
                callback(filepath=str(cached))
            else:
                threading.Thread(target=native.TaskRequest.run_task,
                                 args=(api, str(reference), prefs.get_save_folder(), callback, 'pose_capture_image'),
                                 kwargs={'json_data_extra': {'pose_optimization': True} if props.use_pose_optimization else {}}, daemon=True).start()
        settings.animchar_status = 'AnimChar：捕捉参考图并适配当前骨架…'
        def poll():
            if not _JOB:
                return None
            if native.is_running['pose_capture_image'] and not job['completed']:
                return .5
            message = native.message_to_show['message_pose_capture_image']
            # Native success leaves an Action on the isolated target. An error
            # message alone must never create a supposedly completed pose.
            success = job['completed'] and bool(proxy.animation_data and proxy.animation_data.action)
            _finish_job('' if success else 'AnimChar 未完成：'+message[:400])
            return None
        bpy.app.timers.register(poll, first_interval=1)
    except Exception:
        _finish_job('AnimChar 捕捉启动失败。')
        raise


def ensure_ready(shot):
    require_shot_asset(shot)
