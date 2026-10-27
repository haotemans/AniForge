import importlib.util
import tempfile
import unittest
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
            self.assertEqual(len({s['azimuth'] for s in group_shots}), 9)

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
