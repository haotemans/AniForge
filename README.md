# AniForge · T Pose 四视图渲染

Blender 4.2+ / 5.2 的 MMD 角色三渲二与训练图片输出插件。当前版本 **0.10.0**。

每个角色只使用一个 T Pose，面部保持自然状态。正式输出固定为 **16 张**：

| 范围 | 数量 | 视角 |
| --- | --- | --- |
| 肖像 | 4 | 正面 0°、左侧 90°、右侧 -90°、背面 180° |
| 半身 | 4 | 同上 |
| 全身 | 4 | 同上 |
| 下半身 | 4 | 同上，腰胯至脚底，包含鞋子 |

保留六款游戏的材质风格、贴图重连与匹配、头发高光、脸部补光、眼部与饰件透明层处理、EEVEE 白底渲染、镜头控制、PNG、同名训练标签、运行清单和中止恢复。已移除其他动作、手型、表情预设、VPD 导入、AnimChar 生成与镜头动作绑定。无需 AnimChar 账号或服务。

## 安装与使用

运行 `python tools/build_zip.py`，在 Blender 偏好设置中从磁盘安装 `dist/mmd_lora_studio-0.10.0.zip`。升级时关闭 Blender，用新包替换旧的 `mmd_lora_studio` 文件夹，避免残留旧模块；重新启动后生效。

1. 使用 MMD Tools 导入 PMX/PMD，在侧栏 **MMD LoRA** 选择角色骨架。
2. 在“材质”选择游戏风格，按需重连贴图，应用三渲二。
3. 在“四视图”选择范围与视角，预览相机不出图。“应用唯一 T Pose”支持撤销。
4. 在“镜头”调整正面方向、投影、焦距、画幅、留白和移轴；可保存、应用、导入和导出镜头预设，旧版镜头预设会保留。
5. 在“输出”设置触发词、标签、分辨率和目录，点击“生成 16 张四视图 + 标签”。单张预览只输出选中的一张。

T Pose 会先清除旧骨骼偏移，再根据角色正面方向把双臂伸直至水平；A Pose 模型也会展开双臂。渲染期间临时停用骨骼约束和已有布料、软体、刚体模拟，结束、取消或失败时恢复用户状态。面部归零依据 MMD 面部分类和常用形态键名称，保留体型与服装开关。已有 Action/NLA 动画需先在角色副本中解除，插件不会删除用户动画。

旧工程中的动作和表情配置在读取时清理；不再读取历史本地动作缓存。源 PMX、贴图、现有工程和历史输出不被修改。

## 开发验证

普通检查不渲染图片：

```powershell
python -m unittest discover -s tests
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_smoke.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_static_render.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_tpose.py
```

需要出图测试时，默认仅对指定模型渲染正面、侧面、背面各一张：

```powershell
python tools/run_three_views.py --games WUWA --source test_output/three_views_040 --output test_output/tpose_three_views --group full_body
```

不在普通测试中运行 16 张完整输出；全量输出须显式点击或使用 `ml.render_dataset(test_only=False)`。详细清单见 [docs/shot_plan.md](docs/shot_plan.md)。
