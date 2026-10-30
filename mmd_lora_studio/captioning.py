"""Main-thread thumbnails and sidecars; network-only work runs off the UI thread."""
import hashlib
import json
import re
import struct
import zlib
from concurrent.futures import Future
from pathlib import Path
from threading import Thread

import bpy
import numpy as np
from bpy.props import StringProperty, IntProperty, EnumProperty

from .caption_api import ApiConfig, CaptionError, BATCH_SIZE, IMAGE_SIZE, request_batch, format_caption, atomic_text, camera_context
from .core import dataset_metadata_dir, safe_name

_JOB = None


def preferences(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def api_config(context):
    prefs = preferences(context)
    if prefs is None:
        raise CaptionError('请先在偏好设置中启用 AniForge，再配置打标 API。')
    config = ApiConfig(prefs.base_url, prefs.vision_model, prefs.api_key, prefs.caption_language, prefs.api_timeout)
    config.endpoint()
    return config


def preflight(context):
    settings = context.scene.ml_settings
    if busy():
        raise CaptionError('已有图片打标任务正在进行。')
    if settings.api_caption:
        if not settings.trigger.strip() or settings.trigger.strip() == 'my_character':
            raise CaptionError('请填写实际角色名，固定前缀由插件拼接。')
        api_config(context)


def thumbnail_png(path):
    """Downscale proportionally to at most 1024 on the long edge; no padding."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.alpha_mode = 'STRAIGHT'
        width, height = image.size
        if width <= 0 or height <= 0:
            raise CaptionError('图片无法读取。')
        scale = min(1.0, IMAGE_SIZE/width, IMAGE_SIZE/height)
        width, height = max(1, round(width*scale)), max(1, round(height*scale))
        if tuple(image.size) != (width, height):
            image.scale(width, height)
        data = np.empty(width*height*4, dtype=np.float32)
        image.pixels.foreach_get(data)
        rgba = data.reshape(height, width, 4)
        alpha = np.clip(rgba[:, :, 3:4], 0, 1)
        rgb = np.clip(rgba[:, :, :3], 0, 1)*alpha+(1-alpha)
        pixels = np.rint(rgb*255).astype(np.uint8)
        def chunk(kind, payload):
            return struct.pack('>I', len(payload))+kind+payload+struct.pack('>I', zlib.crc32(kind+payload)&0xffffffff)
        # Blender buffers start at the bottom; PNG scanlines start at the top.
        rows = b''.join(b'\0'+row.tobytes() for row in pixels[::-1])
        header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
        return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', header)+chunk(b'IDAT', zlib.compress(rows, 6))+chunk(b'IEND', b'')
    finally:
        bpy.data.images.remove(image)


def _json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def _read_json(path):
    try:
        if path.stat().st_size > 8*1024*1024:
            return {}
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read_manifest(root):
    path = dataset_metadata_dir(root)/'manifest.json'
    # Old four-directory outputs can still be captioned without moving assets.
    return _read_json(path if path.is_file() else root/'manifest.json')


def image_paths(root):
    manifest = read_manifest(root)
    if manifest.get('shots'):
        paths = [(root/shot['image']).resolve() for shot in manifest['shots']]
    else:
        paths = sorted(p.resolve() for p in root.iterdir() if p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'})
    if not paths:
        raise CaptionError('请选择单次渲染输出目录，或直接包含图片的目录。')
    if len(set(paths)) != len(paths) or any(not p.is_relative_to(root) or not p.is_file() for p in paths):
        raise CaptionError('图片清单包含重复、丢失或目录以外的文件。')
    return paths


def resolve_caption_folder(settings):
    """Follow the current output, selecting one current-character dataset."""
    value = settings.caption_folder.strip() or settings.output.strip()
    if not value:
        raise CaptionError('请先填写输出目录，或另选补打标目录。')
    root = Path(bpy.path.abspath(value)).resolve()
    if not root.is_dir():
        raise CaptionError('输出目录不存在，请先渲染图片或另选已有图片目录。')

    def has_images(folder):
        return bool(read_manifest(folder).get('shots')) or any(
            p.is_file() and p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'} for p in folder.iterdir())

    if has_images(root):
        image_paths(root)  # Validate the selected dataset before starting a job.
        return root
    pattern = re.compile(re.escape(safe_name(settings.trigger)) + r'_\d{8}_\d{6}_\d{6}')
    candidates = sorted((p for p in root.iterdir() if p.is_dir() and pattern.fullmatch(p.name)),
                        key=lambda p: p.name, reverse=True)
    for folder in candidates:
        if has_images(folder):
            image_paths(folder)
            return folder.resolve()
    # Renaming the caption prefix must still work for a private one-character
    # output directory. A previous session outside this output is never used.
    last = Path(bpy.path.abspath(settings.last_render_folder)).resolve() if settings.last_render_folder.strip() else None
    generated = re.compile(r'(.+)_\d{8}_\d{6}_\d{6}')
    if last is not None and last.parent == root and (match := generated.fullmatch(last.name)):
        folders = [(p, m[1]) for p in root.iterdir() if p.is_dir() and (m := generated.fullmatch(p.name))]
        if {name for _, name in folders} == {match[1]}:
            for folder, _ in sorted(folders, key=lambda item: item[0].name, reverse=True):
                if has_images(folder):
                    image_paths(folder)
                    return folder.resolve()
    raise CaptionError('输出目录中未找到当前角色的图片；可另选单次渲染目录。')


class CaptionJob:
    def __init__(self, scene, root, config):
        self.scene = scene; self.root = root; self.config = config
        self.character = scene.ml_settings.trigger.strip()
        if not self.character or self.character == 'my_character':
            raise CaptionError('请先填写实际角色名。')
        self.signature = config.fingerprint()
        self.cache_path = dataset_metadata_dir(root)/'caption_cache.json'
        self.cache = _read_json(self.cache_path if self.cache_path.is_file() else root/'caption_cache.json')
        self.cache = {'schema_version': 1, 'images': self.cache.get('images', {})}
        if not isinstance(self.cache['images'], dict):
            self.cache['images'] = {}
        self.pending = []; self.total = 0; self.done = 0; self.failed = 0
        self.future = None; self.batch = []; self.stop_after_batch = False; self.errors = {}
        self.records = {}
        manifest = read_manifest(root)
        contexts = {Path(shot['image']).as_posix(): camera_context(shot)
                    for shot in manifest.get('shots', []) if isinstance(shot, dict) and isinstance(shot.get('image'), str)}
        for index, path in enumerate(image_paths(root), 1):
            rel = path.relative_to(root).as_posix()
            context = contexts.get(rel)
            if context is None:
                match = re.search(r'_(portrait|half_body|full_body|lower_body)_\d+$', path.stem)
                # Historical three-view index 03 means back, while full index
                # 03 means right: never guess a view from an image number.
                context = camera_context({'group': match[1]}) if match else {}
            item = {'id': f'image_{index:03}', 'path': path, 'relative': rel,
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'context': context}
            self.total += 1
            cached = self.cache['images'].get(rel, {})
            if isinstance(cached, dict) and cached.get('sha256') == item['sha256'] and cached.get('signature') == self.signature and cached.get('context', {}) == context and isinstance(cached.get('description'), str) and cached['description'].strip():
                self.write_caption(item, cached['description'])
            else:
                self.pending.append(item)

    def write_caption(self, item, description):
        caption = format_caption(self.character, description)
        atomic_text(item['path'].with_suffix('.txt'), caption)
        self.records[item['relative']] = {'caption': caption, 'caption_status': 'API_COMPLETE'}
        self.done += 1

    def prepare(self):
        self.batch = self.pending[:BATCH_SIZE]
        del self.pending[:len(self.batch)]
        return [{'id': item['id'], 'png': thumbnail_png(item['path']), 'context': item['context']} for item in self.batch]

    def accept(self, descriptions):
        # Persist validated descriptions first: a later file-write failure or
        # restart can repair TXT files without paying for another API request.
        for item in self.batch:
            self.cache['images'][item['relative']] = {'sha256': item['sha256'], 'signature': self.signature,
                                                     'context': item['context'],
                                                     'description': descriptions[item['id']]}
        _json_write(self.cache_path, self.cache)
        for item in self.batch:
            self.write_caption(item, descriptions[item['id']])
        self.update_manifest()

    def fail(self, message):
        self.scene.ml_settings.caption_error = message
        for item in self.batch:
            if item['relative'] not in self.records:
                self.failed += 1
                self.errors[item['relative']] = message
                self.records[item['relative']] = {'caption_status': 'API_FAILED', 'caption_error': message}
        self.update_manifest()

    def update_manifest(self):
        path = dataset_metadata_dir(self.root)/'manifest.json'
        manifest = read_manifest(self.root)
        if not manifest:
            return
        manifest['dataset_folder'] = self.root.name
        for shot in manifest.get('shots', []):
            key = Path(shot['image']).as_posix()
            if key in self.records:
                shot.update(self.records[key])
                if shot['caption_status'] == 'API_COMPLETE':
                    shot.pop('caption_error', None)
        manifest['captioning'] = {'completed': self.done, 'failed': self.failed, 'total': self.total,
                                 'batch_size': BATCH_SIZE, 'api_image_max_edge': IMAGE_SIZE,
                                 'api_image_resize': 'proportional_no_padding_no_upscale',
                                 'errors': self.errors}
        manifest['dataset_status'] = ('READY' if self.done == self.total else
                                      'CAPTION_STOPPED' if self.stop_after_batch else
                                      'CAPTION_FAILED' if self.failed else 'CAPTION_PENDING')
        _json_write(path, manifest)


def busy():
    return _JOB is not None


def _worker(future, config, images):
    try:
        future.set_result(request_batch(config, images))
    except Exception as exc:
        future.set_exception(exc)


def _finish(job):
    global _JOB
    settings = job.scene.ml_settings
    try:
        job.update_manifest()
        if job.stop_after_batch:
            settings.caption_status = f'打标已停止：完成 {job.done}/{job.total}'
        elif job.failed:
            settings.caption_status = f'API 打标：完成 {job.done}，失败 {job.failed}，可重试'
        else:
            settings.caption_status = f'API 打标完成：{job.done}/{job.total}'
        settings.caption_error = next(iter(job.errors.values()), '')
    finally:
        settings.caption_running = False
        _JOB = None


def tick():
    job = _JOB
    if job is None:
        return None
    try:
        settings = job.scene.ml_settings
        if job.future is not None:
            if not job.future.done():
                return .2
            try:
                job.accept(job.future.result())
            except Exception as exc:
                job.fail(str(exc) if isinstance(exc, CaptionError) else '打标结果保存失败，请检查输出目录后重试。')
            job.future = None
        if job.stop_after_batch or not job.pending:
            _finish(job); return None
        try:
            images = job.prepare()
        except Exception:
            job.fail('图片压缩失败，请检查本批图片是否可读取。')
            return .1
        settings.caption_status = f'API 打标：{job.done}/{job.total} · 本批 {len(images)} 张'
        job.future = Future()
        Thread(target=_worker, args=(job.future, job.config, images), daemon=True).start()
        return .2
    except (ReferenceError, AttributeError):
        shutdown(); return None
    except Exception:
        try:
            job.scene.ml_settings.caption_status = '打标未完成，请检查输出目录后重试。'
            job.scene.ml_settings.caption_running = False
        finally:
            shutdown()
        return None


def start(context, root, config=None, blocking=None):
    global _JOB
    if busy():
        raise CaptionError('已有图片打标任务正在进行。')
    config = config or api_config(context)
    job = CaptionJob(context.scene, Path(root).resolve(), config)
    context.scene.ml_settings.caption_error = ''
    context.scene.ml_settings.caption_running = True
    _JOB = job
    blocking = bpy.app.background if blocking is None else blocking
    if blocking:
        try:
            while job.pending and not job.stop_after_batch:
                try:
                    images = job.prepare()
                    job.accept(request_batch(config, images))
                except Exception as exc:
                    job.fail(str(exc) if isinstance(exc, CaptionError) else '图片读取或打标结果保存失败，请检查输出目录。')
        finally:
            _finish(job)
    else:
        if not bpy.app.timers.is_registered(tick):
            bpy.app.timers.register(tick, first_interval=.1)
    return job


def shutdown():
    global _JOB
    if _JOB is not None:
        try:
            _JOB.scene.ml_settings.caption_running = False
        except (ReferenceError, AttributeError):
            pass
    _JOB = None
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)


@bpy.app.handlers.persistent
def on_load(_=None):
    shutdown()


class MLPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    base_url: StringProperty(name='Base URL', default='')
    vision_model: StringProperty(name='视觉模型', default='')
    api_key: StringProperty(name='API Key', subtype='PASSWORD', default='')
    caption_language: EnumProperty(name='描述语言', items=[('en', '英文', ''), ('zh', '中文', '')], default='en')
    api_timeout: IntProperty(name='超时 (秒)', default=120, min=5, max=600)

    def draw(self, context):
        draw_config(self.layout, context)


def draw_config(layout, context):
    prefs = preferences(context)
    if prefs is None:
        layout.label(text='启用插件后可配置 API。', icon='INFO'); return
    for prop in ('base_url', 'vision_model', 'api_key', 'caption_language', 'api_timeout'):
        layout.prop(prefs, prop)
    layout.label(text='兼容 Chat Completions 视觉接口')
    layout.label(text=f'每批 {BATCH_SIZE} 张 · 等比缩小 · 长边 ≤1024')
    layout.label(text='逐图附带取景 / 方向 · 以画面为准')
    layout.operator('ml.save_caption_api', text='保存 API 设置', icon='FILE_TICK')


class ML_OT_save_caption_api(bpy.types.Operator):
    bl_idname = 'ml.save_caption_api'; bl_label = '保存本机 API 设置'

    def execute(self, context):
        try:
            api_config(context)
            bpy.ops.wm.save_userpref()
            self.report({'INFO'}, 'API 设置已保存在本机偏好设置。')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_caption_images(bpy.types.Operator):
    bl_idname = 'ml.caption_images'; bl_label = '为已有图片打标 / 重试失败项'

    @classmethod
    def poll(cls, context):
        return not context.scene.ml_settings.running and not busy()

    def execute(self, context):
        try:
            settings = context.scene.ml_settings
            start(context, resolve_caption_folder(settings))
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


class ML_OT_cancel_caption(bpy.types.Operator):
    bl_idname = 'ml.cancel_caption'; bl_label = '停止后续打标批次'

    def execute(self, context):
        if _JOB is not None:
            _JOB.stop_after_batch = True
            context.scene.ml_settings.caption_status = '当前批次完成后停止'
        return {'FINISHED'}


CLASSES = (MLPreferences, ML_OT_save_caption_api, ML_OT_caption_images, ML_OT_cancel_caption)
