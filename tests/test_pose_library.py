"""Filename discovery uses generated empty files, never third-party pose data."""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'mmd_lora_studio'
package = types.ModuleType('_pose_library_tests')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__+'.pose_library', ROOT / 'pose_library.py')
poses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poses)


class LocalPoseTests(unittest.TestCase):
    def test_mojibake_is_fixed_only_in_display(self):
        broken = '傾僀僪儖偭傐偄億乕僘/傐偄1.vpd'
        self.assertEqual(poses.display_filename(broken), 'アイドルっぽいポーズ/ぽい1.vpd')
        self.assertEqual(poses.display_filename('我的姿势/坐姿.vpd'), '我的姿势/坐姿.vpd')

    def test_single_and_paired_discovery_and_reference_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = ['アイドルっぽいポーズ/ぽい1.vpd', 'アイドルっぽいポーズ/ぽい1.png',
                     'ハートポーズ/天使のハート/天使の羽ハート(胸).VPD',
                     'ハートポーズ/ふたりではーと/left.vpd', 'notes.txt']
            for name in paths:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            entries = poses.scan_pose_folder(root)
            self.assertEqual(len(entries), 3)
            self.assertEqual(sum(e['paired'] for e in entries), 1)
            self.assertEqual({e['role'] for e in entries if not e['paired']}, {'idol_peace', 'heart_chest'})
            self.assertTrue(next(e for e in entries if e['role'] == 'idol_peace')['preview'].endswith('.png'))
            self.assertEqual(sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()), sorted(paths))

    def test_folder_errors_are_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                poses.scan_pose_folder(root / 'missing')
            for index in range(257):
                (root / f'{index}.vpd').touch()
            with self.assertRaises(ValueError):
                poses.scan_pose_folder(root)

    def test_secondary_hair_and_clothing_are_not_retargeted_as_human_controls(self):
        for name in ('全ての親', '右足ＩＫ', '左つま先ＩＫ', '右親指０', '左腕捩3', '上半身2', '左足D'):
            self.assertTrue(poses.humanoid_control(name), name)
        for name in ('右袖', '右ツインテ1', 'ネクタイ1', '左横スカート', '左ねこみみ', '左胸１'):
            self.assertFalse(poses.humanoid_control(name), name)


if __name__ == '__main__':
    unittest.main()
