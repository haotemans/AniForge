"""Local mock API, proportional memory thumbnails, single-image requests; no renders."""
import base64
import hashlib
import json
import shutil
import struct
import sys
import tempfile
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import Counter
from threading import Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import numpy as np
import mmd_lora_studio as addon
from mmd_lora_studio import captioning
from mmd_lora_studio.caption_api import ApiConfig, FRAMING_HINTS, VIEW_HINTS
from mmd_lora_studio.core import dataset_metadata_dir

addon.register()
settings = bpy.context.scene.ml_settings
settings.trigger = 'LOCAL_CHARACTER_A'
assert 'api_key' not in settings.bl_rna.properties
requests = []; invalid = [False]; view_overrides = {}
request_ids = []; response_plans = {}


def check_png(data, expected=(64, 32)):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    position = 8; compressed = b''
    while position < len(data):
        size = struct.unpack('>I', data[position:position+4])[0]
        kind = data[position+4:position+8]
        value = data[position+8:position+8+size]
        if kind == b'IHDR':
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', value)
            assert (width, height) == expected
            assert (depth, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)
        if kind == b'IDAT': compressed += value
        position += size+12
    rows = np.frombuffer(zlib.decompress(compressed), dtype=np.uint8).reshape(height, width*3+1)
    assert np.all(rows[:, 0] == 0)
    rgb = rows[:, 1:].reshape(height, width, 3)
    # Content must reach the edges, with no new white borders or upside-down image.
    assert tuple(rgb[0, 0]) == (255, 0, 0), rgb[0, 0]
    assert tuple(rgb[-1, 0]) == (0, 0, 255), rgb[-1, 0]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        assert b'LOCAL_CHARACTER' not in raw
        payload = json.loads(raw)
        content = payload['messages'][1]['content']
        ids = json.loads(content[0]['text'])['image_ids']
        metadata = {item['id']: item for part in content[1:] if part['type'] == 'text'
                    for item in [json.loads(part['text'])]}
        for key in ids:
            index = int(key.rsplit('_', 1)[1])-1
            expected_group = tuple(FRAMING_HINTS)[index//4]
            expected_view = view_overrides.get(key, ('front', 'left', 'right', 'back')[index%4])
            hint = metadata[key]['render_context']
            assert hint['framing'] == FRAMING_HINTS[expected_group]
            assert hint['view'] == VIEW_HINTS[expected_view]
        images = [part['image_url']['url'] for part in content if part['type'] == 'image_url']
        assert len(ids) == len(images) == 1
        for image in images:
            check_png(base64.b64decode(image.split(',', 1)[1]))
        requests.append(len(images))
        request_ids.extend(ids)
        plan = response_plans.get(ids[0], [])
        mode = plan.pop(0) if plan else ''
        if mode in {'500', '401'}:
            self.send_response(int(mode)); self.end_headers(); return
        rows = [{'id': key, 'caption': 'Detailed visible red and blue clothing for '+key+'.'} for key in reversed(ids)]
        if invalid[0]: rows = rows[:-1]
        body = {'caption': 'Wrong response schema.'} if mode == 'schema' else {'images': rows}
        response = json.dumps({'choices': [{'message': {'content': json.dumps(body)}}]}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(response)
    def log_message(self, *args): pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True); thread.start()
config = ApiConfig(f'http://127.0.0.1:{server.server_port}/v1', 'vision-unit', 'unit-test-key')


def fixture(root, source, count):
    root.mkdir()
    shots = []
    for i in range(1, count+1):
        image = root/f'frame_{i:02}.png'; shutil.copyfile(source, image)
        shots.append({'image': image.name, 'caption_status': 'API_PENDING',
                      'group': tuple(FRAMING_HINTS)[(i-1)//4], 'view': ('front', 'left', 'right', 'back')[(i-1)%4]})
    metadata = dataset_metadata_dir(root); metadata.mkdir(parents=True, exist_ok=True)
    (metadata/'manifest.json').write_text(json.dumps({'shots': shots}), encoding='utf-8')


def drain():
    deadline = time.monotonic()+10
    while captioning.busy() and time.monotonic() < deadline:
        captioning.tick(); time.sleep(.01)
    assert not captioning.busy(), 'Caption job did not finish'


try:
    with tempfile.TemporaryDirectory() as folder, patch.object(captioning, 'RETRY_DELAYS', (0, 0)):
        root = Path(folder)
        source = root/'source.png'
        image = bpy.data.images.new('Caption fixture', width=64, height=32, alpha=True)
        pixels = np.zeros((32, 64, 4), dtype=np.float32); pixels[:, :, 3] = 1
        pixels[:16, :, 2] = 1; pixels[16:, :, 0] = 1
        image.pixels.foreach_set(pixels.ravel())
        image.filepath_raw = str(source); image.file_format = 'PNG'; image.save()
        bpy.data.images.remove(image)
        image_count = len(bpy.data.images)
        for width, height, expected in ((2048, 1024, (1024, 512)), (1024, 2048, (512, 1024)), (512, 512, (512, 512))):
            path = root/f'ratio_{width}_{height}.png'
            sample = bpy.data.images.new('Ratio fixture', width=width, height=height, alpha=True)
            pixels = np.zeros((height, width, 4), dtype=np.float32); pixels[:, :, 3] = 1
            pixels[:height//2, :, 2] = 1; pixels[height//2:, :, 0] = 1
            sample.pixels.foreach_set(pixels.ravel())
            sample.filepath_raw = str(path); sample.file_format = 'PNG'; sample.save()
            bpy.data.images.remove(sample)
            original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            check_png(captioning.thumbnail_png(path), expected)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
        assert len(bpy.data.images) == image_count
        # Manual captioning follows the output currently selected in the UI,
        # selecting one latest matching character rather than a stale session.
        output = root/'render_output'; output.mkdir()
        old = output/'LOCAL_CHARACTER_A_20261029_120000_000001'
        latest = output/'LOCAL_CHARACTER_A_20261030_120000_000002'
        unrelated = output/'OTHER_CHARACTER_20261031_120000_000003'
        for folder in (old, latest, unrelated): fixture(folder, source, 1)
        (output/'LOCAL_CHARACTER_A_20261101_120000_000004').mkdir()  # Empty interrupted run.
        settings.output = str(output); settings.caption_folder = ''
        settings.last_render_folder = str(unrelated)
        assert captioning.resolve_caption_folder(settings) == latest
        with patch.object(captioning, 'start') as start:
            assert bpy.ops.ml.caption_images() == {'FINISHED'}
            assert start.call_count == 1 and start.call_args.args[1] == latest
        settings.output = str(old)
        assert captioning.resolve_caption_folder(settings) == old
        other_output = root/'other_output'; other_output.mkdir()
        other_latest = other_output/'LOCAL_CHARACTER_A_20261028_120000_000001'
        fixture(other_latest, source, 1)
        settings.output = str(other_output)
        assert captioning.resolve_caption_folder(settings) == other_latest
        settings.last_render_folder = str(other_latest)
        settings.trigger = 'Renamed caption prefix'
        assert captioning.resolve_caption_folder(settings) == other_latest
        settings.trigger = 'LOCAL_CHARACTER_A'
        settings.caption_folder = str(latest)
        assert captioning.resolve_caption_folder(settings) == latest
        settings.caption_folder = ''
        empty_output = root/'only_other_character'; empty_output.mkdir()
        fixture(empty_output/'OTHER_CHARACTER_20261031_120000_000003', source, 1)
        settings.output = str(empty_output)
        try:
            captioning.resolve_caption_folder(settings)
        except captioning.CaptionError:
            pass
        else:
            raise AssertionError('A different character was selected implicitly')
        dataset = root/'dataset'; fixture(dataset, source, 16)
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dataset.glob('*.png')}
        job = captioning.start(bpy.context, dataset, config=config, blocking=True)
        assert requests == [1]*16 and job.done == 16 and job.failed == 0
        assert json.loads((dataset_metadata_dir(dataset)/'manifest.json').read_text())['dataset_status'] == 'READY'
        assert all(p.is_file() and p.suffix in {'.png', '.txt'} for p in dataset.iterdir())
        assert len(list(dataset.glob('*.txt'))) == 16
        for i in range(1, 17):
            text = (dataset/f'frame_{i:02}.txt').read_text(encoding='utf-8')
            assert text.startswith('@3d, LOCAL CHARACTER A, ')
            assert f'image_{i:03}' in text
        assert 'unit-test-key' not in (dataset_metadata_dir(dataset)/'caption_cache.json').read_text()
        assert len(bpy.data.images) == image_count

        # Renaming the local trigger rewrites prefixes without any API calls.
        settings.trigger = 'LOCAL_CHARACTER_B'
        captioning.start(bpy.context, dataset, config=config, blocking=True)
        assert requests == [1]*16
        assert all(p.read_text().startswith('@3d, LOCAL CHARACTER B, ') for p in dataset.glob('*.txt'))

        # Reproduce the real 13-success/3-failure run. Only missing captions
        # need new API calls; one HTTP 500 and two bad schemas recover.
        cache_path = dataset_metadata_dir(dataset)/'caption_cache.json'; cache = json.loads(cache_path.read_text())
        for i, failure in ((12, 'schema'), (13, '500'), (16, 'schema')):
            del cache['images'][f'frame_{i:02}.png']
            (dataset/f'frame_{i:02}.txt').unlink()
            response_plans[f'image_{i:03}'] = [failure]
        cache_path.write_text(json.dumps(cache), encoding='utf-8')
        previous_requests = len(requests)
        job = captioning.start(bpy.context, dataset, config=config, blocking=True)
        assert job.done == 16 and job.failed == 0
        assert Counter(request_ids[previous_requests:]) == {'image_012': 2, 'image_013': 2, 'image_016': 2}
        manifest = json.loads((dataset_metadata_dir(dataset)/'manifest.json').read_text())
        assert manifest['dataset_status'] == 'READY' and manifest['captioning']['errors'] == {}
        assert set(manifest['captioning']['attempts'].values()) == {2}
        assert all(shot['caption_status'] == 'API_COMPLETE' and 'caption_error' not in shot for shot in manifest['shots'])
        assert len(list(dataset.glob('*.txt'))) == 16

        cache_path = dataset_metadata_dir(dataset)/'caption_cache.json'; cache = json.loads(cache_path.read_text())
        for i in range(1, 9):
            del cache['images'][f'frame_{i:02}.png']
            (dataset/f'frame_{i:02}.txt').unlink()
        cache_path.write_text(json.dumps(cache), encoding='utf-8')
        invalid[0] = True
        previous_requests = len(requests)
        job = captioning.start(bpy.context, dataset, config=config, blocking=True)
        assert job.done == 8 and job.failed == 8
        assert len(requests) == previous_requests+8*captioning.MAX_ATTEMPTS
        manifest = json.loads((dataset_metadata_dir(dataset)/'manifest.json').read_text())
        assert manifest['dataset_status'] == 'CAPTION_FAILED'
        assert len(manifest['captioning']['errors']) == 8
        assert set(manifest['captioning']['attempts'].values()) == {3}
        assert '标签未齐全' in settings.caption_status and 'frame_01.png' in settings.caption_error
        assert len(list(dataset.glob('*.txt'))) == 8
        invalid[0] = False
        previous_requests = len(requests)
        job = captioning.start(bpy.context, dataset, config=config, blocking=True)
        assert len(requests) == previous_requests+8 and job.done == 16
        assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dataset.glob('*.png')}
        assert not list(dataset.glob('*.tmp'))

        # Read a historical grouped output and its in-folder cache, keeping
        # the original images in place and writing new status outside it.
        legacy = root/'legacy'; grouped = legacy/'portrait'; grouped.mkdir(parents=True)
        old_image = grouped/'old.png'; shutil.copyfile(source, old_image)
        (legacy/'manifest.json').write_text(json.dumps({'shots': [{'image': 'portrait/old.png'}]}), encoding='utf-8')
        (legacy/'caption_cache.json').write_text(json.dumps({'images': {'portrait/old.png': {
            'sha256': hashlib.sha256(old_image.read_bytes()).hexdigest(), 'signature': config.fingerprint(),
            'description': 'A visible red and blue image.'}}}), encoding='utf-8')
        old_requests = len(requests)
        job = captioning.start(bpy.context, legacy, config=config, blocking=True)
        assert job.done == 1 and len(requests) == old_requests
        assert old_image.with_suffix('.txt').read_text().startswith('@3d, LOCAL CHARACTER B, ')
        assert json.loads((dataset_metadata_dir(legacy)/'manifest.json').read_text())['dataset_status'] == 'READY'
        assert hashlib.sha256(old_image.read_bytes()).hexdigest() == hashlib.sha256(source.read_bytes()).hexdigest()
        settings.output = str(legacy)
        assert captioning.resolve_caption_folder(settings) == legacy

        asynchronous = root/'async'; fixture(asynchronous, source, 2)
        response_plans['image_001'] = ['500', 'schema']
        previous_requests = len(requests)
        captioning.start(bpy.context, asynchronous, config=config, blocking=False)
        assert settings.caption_running
        drain()
        assert request_ids[previous_requests:] == ['image_001']*3+['image_002']
        assert len(list(asynchronous.glob('*.txt'))) == 2
        assert not settings.caption_running

        # Authentication errors are not transient and must not multiply calls.
        unauthorized = root/'unauthorized'; fixture(unauthorized, source, 1)
        response_plans['image_001'] = ['401']
        previous_requests = len(requests)
        job = captioning.start(bpy.context, unauthorized, config=config, blocking=True)
        assert job.failed == 1 and len(requests) == previous_requests+1
        assert '401' in settings.caption_error and not list(unauthorized.glob('*.txt'))

        # Cancel while waiting for a retry: the timer remains responsive, and
        # neither a second attempt nor another image is sent after cancellation.
        waiting = root/'waiting'; fixture(waiting, source, 2)
        response_plans['image_001'] = ['500']
        previous_requests = len(requests)
        with patch.object(captioning, 'RETRY_DELAYS', (10, 20)):
            job = captioning.start(bpy.context, waiting, config=config, blocking=False)
            captioning.tick()
            deadline = time.monotonic()+5
            while not job.future.done() and time.monotonic() < deadline: time.sleep(.01)
            assert job.future.done()
            assert 0 < captioning.tick() <= .2
            assert job.retry_at > time.monotonic() and '自动重试 2/3' in settings.caption_status
            assert len(requests) == previous_requests+1
            job.stop_after_batch = True
            drain()
        assert len(requests) == previous_requests+1 and job.done == 0
        manifest = json.loads((dataset_metadata_dir(waiting)/'manifest.json').read_text())
        assert manifest['dataset_status'] == 'CAPTION_STOPPED'
        assert manifest['shots'][0]['caption_status'] == 'API_PENDING'

        # A local disk-write error must not resend an already paid API result.
        disk_error = root/'disk_error'; fixture(disk_error, source, 1)
        previous_requests = len(requests)
        original_atomic_text = captioning.atomic_text
        def deny_txt(path, text):
            if Path(path).suffix == '.txt': raise PermissionError('fixture')
            return original_atomic_text(path, text)
        with patch.object(captioning, 'atomic_text', side_effect=deny_txt):
            job = captioning.start(bpy.context, disk_error, config=config, blocking=True)
        assert job.failed == 1 and len(requests) == previous_requests+1
        job = captioning.start(bpy.context, disk_error, config=config, blocking=True)
        assert job.done == 1 and len(requests) == previous_requests+1

        # Completion checks actual TXT files; a removed file cannot be READY.
        lost_txt = root/'lost_txt'; fixture(lost_txt, source, 1)
        job = captioning.start(bpy.context, lost_txt, config=config, blocking=False)
        original_accept = job.accept
        def remove_sidecar(descriptions):
            original_accept(descriptions)
            next(lost_txt.glob('*.txt')).unlink()
        with patch.object(job, 'accept', side_effect=remove_sidecar): drain()
        assert job.done == 0 and job.failed == 1
        manifest = json.loads((dataset_metadata_dir(lost_txt)/'manifest.json').read_text())
        assert manifest['dataset_status'] == 'CAPTION_FAILED' and 'TXT' in settings.caption_error
        previous_requests = len(requests)
        job = captioning.start(bpy.context, lost_txt, config=config, blocking=True)
        assert job.done == 1 and len(requests) == previous_requests

        cancelled = root/'cancelled'; fixture(cancelled, source, 16)
        before_cancel = len(requests)
        job = captioning.start(bpy.context, cancelled, config=config, blocking=False)
        captioning.tick(); job.stop_after_batch = True
        drain()
        assert job.done == 1 and len(list(cancelled.glob('*.txt'))) == 1
        assert len(requests) == before_cancel+1 and requests[-1] == 1
        assert '停止' in settings.caption_status
        assert len(bpy.data.images) == image_count

        # Editing the camera hint invalidates only that image's cached caption.
        metadata = dataset_metadata_dir(dataset)/'manifest.json'
        manifest = json.loads(metadata.read_text())
        manifest['shots'][0]['view'] = 'back'
        view_overrides['image_001'] = 'back'
        metadata.write_text(json.dumps(manifest), encoding='utf8')
        previous_requests = len(requests)
        job = captioning.start(bpy.context, dataset, config=config, blocking=True)
        assert job.done == 16 and len(requests) == previous_requests+1 and requests[-1] == 1
        assert job.cache['images']['frame_01.png']['context']['view'] == 'back'
        assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dataset.glob('*.png')}
        assert all(p.is_file() and p.suffix in {'.png','.txt'} for p in dataset.iterdir())
finally:
    captioning.shutdown(); addon.unregister()
    server.shutdown(); server.server_close(); thread.join()

print('CAPTIONING_OK: batch1, proportional PNGs <=1024, 1 image/1 TXT, 13+3 recovery, bounded retries, auth/disk failures, async/cancel, final TXT verification; zero renders')
