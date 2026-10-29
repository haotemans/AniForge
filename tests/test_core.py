import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('core', Path(__file__).resolve().parents[1] / 'mmd_lora_studio/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class RecipeTests(unittest.TestCase):
    def test_exactly_sixteen_four_views(self):
        shots = core.recipe()
        self.assertEqual(len(shots), 16)
        self.assertEqual(core.GROUPS, ('portrait', 'half_body', 'full_body', 'lower_body'))
        for group in core.GROUPS:
            group_shots = [s for s in shots if s['group'] == group]
            self.assertEqual([s['index'] for s in group_shots], [1, 2, 3, 4])
            self.assertEqual([s['azimuth'] for s in group_shots], [0, 90, -90, 180])
            self.assertEqual([s['view'] for s in group_shots], ['front', 'left', 'right', 'back'])

    def test_only_tpose_and_neutral_face(self):
        self.assertEqual({s['pose'] for s in core.recipe()}, {'tpose'})
        self.assertEqual({s['expression'] for s in core.recipe()}, {'neutral'})
        self.assertTrue(all('pose_provider' not in s and 'performance' not in s for s in core.recipe()))

    def test_ordinary_test_budget_stays_three(self):
        for group in core.GROUPS:
            self.assertEqual([s['azimuth'] for s in core.three_view_recipe(group)], [0, 90, 180])
        with self.assertRaises(ValueError):
            core.three_view_recipe('outfit_detail')

    def test_recipe_has_no_shared_mutation(self):
        plan = core.recipe(); plan[0]['pose'] = 'old'
        self.assertEqual(core.recipe()[0]['pose'], 'tpose')

    def test_captions_describe_the_visible_crop(self):
        self.assertIn('lower body, legs, feet', core.caption('character', 'lower_body', 'back'))
        self.assertIn('from behind', core.caption('character', 'portrait', 'back'))
        self.assertNotIn('t-pose', core.caption('character', 'portrait', 'front'))
        self.assertIn('t-pose', core.caption('character', 'full_body', 'left'))

    def test_coverage(self):
        self.assertEqual(core.dataset_coverage(core.recipe())['per_group'], dict.fromkeys(core.GROUPS, 4))

    def test_camera_migration_discards_all_poses_and_expressions(self):
        old = {'poses': {'old': {}}, 'expressions': {'old': {}},
               'cameras': {'lens': {'preview_group': 'outfit_detail', 'camera_lens': 75}}}
        result = core.validate_camera_library(old)
        self.assertEqual(set(result), {'schema_version', 'cameras'})
        self.assertEqual(result['cameras']['lens']['preview_group'], 'lower_body')
        self.assertEqual(result['cameras']['lens']['camera_lens'], 75)
        with self.assertRaises(ValueError):
            core.validate_camera_library({'cameras': {'bad': {'camera_lens': float('nan')}}})

    def test_safe_paths_and_manifest(self):
        self.assertNotIn('/', core.safe_name('../../test:character'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'manifest.json'
            core.write_json(path, {'status': 'running'})
            core.write_json(path, {'status': 'complete'})
            self.assertIn('complete', path.read_text())
            self.assertFalse(path.with_suffix('.json.tmp').exists())


if __name__ == '__main__':
    unittest.main()
