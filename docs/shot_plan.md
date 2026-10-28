# AniForge 42 张拍摄编排

由 `python tools/export_shot_plan.py` 从插件当前编排生成。正式输出肖像12张、半身12张、全身9张、服装细节9张，每个位置只拍一张；不为每个 Pose 重复三视图。

肖像/半身先应用当前模型的AnimChar动作及表情，保留捕捉的头部与手型，再根据最终姿势定位镜头。角度相对角色正面；肖像默认 1:1 方形，其余 3:4 竖幅透视，背景纯白。

肖像和半身各为4张标准视图＋8张特色照；全身保留两张坐姿。衣服只跟随骨骼蒙皮，不运行自动物理。每个动作需检查手型、衣服穿模和遮挡。

## 肖像 · 12 张

| 序号 | 镜头 | 动作 | 表情 | 环绕 / 俯仰 | 取景 | 来源 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 标准正面大头照 | 自然站姿 | 中性 | 0° / 0° | HEAD | [AnimChar](animchar_workflow.md) |
| 2 | 标准左侧面 | 自然站姿 | 中性 | 90° / 0° | HEAD | [AnimChar](animchar_workflow.md) |
| 3 | 标准右侧面 | 自然站姿 | 中性 | -90° / 0° | HEAD | [AnimChar](animchar_workflow.md) |
| 4 | 标准头部背面 | 自然站姿 | 中性 | 180° / 0° | HEAD | [AnimChar](animchar_workflow.md) |
| 5 | 比耶 Wink | 脸侧剪刀手 | 眨单眼 | 15° / 0° | HEAD，保留手势 | [AnimChar](animchar_workflow.md) |
| 6 | 惊喜 | 猫爪手势 | 惊讶 | -10° / 0° | HEAD，保留手势 | [AnimChar](animchar_workflow.md) |
| 7 | 甜美笑容 | 侧身立绘 | 微笑 | -15° / 0° | HEAD | [AnimChar](animchar_workflow.md) |
| 8 | 害羞 | 害羞收手 | 微笑 | 12° / 0° | HEAD，保留手势 | [AnimChar](animchar_workflow.md) |
| 9 | 调皮点脸 | 托腮思考 | 眨单眼 | -10° / 0° | HEAD，保留手势 | [AnimChar](animchar_workflow.md) |
| 10 | 傲娇神情 | 单手叉腰 | 生气 | 18° / 0° | HEAD | [AnimChar](animchar_workflow.md) |
| 11 | 元气开心 | 元气举手 | 开心 | -8° / 0° | HEAD，保留手势 | [AnimChar](animchar_workflow.md) |
| 12 | 温柔回眸 | 背手前倾 | 微笑 | -40° / 0° | HEAD | [AnimChar](animchar_workflow.md) |

## 半身 · 12 张

| 序号 | 镜头 | 动作 | 表情 | 环绕 / 俯仰 | 取景 | 来源 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 标准正面半身 | 自然站姿 | 中性 | 0° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) |
| 2 | 标准左侧半身 | 自然站姿 | 中性 | 90° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) |
| 3 | 标准右侧半身 | 自然站姿 | 中性 | -90° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) |
| 4 | 标准背面半身 | 自然站姿 | 中性 | 180° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) |
| 5 | 单手叉腰·自信浅笑 | 单手叉腰 | 微笑 | 30° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) · [PR规范](halfbody_pose_05.md) |
| 6 | 向镜头比耶·开心笑脸 | 向镜头比耶 | 开心笑脸（睁眼） | 15° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) · [PR规范](halfbody_pose_06.md) |
| 7 | 腰前轻交叠·温柔浅笑 | 腰前轻交叠 | 微笑 | 0° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) · [PR规范](halfbody_pose_07.md) |
| 8 | 掌心向上邀请·友好微笑 | 掌心向上邀请 | 微笑 | 45° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) · [PR规范](halfbody_pose_08.md) |
| 9 | 轻触耳侧·甜美微笑 | 轻触耳侧 | 微笑 | 55° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) · [PR规范](halfbody_pose_09.md) |
| 10 | 侧后回眸·单手轻抬 | 侧后回眸、单手轻抬 | 微笑 | 135° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) · [PR规范](halfbody_pose_10.md) |
| 11 | 背向单臂斜上举 | 背向单臂斜上举 | 中性 | 160° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) · [PR规范](halfbody_pose_11.md) |
| 12 | 正后双臂轻展 | 正后双臂轻展 | 中性 | 180° / 0° | UPPER，保留手势 | [AnimChar](animchar_workflow.md) · [PR规范](halfbody_pose_12.md) |

