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
parser.add_argument('--pose')
parser.add_argument('--expression')
parser.add_argument('--projection', choices=('ORTHO', 'PERSP'))
parser.add_argument('--aspect', choices=('SQUARE', 'PORTRAIT', 'LANDSCAPE'))
parser.add_argument('--group', choices=('portrait', 'half_body', 'full_body', 'outfit_detail'), default='portrait')
parser.add_argument('--plan-index', type=int, choices=range(1, 10))
args = parser.parse_args()
args.source = args.source.resolve()
args.output = args.output.resolve()
args.output.mkdir(parents=True, exist_ok=True)
failed = []
for game in args.games:
    blend = args.source / game / 'optimized.blend'
    if not blend.is_file():
        raise FileNotFoundError(blend)
    print('START', game, '3 views', flush=True)
    extra = [part for flag, value in (('--pose', args.pose), ('--expression', args.expression), ('--projection', args.projection),
                                      ('--aspect', args.aspect), ('--group', args.group), ('--plan-index', str(args.plan_index) if args.plan_index else None)) if value for part in (flag, value)]
    with (args.output / (game + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run([args.blender, '--background', str(blend), '--python-exit-code', '1',
                                 '--python', str(ROOT / 'tools/test_three_views.py'), '--', '--game', game,
                                 '--output', str(args.output), '--resolution', args.resolution, *extra],
                                stdout=log, stderr=subprocess.STDOUT)
    print('END', game, result.returncode, flush=True)
    if result.returncode:
        failed.append(game)
raise SystemExit(bool(failed))
