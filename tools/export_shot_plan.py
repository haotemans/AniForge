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
lines = ['# AniForge 42 张拍摄编排', '',
         '由 `python tools/export_shot_plan.py` 从插件当前编排生成。正式输出肖像12张、半身12张、全身9张、服装细节9张，每个位置只拍一张；不为每个 Pose 重复三视图。', '',
         '肖像/半身先应用当前模型的AnimChar动作及表情，保留捕捉的头部与手型，再根据最终姿势定位镜头。角度相对角色正面；肖像默认 1:1 方形，其余 3:4 竖幅透视，背景纯白。', '',
         '肖像和半身各为4张标准视图＋8张特色照；全身保留两张坐姿。衣服只跟随骨骼蒙皮，不运行自动物理。每个动作需检查手型、衣服穿模和遮挡。', '']
for group, label in zip(core.GROUPS, ('肖像', '半身', '全身', '穿着服装的细节')):
    lines += [f'## {label} · {len(core.SHOT_PLANS[group])} 张', '', '| 序号 | 镜头 | 动作 | 表情 | 环绕 / 俯仰 | 取景 | 来源 |',
              '| --- | --- | --- | --- | --- | --- | --- |']
    for shot in core.recipe():
        if shot['group'] != group:
            continue
        gesture = '，保留手势' if shot['include_hands'] else ''
        source = '[AnimChar](animchar_workflow.md)' if shot.get('pose_provider') else '旧模板，待模型验收'
        if group == 'half_body' and shot['index'] > 4:
            source += f' · [PR规范](halfbody_pose_{shot["index"]:02}.md)'
        lines.append(f'| {shot["index"]} | {shot["title"]} | {shot.get("action_label", core.POSE_LABELS[shot["pose"]])} | '
                     f'{core.EXPRESSION_LABELS[shot["expression"]]} | {shot["azimuth"]}° / {shot["elevation"]}° | {shot["focus"]}{gesture} | {source} |')
    lines += ['']
lines += ['## 使用与验收', '',
          '肖像/半身使用[AnimChar工作流](animchar_workflow.md)，未绑定时不以旧程序化手型补齐。标准视图共用其自然站姿，特色半身按PR参考图，特色肖像可按语义生成。', '',
          '肖像为85 mm、1:1；半身为75 mm、3:4；均白底平视。实际动作以当前模型保存的AnimChar结果为准，头部/指节不再叠加旧模板。', '',
          '新导入VPD保留为用户预设，不自动覆盖这两组PR镜头。全身/服装旧模板本次保留。每个位置只有一张正式输出，开发三视图不增加正式数量；均须检查手型、服装与遮挡。', '']
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text('\n'.join(lines), encoding='utf-8')
print(args.output)
