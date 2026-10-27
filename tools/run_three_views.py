"""Default local QA: three views per explicitly selected affected game."""
import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--games', nargs='+', required=True)
parser.add_argument('--source', type=Path, required=True, help='Folder with GAME/optimized.blend subdirectories')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--blender', default=os.environ.get('BLENDER_EXECUTABLE') or shutil.which('blender') or 'D:/Program Files/Blender 5.2/blender.exe')
parser.add_argument('--resolution', default='1024')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
failed = []
for game in args.games:
    blend = args.source / game / 'optimized.blend'
    if not blend.is_file():
        raise FileNotFoundError(blend)
    print('START', game, '3 views', flush=True)
    with (args.output / (game + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run([args.blender, '--background', str(blend), '--python-exit-code', '1',
                                 '--python', str(ROOT / 'tools/test_three_views.py'), '--', '--game', game,
                                 '--output', str(args.output), '--resolution', args.resolution],
                                stdout=log, stderr=subprocess.STDOUT)
    print('END', game, result.returncode, flush=True)
    if result.returncode:
        failed.append(game)
raise SystemExit(bool(failed))
