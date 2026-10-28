import importlib.util
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

spec = importlib.util.spec_from_file_location('core', Path(__file__).resolve().parents[1] / 'mmd_lora_studio' / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class RecipeTests(unittest.TestCase):
    def test_three_views_are_the_default_test_budget(self):
        shots = core.three_view_recipe()
        self.assertEqual([s['view'] for s in shots], ['front', 'side', 'back'])
        self.assertEqual([s['azimuth'] for s in shots], [0, 90, 180])
        self.assertTrue(all(s['expression'] == 'neutral' for s in shots))
        with self.assertRaises(ValueError):
            core.three_view_recipe('unknown')

    def test_balanced_unique_recipe(self):
        shots = core.recipe()
        self.assertEqual(len(shots), 36)
        for group in core.GROUPS:
            group_shots = [s for s in shots if s['group'] == group]
            self.assertEqual([s['index'] for s in group_shots], list(range(1, 10)))
            self.assertEqual(len({s['title'] for s in group_shots}), 9)
            self.assertTrue(all(s['pose'] in core.POSE_LABELS and s['expression'] in core.EXPRESSION_LABELS for s in group_shots))

    def test_camera_and_performance_are_designed_for_each_group(self):
        shots = core.recipe()
        portrait = [s for s in shots if s['group'] == 'portrait']
        outfit = [s for s in shots if s['group'] == 'outfit_detail']
        self.assertEqual({s['expression'] for s in portrait}, set(core.EXPRESSION_LABELS)-{'blink'})
        self.assertTrue(all(abs(s['azimuth']) <= 45 for s in portrait))
        self.assertEqual(len({s['focus'] for s in outfit}), 9)
        self.assertTrue(all(s['pose'] == 'arms_clear' and s['expression'] == 'neutral' for s in outfit))
        self.assertTrue({90, 180} <= {s['azimuth'] for s in shots if s['group'] == 'full_body'})
        self.assertTrue(any(s['include_hands'] for s in portrait))
        self.assertEqual(len(core.POSE_LABELS), 15)
        self.assertEqual(set(core.POSE_LABELS), set(core.POSE_DIRECTIONS))
        # Every request gets its own nested controls.
        shots[0]['camera']['camera_lens'] = 25
        self.assertEqual(core.recipe()[0]['camera']['camera_lens'], 85)

    def test_current_mode_and_shot_overrides_preserve_user_controls(self):
        settings = SimpleNamespace(pose='user pose', expression='user face', camera_lens=40, hand_pose='KEEP')
        shots = core.current_recipe(settings)
        self.assertEqual(len(shots), 36)
        self.assertTrue(all(s['pose'] == settings.pose and s['expression'] == settings.expression for s in shots))
        self.assertTrue(all('camera' not in s and 'performance' not in s and 'include_hands' not in s for s in shots))
        effective = core.ShotSettings(settings, core.recipe()[0])
        self.assertEqual(effective.camera_lens, 85)
        self.assertEqual(settings.camera_lens, 40)
        self.assertEqual(effective.hand_pose, 'AUTO')
        self.assertEqual(settings.hand_pose, 'KEEP')

    def test_three_views_keep_the_selected_performance(self):
        shots = core.three_view_recipe('half_body', 'custom pose', 'custom smile')
        self.assertTrue(all(s['pose'] == 'custom pose' and s['expression'] == 'custom smile' for s in shots))

    def test_library_validation_and_legacy_migration(self):
        value = core.validate_library({'poses': {}, 'expressions': {}})
        self.assertEqual(value['cameras'], {})
        self.assertEqual(core.normalized_name('ウィンク２'), core.normalized_name('ウィンク2'))
        for invalid in [{'poses': {'broken': {'head': [[1, 2]]}}},
                        {'expressions': {'bad': {'mesh': {'smile': float('nan')}}}},
                        {'cameras': {'bad': {'camera_lens': 0}}},
                        {'cameras': {'bad': {'camera_projection': 'FISHEYE'}}},
                        {'cameras': {'bad': {'camera_roll': 'invalid'}}}]:
            with self.assertRaises(ValueError):
                core.validate_library(invalid)

    def test_caption_does_not_invent_pose(self):
        text = core.caption('test_character', 'outfit_detail', 'neutral', '')
        self.assertIn('clothing focus', text)
        self.assertNotIn('wave', text)

    def test_safe_path_and_atomic_manifest(self):
        self.assertNotIn('/', core.safe_name('../../test:character'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            core.write_json(path, {'status': 'running'})
            core.write_json(path, {'status': 'complete'})
            self.assertIn('complete', path.read_text())
            self.assertFalse(path.with_suffix('.json.tmp').exists())


if __name__ == '__main__':
    unittest.main()