## 全身 · 9 张

| 序号 | 镜头 | 动作 | 表情 | 环绕 / 俯仰 | 取景 | 来源 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 正面立绘 | 自然站姿 | 中性 | 0° / 0° | FULL | 旧模板，待模型验收 |
| 2 | 端坐全身 | 端坐 | 微笑 | -25° / 3° | FULL | 旧模板，待模型验收 |
| 3 | 叉腰立绘 | 单手叉腰 | 微笑 | 30° / -2° | FULL | 旧模板，待模型验收 |
| 4 | 侧坐全身 | 侧坐 | 中性 | 35° / 2° | FULL | 旧模板，待模型验收 |
| 5 | 元气举手 | 元气举手 | 开心 | -15° / -2° | FULL | 旧模板，待模型验收 |
| 6 | 张臂立绘 | 张开双臂 | 微笑 | 22° / 0° | FULL | 旧模板，待模型验收 |
| 7 | 背手立绘 | 背手前倾 | 微笑 | -40° / 1° | FULL | 旧模板，待模型验收 |
| 8 | 侧面轮廓 | 服装展示 | 中性 | 90° / 0° | FULL | 旧模板，待模型验收 |
| 9 | 背面服饰 | 服装展示 | 中性 | 180° / 0° | FULL | 旧模板，待模型验收 |

## 穿着服装的细节 · 9 张

| 序号 | 镜头 | 动作 | 表情 | 环绕 / 俯仰 | 取景 | 来源 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 领口与颈饰 | 服装展示 | 中性 | 0° / 3° | COLLAR | 旧模板，待模型验收 |
| 2 | 胸前与肩饰 | 服装展示 | 中性 | -25° / 0° | CHEST | 旧模板，待模型验收 |
| 3 | 左袖与手套 | 服装展示 | 中性 | 38° / 0° | SLEEVE_LEFT | 旧模板，待模型验收 |
| 4 | 右袖与手套 | 服装展示 | 中性 | -38° / 0° | SLEEVE_RIGHT | 旧模板，待模型验收 |
| 5 | 腰带与腰饰 | 服装展示 | 中性 | 15° / 2° | WAIST | 旧模板，待模型验收 |
| 6 | 下装与侧饰 | 服装展示 | 中性 | -45° / 0° | HIPS | 旧模板，待模型验收 |
| 7 | 下摆与腿部 | 服装展示 | 中性 | 0° / -2° | HEM | 旧模板，待模型验收 |
| 8 | 背部上装 | 服装展示 | 中性 | 180° / 0° | BACK_UPPER | 旧模板，待模型验收 |
| 9 | 鞋靴与足饰 | 服装展示 | 中性 | 25° / 4° | SHOES | 旧模板，待模型验收 |

## 使用与验收

肖像/半身使用[AnimChar工作流](animchar_workflow.md)，未绑定时不以旧程序化手型补齐。标准视图共用其自然站姿，特色半身按PR参考图，特色肖像可按语义生成。

肖像为85 mm、1:1；半身为75 mm、3:4；均白底平视。实际动作以当前模型保存的AnimChar结果为准，头部/指节不再叠加旧模板。

新导入VPD保留为用户预设，不自动覆盖这两组PR镜头。全身/服装旧模板本次保留。每个位置只有一张正式输出，开发三视图不增加正式数量；均须检查手型、服装与遮挡。
