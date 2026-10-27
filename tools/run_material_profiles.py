import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--games', nargs='+', default=['WUWA', 'ZZZ', 'ENDFIELD', 'GENSHIN'])
parser.add_argument('--output', required=True)
parser.add_argument('--resolution', default='1024')
parser.add_argument('--full-comparison', action='store_true', help='Explicit opt-in to the historical multi-image A/B suite')
args = parser.parse_args()
if not args.full_comparison:
    parser.error('For ordinary tests use run_three_views.py. This suite requires --full-comparison.')
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
failed = []
for game in args.games:
    print('START', game, flush=True)
    blend = ROOT / 'test_output/real_models_20261028_final' / game / 'tested_full.blend'
    with (output / (game + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(['D:/Program Files/Blender 5.2/blender.exe', '--background', str(blend),
                                 '--python-exit-code', '1', '--python', str(ROOT / 'tools/test_material_profiles.py'), '--',
                                '--game', game, '--output', str(output), '--resolution', args.resolution, '--full-comparison'],
                                stdout=log, stderr=subprocess.STDOUT)
    print('END', game, result.returncode, flush=True)
    if result.returncode:
        failed.append(game)
print('FAILED', failed, flush=True)
raise SystemExit(bool(failed))
