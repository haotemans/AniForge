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
    def test_halfbody_prs_define_neutral_four_views_and_eight_distinct_photos(self):
        shots = [s for s in core.recipe() if s['group'] == 'half_body']
        self.assertEqual([s['azimuth'] for s in shots], [0, 90, -90, 180, 30, 15, 0, 45, 55, 135, 160, 180])
        self.assertTrue(all(s['expression'] == 'neutral' and s['performance']['head_angles'] == [0, 0, 0] for s in shots[:4]))
        self.assertTrue(all(s['pose_provider'] == 'AnimCharTools' for s in shots))
        self.assertEqual(shots[5]['expression'], 'happy_open')
        self.assertTrue(all(s['performance']['head_angles'] == [0, 0, 0] for s in shots[10:]))
        self.assertEqual([s['reference_pr'] for s in shots[4:]], list(range(3, 11)))

    def test_missing_animchar_pose_never_falls_back_to_legacy_template(self):
        library = core.empty_library()
        shots = core.resolve_shot_assets(core.recipe(), library, 'a'*24)
        self.assertEqual(sum(s.get('asset_status') == 'MISSING' for s in shots), 24)
        for shot in shots[:24]:
            with self.assertRaises(ValueError):
                core.require_shot_asset(shot)
        with self.assertRaises(ValueError):
            core.require_shot_asset(core.recipe()[0])

    def test_animchar_binding_is_model_specific_and_keeps_captured_fingers_and_head(self):
        library = core.empty_library()
        key = 'a'*24
        library['poses']['captured'] = {}
        asset = dict(pose='captured', rig=key, slot='half_body:5', provider='AnimCharTools', reference='local reference', reviewed=False)
        library['shot_assets'][key+'|half_body:5'] = asset
        library = core.validate_library(library)
        shot = core.resolve_shot_assets(core.recipe(), library, key)[16]
        self.assertEqual(shot['pose'], 'captured')
        self.assertEqual(shot['performance']['hand_pose'], 'KEEP')
        self.assertEqual(shot['performance']['head_angles'], [0, 0, 0])
        self.assertEqual(shot['asset_status'], 'CAPTURED')
        core.require_shot_asset(shot)
        wrong_model = core.resolve_shot_assets(core.recipe(), library, 'b'*24)[16]
        self.assertEqual(wrong_model['asset_status'], 'MISSING')

    def test_shared_animchar_neutral_pose_fills_eight_standard_views(self):
        library = core.empty_library(); key = 'a'*24
        library['poses']['standing'] = {}
        library['shot_assets'][key+'|neutral'] = dict(pose='standing', rig=key, slot='neutral', provider='AnimCharTools', reference='body_stand', reviewed=True)
        shots = core.resolve_shot_assets(core.recipe(), library, key)
        references = [s for s in shots if s.get('asset_slot') == 'neutral']
        self.assertEqual(len(references), 8)
        self.assertTrue(all(s['pose'] == 'standing' and s['asset_status'] == 'REVIEWED' for s in references))

    def test_old_halfbody_overrides_are_preserved_without_replacing_new_standard_views(self):
        old = dict(schema_version=2, shot_overrides={'half_body:3': {'pose': 'hand_hip'}, 'portrait:5': {'pose': 'peace_face'}})
        library = core.validate_library(old)
        self.assertEqual(library['schema_version'], 3)
        self.assertEqual(library['legacy_shot_overrides']['half_body:3'], old['shot_overrides']['half_body:3'])
        self.assertNotIn('half_body:3', library['shot_overrides'])
        self.assertIn('portrait:5', library['shot_overrides'])
        self.assertEqual(core.validate_library(library), library)
        with self.assertRaises(ValueError):
            core.validate_library(dict(schema_version=2, shot_overrides={'half_body:10': {'pose': 'relaxed'}}))

    def test_halfbody05_is_the_documented_hand_on_hip_photo(self):
        shot = next(s for s in core.recipe() if s['group'] == 'half_body' and s['index'] == 5)
        self.assertEqual((shot['pose'], shot['expression']), ('hand_hip', 'smile'))
        self.assertEqual((shot['azimuth'], shot['elevation']), (30, 0))
        self.assertEqual(shot['camera']['camera_lens'], 75)
        self.assertEqual(shot['camera']['camera_aspect'], 'PORTRAIT')
        self.assertEqual(shot['performance']['expression_strength'], .4)
        self.assertEqual(shot['include_hands'], ['left_wrist', 'right_wrist'])
        self.assertEqual(len(core.recipe()), 42)

    def test_three_views_are_the_default_test_budget(self):
        shots = core.three_view_recipe()
        self.assertEqual([s['view'] for s in shots], ['front', 'side', 'back'])
        self.assertEqual([s['azimuth'] for s in shots], [0, 90, 180])
        self.assertTrue(all(s['expression'] == 'neutral' for s in shots))
        with self.assertRaises(ValueError):
            core.three_view_recipe('unknown')

    def test_balanced_unique_recipe(self):
        shots = core.recipe()
        self.assertEqual(len(shots), 42)
        for group in core.GROUPS:
            group_shots = [s for s in shots if s['group'] == group]
            self.assertEqual([s['index'] for s in group_shots], list(range(1, 13)) if group in {'portrait', 'half_body'} else list(range(1, 10)))
            self.assertEqual(len({s['title'] for s in group_shots}), 12 if group in {'portrait', 'half_body'} else 9)
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
        self.assertEqual(len(shots), 42)
        self.assertTrue(all(s['pose'] == settings.pose and s['expression'] == settings.expression for s in shots))
        self.assertTrue(all('performance' not in s and 'include_hands' not in s for s in shots))
        self.assertTrue(all(s['camera']['camera_aspect'] == 'SQUARE' for s in shots if s['group'] == 'portrait'))
        effective = core.ShotSettings(settings, core.recipe()[0])
        self.assertEqual(effective.camera_lens, 85)
        self.assertEqual(settings.camera_lens, 40)
        self.assertEqual(effective.hand_pose, 'AUTO')
        self.assertEqual(settings.hand_pose, 'KEEP')

    def test_42_photos_cover_character_information_without_pose_triplication(self):
        report = core.dataset_coverage(core.recipe())
        self.assertEqual(report['total'], 42)
        self.assertEqual(report['per_group'], {'portrait': 12, 'half_body': 12, 'full_body': 9, 'outfit_detail': 9})
        self.assertEqual(report['seated_photos'], 2)
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

    def test_local_pose_slots_preserve_captured_gestures_and_42_photo_budget(self):
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
        self.assertEqual(core.dataset_coverage(shots)['per_group'], {'portrait': 12, 'half_body': 12, 'full_body': 9, 'outfit_detail': 9})
        self.assertEqual(shots[30:], original[30:])
        self.assertEqual(core.dataset_coverage(shots)['seated_photos'], 2)
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
