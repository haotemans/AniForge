"""Export the source recipe as readable project documentation; no Blender/render."""
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
lines = ['# AniForge 36 张拍摄编排', '',
         '由 `python tools/export_shot_plan.py` 从插件当前编排生成。固定四组各九张；无出图预览和三视图测试不扩展为全量渲染。', '',
         '每张先应用姿势、表情和头部/手型，再根据变形后的模型定位镜头。角度相对角色正面；默认 3:4 竖幅透视。', '',
         '姿势为站立、躯干及上肢模板，尚无坐/蹲/行走解算。服装区域按骨架与身高估算，需检查不同服饰、发丝和披风遮挡。', '']
for group, label in zip(core.GROUPS, ('肖像', '半身', '全身', '穿着服装的细节')):
    lines += [f'## {label} · 9 张', '', '| 序号 | 镜头 | 姿势 | 表情 | 环绕 / 俯仰 | 取景 |',
              '| --- | --- | --- | --- | --- | --- |']
    for shot in core.recipe():
        if shot['group'] != group:
            continue
        gesture = '，保留手势' if shot['include_hands'] else ''
        lines.append(f'| {shot["index"]} | {shot["title"]} | {core.POSE_LABELS[shot["pose"]]} | '
                     f'{core.EXPRESSION_LABELS[shot["expression"]]} | {shot["azimuth"]}° / {shot["elevation"]}° | {shot["focus"]}{gesture} |')
    lines += ['']
lines += ['## 姿势设计参考', '',
          '- [CLIP STUDIO：动作线、姿势平衡与节奏](https://www.clipstudio.net/how-to-draw/archives/156164)',
          '- [CLIP STUDIO TIPS：肩胯反向倾斜、构图与镜头](https://tips.clip-studio.com/en-us/articles/6073)', '',
          '内置模板为程序化设计，不包含第三方姿势文件。用户已有 VPD 可导入并保存，按实际模型检查手型和服装。', '']
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text('\n'.join(lines), encoding='utf-8')
print(args.output)
