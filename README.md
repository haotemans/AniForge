# AniForge · T Pose 四视图渲染

Blender 4.2+ / 5.2 的 MMD 角色三渲二与训练图片输出插件。当前版本 **0.10.2**。

每个角色只使用一个 T Pose，面部保持自然状态。正式输出固定为 **16 张**：

| 范围 | 数量 | 视角 |
| --- | --- | --- |
| 肖像 | 4 | 正面 0°、左侧 90°、右侧 -90°、背面 180° |
| 半身 | 4 | 同上 |
| 全身 | 4 | 同上 |
| 下半身 | 4 | 同上，腰胯至脚底，包含鞋子 |

保留六款游戏的材质风格、贴图重连与匹配、头发高光、脸部补光、眼部与饰件透明层处理、EEVEE 白底渲染、镜头控制、PNG、同名训练标签、运行清单和中止恢复。已移除其他动作、手型、表情预设、VPD 导入、AnimChar 生成与镜头动作绑定。无需 AnimChar 账号或服务。

## 安装与使用

运行 `python tools/build_zip.py`，在 Blender 偏好设置中从磁盘安装 `dist/mmd_lora_studio-0.10.2.zip`。升级时关闭 Blender，用新包替换旧的 `mmd_lora_studio` 文件夹，避免残留旧模块；重新启动后生效。

1. 使用 MMD Tools 导入 PMX/PMD，打开侧栏 **MMD LoRA**。
2. 展开 **1 角色与材质**：选择角色骨架，或选中角色网格后点击吸管识别，选择后自动创建或复用一个相机并切换为白底，选择游戏风格并应用三渲二。贴图修复和材质微调默认收起。
3. 展开 **2 构图与预览**：按钮选择肖像/半身/全身/下半身及正面/左侧/右侧/背面，设置画幅和投影。切换取景、视角或镜头参数会自动更新相机；“刷新自动相机”可手动同步且不出图，“渲染当前视图”仅输出1张。高级镜头与镜头预设默认收起。
4. 展开 **3 渲染与输出**：设置角色触发词、渲染长边和输出目录，确认画布尺寸，点击“开始渲染 · 全部16张”。目录旁的文件夹按钮可打开已有输出目录。附加标签与开发检查默认收起。

三个步骤为可折叠面板，无需切换功能页。保留现有材质与镜头参数，旧工程的镜头预设会保留。

输出固定为纯白 RGB PNG，默认按角色的透明轮廓裁掉多余白边，保留 3% 安全留白。白发、白衣、细发丝和半透明装饰都参与轮廓判断。裁剪只收缩画布，不拉伸角色、不重新补成方图，因此最终尺寸和宽高比随主体变化；“渲染长边”表示裁剪前的画布长边。在“高级镜头”可调整输出留白，也可关闭自动裁剪以保留原画幅。每张图的实际尺寸和裁剪框会写入清单。中间透明图片会在处理完成后清理。

T Pose 会先清除旧骨骼偏移，再根据角色正面方向把双臂伸直至水平；A Pose 模型也会展开双臂。渲染期间临时停用骨骼约束和已有布料、软体、刚体模拟，结束、取消或失败时恢复用户状态。面部归零依据 MMD 面部分类和常用形态键名称，保留体型与服装开关。已有 Action/NLA 动画需先在角色副本中解除，插件不会删除用户动画。

旧工程中的动作和表情配置在读取时清理；不再读取历史本地动作缓存。源 PMX、贴图、现有工程和历史输出不被修改。

## 开发验证

普通检查不渲染图片：

```powershell
python -m unittest discover -s tests
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_smoke.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_static_render.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_tpose.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_output.py
```

需要出图测试时，默认仅对指定模型渲染正面、侧面、背面各一张：

```powershell
python tools/run_three_views.py --games WUWA --source test_output/three_views_040 --output test_output/tpose_three_views --group full_body
```

不在普通测试中运行 16 张完整输出；全量输出须显式点击或使用 `ml.render_dataset(test_only=False)`。详细清单见 [docs/shot_plan.md](docs/shot_plan.md)。
