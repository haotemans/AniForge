import importlib.util
import copy
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
        self.assertEqual(len(shots), 39)
        for group in core.GROUPS:
            group_shots = [s for s in shots if s['group'] == group]
            self.assertEqual([s['index'] for s in group_shots], list(range(1, 13)) if group == 'portrait' else list(range(1, 10)))
            self.assertEqual(len({s['title'] for s in group_shots}), 12 if group == 'portrait' else 9)
            self.assertTrue(all(s['pose'] in core.POSE_LABELS and s['expression'] in core.EXPRESSION_LABELS for s in group_shots))

    def test_camera_and_performance_are_designed_for_each_group(self):
        shots = core.recipe()
        portrait = [s for s in shots if s['group'] == 'portrait']
        outfit = [s for s in shots if s['group'] == 'outfit_detail']
        self.assertTrue(all(s['expression'] == 'neutral' for s in portrait[:4]))
        self.assertEqual([s['azimuth'] for s in portrait[:4]], [0, 90, -90, 180])
        self.assertTrue(all(s['camera']['camera_aspect'] == 'SQUARE' for s in portrait))
        self.assertTrue(all(abs(s['azimuth']) <= 45 for s in portrait[4:]))
        self.assertEqual(len({s['focus'] for s in outfit}), 9)
        self.assertTrue(all(s['pose'] == 'arms_clear' and s['expression'] == 'neutral' for s in outfit))
        self.assertTrue({90, 180} <= {s['azimuth'] for s in shots if s['group'] == 'full_body'})
        self.assertTrue(any(s['include_hands'] for s in portrait))
        self.assertEqual(len(core.POSE_LABELS), 17)
        self.assertTrue(core.SEATED_POSES <= {s['pose'] for s in shots if s['group'] == 'full_body'})
        self.assertEqual(set(core.POSE_LABELS), set(core.POSE_DIRECTIONS))
        # Every request gets its own nested controls.
        shots[0]['camera']['camera_lens'] = 25
        self.assertEqual(core.recipe()[0]['camera']['camera_lens'], 85)

    def test_current_mode_and_shot_overrides_preserve_user_controls(self):
        settings = SimpleNamespace(pose='user pose', expression='user face', camera_lens=40, hand_pose='KEEP')
        shots = core.current_recipe(settings)
        self.assertEqual(len(shots), 39)
        self.assertTrue(all(s['pose'] == settings.pose and s['expression'] == settings.expression for s in shots))
        self.assertTrue(all('performance' not in s and 'include_hands' not in s for s in shots))
        self.assertTrue(all(s['camera']['camera_aspect'] == 'SQUARE' for s in shots if s['group'] == 'portrait'))
        effective = core.ShotSettings(settings, core.recipe()[0])
        self.assertEqual(effective.camera_lens, 85)
        self.assertEqual(settings.camera_lens, 40)
        self.assertEqual(effective.hand_pose, 'AUTO')
        self.assertEqual(settings.hand_pose, 'KEEP')

    def test_39_photos_cover_character_information_without_pose_triplication(self):
        report = core.dataset_coverage(core.recipe())
        self.assertEqual(report['total'], 39)
        self.assertEqual(report['per_group'], {'portrait': 12, 'half_body': 9, 'full_body': 9, 'outfit_detail': 9})
        self.assertEqual(report['seated_photos'], 3)
        self.assertTrue({90, 180} <= set(report['full_body_angles']))
        self.assertIn('SHOES', report['clothing_regions'])
        self.assertEqual(len(report['clothing_regions']), 9)

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

    def test_local_pose_slots_preserve_captured_gestures_and_39_photo_budget(self):
        library = core.empty_library()
        library['poses']['local heart'] = {}
        library['expressions']['local smile'] = {}
        library['shot_overrides']['portrait:8'] = {'pose': 'local heart', 'expression': 'local smile',
            'title': 'Face heart', 'include_hands': ['left_wrist', 'right_wrist'], 'support': 'floor', 'azimuth': -35}
        library = core.validate_library(library)
        original = core.recipe()
        snapshot = copy.deepcopy(original)
        shots = core.apply_shot_overrides(original, library)
        replaced = shots[7]
        self.assertEqual(replaced['pose'], 'local heart')
        self.assertEqual(replaced['azimuth'], -35)
        self.assertEqual(replaced['performance']['head_angles'], [0, 0, 0])
        self.assertEqual(replaced['performance']['hand_pose'], 'KEEP')
        self.assertEqual(replaced['performance']['expression_strength'], 1)
        self.assertEqual(replaced['include_hands'], ['left_wrist', 'right_wrist'])
        self.assertEqual(original, snapshot)
        self.assertEqual(core.dataset_coverage(shots)['per_group'], {'portrait': 12, 'half_body': 9, 'full_body': 9, 'outfit_detail': 9})
        self.assertEqual(shots[30:], original[30:])
        self.assertEqual(core.dataset_coverage(shots)['seated_photos'], 3)
        self.assertEqual(core.validate_library({})['shot_overrides'], {})
        self.assertEqual(core.validate_library({'shot_overrides': {'portrait:3': {'pose': 'wave'}}})['shot_overrides'].get('portrait:5'), {'pose': 'wave'})

    def test_invalid_shot_overrides_reject_missing_and_malformed_references(self):
        for slot, override in [('portrait:13', {'pose': 'wave'}), ('unknown:1', {'pose': 'wave'}),
                ('portrait:1', []), ('portrait:1', {'pose': 'missing'}),
                ('portrait:1', {'pose': []}), ('portrait:1', {'pose': 'wave', 'expression': {}}),
                ('portrait:1', {'pose': 'wave', 'include_hands': ['head']}),
                ('portrait:1', {'pose': 'wave', 'include_hands': [{}]}),
                ('portrait:1', {'pose': 'wave', 'support': []}),
                ('portrait:1', {'pose': 'wave', 'azimuth': 181}),
                ('portrait:1', {'pose': 'wave', 'azimuth': []}),
                ('portrait:1', {'pose': 'wave', 'arbitrary': 3})]:
            with self.subTest(slot=slot, override=override), self.assertRaises(ValueError):
                core.validate_library({'shot_overrides': {slot: override}})

    def test_imported_pose_ik_policy_is_validated_and_backwards_compatible(self):
        self.assertEqual(core.validate_library({})['pose_options'], {})
        valid = {'poses': {'imported': {}}, 'pose_options': {'imported': {'leg_mode': 'KEEP'}}}
        self.assertEqual(core.validate_library(valid)['pose_options'], valid['pose_options'])
        for invalid in ({'missing': {'leg_mode': 'KEEP'}}, {'imported': {'leg_mode': []}},
                        {'imported': {'leg_mode': 'AUTO'}}, {'imported': {}}):
            with self.assertRaises(ValueError):
                core.validate_library({'poses': {'imported': {}}, 'pose_options': invalid})

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
