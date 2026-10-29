import importlib.util
import json
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

spec = importlib.util.spec_from_file_location('aniforge_caption_api', Path(__file__).resolve().parents[1]/'mmd_lora_studio/caption_api.py')
api = importlib.util.module_from_spec(spec); sys.modules[spec.name] = api; spec.loader.exec_module(api)


class CaptionApiTests(unittest.TestCase):
    def test_endpoint_and_secret_representation(self):
        for base, endpoint in [('https://example.com', 'https://example.com/v1/chat/completions'),
                               ('https://example.com/api/v1/', 'https://example.com/api/v1/chat/completions'),
                               ('https://example.com/v1/chat/completions', 'https://example.com/v1/chat/completions')]:
            config = api.ApiConfig(base, 'vision', 'private-unit-key')
            self.assertEqual(config.endpoint(), endpoint)
            self.assertNotIn('private-unit-key', repr(config))
            self.assertEqual(config.fingerprint(), api.ApiConfig(base, 'vision', 'rotated-unit-key').fingerprint())
        for base in ('file:///tmp/image', 'https://key:secret@example.com', 'https://example.com?api_key=secret', 'http://example.com'):
            with self.assertRaises(api.CaptionError):
                api.ApiConfig(base, 'vision', 'private-unit-key').endpoint()

    def test_id_mapping_does_not_depend_on_response_order(self):
        value = {'choices': [{'message': {'content': '```json\n{"images":[{"id":"b","description":"blue coat"},{"id":"a","description":"red coat"}]}\n```'}}]}
        self.assertEqual(api.parse_response(value, ['a', 'b']), {'a': 'red coat', 'b': 'blue coat'})

    def test_grounded_prompt_uses_caption_field_and_new_cache_version(self):
        payload = api.make_payload(api.ApiConfig('https://example.com/v1', 'vision'), [{'id': 'a', 'png': b'png'}])
        prompt = payload['messages'][0]['content']
        self.assertIn('140-240 English words', prompt)
        self.assertIn('Never mix details between images', prompt)
        self.assertIn('exact age', prompt)
        self.assertIn('occluded body', prompt)
        self.assertIn('"caption":', prompt)
        self.assertNotIn('"description":', prompt)
        self.assertGreaterEqual(api.PROMPT_VERSION, 2)
        self.assertEqual(api.parse_response({'images': [{'id': 'a', 'caption': 'A detailed visible outfit.'}]}, ['a']), {'a': 'A detailed visible outfit.'})

    def test_invalid_and_partial_batches_are_rejected(self):
        for rows in ([{'id': 'a', 'description': 'one'}],
                     [{'id': 'a', 'description': 'one'}, {'id': 'a', 'description': 'duplicate'}],
                     [{'id': 'a', 'description': ''}, {'id': 'b', 'description': 'two'}],
                     [{'id': '../other.png', 'description': 'one'}, {'id': 'b', 'description': 'two'}],
                     [{'id': 'a', 'description': '@3d, guessed name'}, {'id': 'b', 'description': 'two'}]):
            with self.assertRaises(api.CaptionError):
                api.parse_response({'images': rows}, ['a', 'b'])
        with self.assertRaises(api.CaptionError):
            api.parse_response({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}, ['a'])

    def test_prefix_and_one_utf8_sidecar_per_image(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('front', 'back'):
                image = Path(folder)/(name+'.png')
                image.write_bytes(b'original-image')
                api.atomic_text(image.with_suffix('.txt'), api.format_caption('明音', 'Detailed\nvisible clothing.'))
                self.assertEqual(image.with_suffix('.txt').read_text(encoding='utf-8'), '@3d, 明音, Detailed visible clothing.\n')
                self.assertEqual(image.read_bytes(), b'original-image')
            self.assertEqual(len(list(Path(folder).glob('*.txt'))), 2)
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_http_request_is_json_with_eight_images(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, self.headers, payload))
                ids = json.loads(payload['messages'][1]['content'][0]['text'])['image_ids']
                value = {'choices': [{'message': {'content': json.dumps({'images': [{'id': i, 'caption': 'Visible '+i} for i in reversed(ids)]})}}]}
                data = json.dumps(value).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            config = api.ApiConfig(f'http://127.0.0.1:{server.server_port}/v1', 'vision', 'unit-test-key')
            items = [{'id': f'image_{i:03}', 'png': b'png-bytes'} for i in range(8)]
            result = api.request_batch(config, items)
            self.assertEqual(set(result), {i['id'] for i in items})
            path, headers, payload = requests[0]
            self.assertEqual(path, '/v1/chat/completions')
            self.assertEqual(headers['Content-Type'], 'application/json')
            self.assertEqual(headers['Authorization'], 'Bearer unit-test-key')
            self.assertEqual(len([c for c in payload['messages'][1]['content'] if c['type'] == 'image_url']), 8)
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
            with self.assertRaises(api.CaptionError):
                api.make_payload(config, items+[{'id': 'extra', 'png': b'x'}])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
