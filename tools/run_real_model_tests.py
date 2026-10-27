"""Launch isolated Blender processes for the six local models, with per-model logs."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--phase', default='three_views', choices=('audit', 'three_views', 'preview', 'full'))
parser.add_argument('--output', required=True)
parser.add_argument('--games', nargs='+', required=True, help='Only the affected games to test')
parser.add_argument('--resolution', default='512')
args = parser.parse_args()
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
failures = []
for game in args.games:
    command = ['D:/Program Files/Blender 5.2/blender.exe', '--background', '--python-exit-code', '1',
               '--python', str(ROOT / 'tools/test_real_models.py'), '--', '--game', game,
               '--output', str(output), '--phase', args.phase, '--resolution', args.resolution]
    print('START', game, args.phase, flush=True)
    with (output / (game + '_' + args.phase + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    print('END', game, 'exit', result.returncode, flush=True)
    if result.returncode:
        failures.append(game)
print('FAILED_GAMES', failures, flush=True)
sys.exit(bool(failures))
