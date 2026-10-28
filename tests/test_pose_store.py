import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pose_store', Path(__file__).resolve().parents[1]/'mmd_lora_studio/pose_store.py')
store = importlib.util.module_from_spec(spec); spec.loader.exec_module(store)


class PoseStoreTests(unittest.TestCase):
    key = 'a'*24

    def fixture(self, slot='half_body:10'):
        asset = dict(pose='Saved native result', rig=self.key, slot=slot, provider='AnimCharTools', reference='local reference', reviewed=False)
        pose = {'head': [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}
        return asset, pose, {'leg_mode': 'FK'}

    def test_saved_result_survives_reload_and_keeps_quality_state(self):
        with tempfile.TemporaryDirectory() as root:
            asset, pose, options = self.fixture()
            asset['review_note'] = 'needs garment review'
            path = store.save_result(root, self.key, asset, pose, options, 'renamable data name')
            self.assertTrue(path.is_file())
            loaded, issues = store.load_results(root, self.key)
            self.assertEqual(issues, [])
            self.assertEqual(loaded['half_body:10']['asset'], asset)
            self.assertEqual(loaded['half_body:10']['pose'], pose)

    def test_each_slot_is_independent(self):
        with tempfile.TemporaryDirectory() as root:
            for slot in ('half_body:5', 'half_body:10', 'portrait:10', 'neutral'):
                store.save_result(root, self.key, *self.fixture(slot))
            self.assertEqual(len(store.load_results(root, self.key)[0]), 4)
            self.assertEqual(store.load_results(root, 'b'*24), ({}, []))

    def test_bad_file_uses_last_valid_backup(self):
        with tempfile.TemporaryDirectory() as root:
            asset, pose, options = self.fixture()
            path = store.save_result(root, self.key, asset, pose, options)
            asset['reviewed'] = True
            store.save_result(root, self.key, asset, pose, options)
            path.write_text('{broken', encoding='utf8')
            loaded, issues = store.load_results(root, self.key)
            self.assertFalse(loaded['half_body:10']['asset']['reviewed'])
            self.assertEqual(len(issues), 1)

    def test_one_damaged_slot_does_not_hide_other_results(self):
        with tempfile.TemporaryDirectory() as root:
            path = store.save_result(root, self.key, *self.fixture())
            store.save_result(root, self.key, *self.fixture('half_body:5'))
            path.write_text('{}', encoding='utf8')
            loaded, issues = store.load_results(root, self.key)
            self.assertEqual(set(loaded), {'half_body:5'})
            self.assertEqual(len(issues), 1)

    def test_atomic_write_failure_preserves_saved_pose(self):
        with tempfile.TemporaryDirectory() as root:
            asset, pose, options = self.fixture()
            path = store.save_result(root, self.key, asset, pose, options)
            old = path.read_bytes(); asset['reviewed'] = True
            with patch.object(store.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    store.save_result(root, self.key, asset, pose, options)
            self.assertEqual(path.read_bytes(), old)
            self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_invalid_paths_model_and_nonfinite_matrices_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            for slot in ('../escape', 'half_body:13', None):
                asset, pose, options = self.fixture(slot)
                with self.assertRaises((ValueError, TypeError)):
                    store.save_result(root, self.key, asset, pose, options)
            asset, pose, options = self.fixture()
            pose['head'][0][0] = float('nan')
            with self.assertRaises(ValueError):
                store.save_result(root, self.key, asset, pose, options)


if __name__ == '__main__':
    unittest.main()
