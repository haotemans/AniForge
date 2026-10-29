"""Export the current 16-view recipe; no Blender and no rendering."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('core', ROOT / 'mmd_lora_studio/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=ROOT / 'docs/shot_plan.md')
args = parser.parse_args()
lines = ['# AniForge 16 张 T Pose 四视图', '',
         '所有镜头共用唯一 T Pose 与自然面部；没有动作或表情变体。下半身为腰胯至脚底，包含鞋子。', '',
         '| 范围 | 序号 | 视角 | 水平角度 |', '| --- | --- | --- | --- |']
for shot in core.recipe():
    lines.append(f'| {core.GROUP_LABELS[shot["group"]]} | {shot["index"]:02} | {shot["title"]} | {shot["azimuth"]}° |')
lines += ['', '由 `python tools/export_shot_plan.py` 生成。四组各4张，共16张；开发出图检查仍限正、侧、背3张。', '']
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text('\n'.join(lines), encoding='utf-8')
print(args.output)
