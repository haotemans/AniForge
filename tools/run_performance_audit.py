import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--blender', default=os.environ.get('BLENDER_EXECUTABLE') or shutil.which('blender') or 'D:/Program Files/Blender 5.2/blender.exe')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
failed = []
for game in ('HSR', 'ZZZ', 'GENSHIN', 'WUWA', 'ENDFIELD', 'NEVERNESS'):
    blend = ROOT / 'test_output/real_models_20261028_final' / game / 'tested_full.blend'
    if game in {'ZZZ', 'WUWA'}:
        blend = ROOT / 'test_output/three_views_040' / game / 'optimized.blend'
    print('AUDIT', game, 'no renders', flush=True)
    with (args.output / (game + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run([args.blender, '--background', str(blend), '--python-exit-code', '1', '--python',
                                 str(ROOT / 'tools/audit_performance.py'), '--', '--game', game, '--output', str(args.output)], stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        failed.append(game)
    print('END', game, result.returncode, flush=True)
print('FAILED', failed, flush=True)
raise SystemExit(bool(failed))
