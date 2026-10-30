# AniForge · A Pose 四视图渲染

Blender 4.2+ / 5.2 的 MMD 角色三渲二与训练图片输出插件。当前版本 **0.12.5**。

每个角色只使用一个 A Pose，面部保持自然状态。正式输出固定为 **16 张**：

| 范围 | 数量 | 视角 |
| --- | --- | --- |
| 肖像 | 4 | 正面 0°、左侧 90°、右侧 -90°、背面 180° |
| 半身 | 4 | 同上 |
| 全身 | 4 | 同上 |
| 下半身 | 4 | 同上，大腿上部至脚底，包含完整鞋子 |

肖像默认聚焦完整头部，突出脸部、发型和头饰；下半身按腿脚骨骼关联的实际几何取景，突出双腿、袜面和鞋子，长发与裙摆不再把腿脚主体缩小。完整头部范围保留发顶及头饰，仍可切换为脸部特写。全身和半身沿用原构图。

下半身出图期间临时排除手臂和手部，避免画面角落或腿旁出现悬空残片；腿脚、头发与服装保留。混合身体网格使用临时副本，成功、取消或失败后恢复原网格、权重、形态键和可见性；其他取景与默认A Pose不变。

保留六款游戏的材质风格、贴图重连与匹配、头发高光、脸部补光、眼部与饰件透明层处理、EEVEE 白底渲染、镜头控制、PNG、同名训练标签、运行清单和中止恢复。已移除其他动作、手型、表情预设、VPD 导入、AnimChar 生成与镜头动作绑定。无需 AnimChar 账号或服务。

## 安装与使用

运行 `python tools/build_zip.py`，在 Blender 偏好设置中从磁盘安装 `dist/mmd_lora_studio-0.12.5.zip`。升级时关闭 Blender，用新包替换旧的 `mmd_lora_studio` 文件夹，避免残留旧模块；重新启动后生效。

1. 使用 MMD Tools 导入 PMX/PMD，打开侧栏 **MMD LoRA**。
2. 展开 **1 角色与材质**：选择角色骨架，或选中角色网格后点击吸管识别，选择后自动创建或复用一个相机并切换为白底，选择游戏风格并应用三渲二。贴图修复和材质微调默认收起。
3. 展开 **2 构图与预览**：按钮选择肖像/半身/全身/下半身及正面/左侧/右侧/背面，设置画幅和投影。切换取景、视角或镜头参数会自动更新相机；“刷新自动相机”可手动同步且不出图，“渲染当前视图”仅输出1张。高级镜头与镜头预设默认收起。
4. 展开 **3 渲染与输出**：设置角色触发词、渲染长边和输出目录，确认画布尺寸，点击“开始渲染 · 全部16张”。目录旁的文件夹按钮可打开已有输出目录。API打标配置与开发检查默认收起。填写角色名并启用“渲染后API打标”后，可用自填接口生成每张图片的详细描述。

标签格式为 `@3d, 角色名, API图片详细描述`，每张图对应一个同目录同名TXT。API模式固定batch=1，逐张顺序请求，16张待打标图片分成16次请求；已有成功缓存继续复用。图片仅在内存中按原比例缩小，最长边不超过1024，不拉伸、不补方图，也不放大小图，原4K图不变，按JSON图片ID解析描述。支持已有图片补打标、成功结果缓存、失败项重试，以及改角色名后只在本地更新前缀。Base URL、Key和视觉模型由用户填写，详见 [API打标说明](docs/caption_api.md)。

接口支持HTTP与HTTPS，以及自定义端口和`/v1`路径，按用户填写的地址请求。“另选目录”留空时，补打标默认跟随上方当前输出目录：目录直接有图片就使用它，否则定位当前角色最新一次有图片的渲染子目录；不混扫其他角色，也不误用其他输出位置的旧记录。需要单独处理旧图时仍可另选目录。

每张图片的JSON元数据附带独立镜头提示，例如“下半身细节、背面”，优先读取该图清单里的取景与实际镜头角度。提示只辅助观察，实际图像优先；不得按“背面”提示编造看不到的脸、眼睛、胸部或手。未识别的图片不猜测方向，也不从文件序号03猜测背面/右侧。角色名、文件名与路径不发给API；修改某张图的镜头提示时只重新请求这一张的描述。

每次渲染创建一个角色文件夹，16张PNG和16个同名TXT全部平铺存放，不再创建肖像、半身、全身、下半身四个子目录。文件名保留取景类型和序号，例如 `角色名_lower_body_03.png` 与 `角色名_lower_body_03.txt`。清单、API缓存和临时文件放在角色文件夹外的同级 `.aniforge/` 中，图片目录只包含图片和TXT。已有旧目录仍可补打标，无需移动原图。

已移除手动“基础附加标签”输入及旧值。API默认生成按实际画面逐张核对的英文连贯描述：细节充分时约140–240词，稀疏画面可更短，不编造遮挡部分、身份、故事或图外信息；返回JSON的`caption`字段只含描述正文。固定前缀和角色名仍在本地添加。

三个步骤为可折叠面板，无需切换功能页。保留现有材质与镜头参数，旧工程的镜头预设会保留。

默认渲染长边为 **4096 px（4K）**：方形画布为4096×4096，竖幅为3072×4096，横幅为4096×3072。旧工程首次升级时同步为4096，之后手动选择的分辨率会保留。开发验证工具继续使用较小测试尺寸，不会自动渲染4K完整批次。

输出固定为纯白 RGB PNG，默认按角色的透明轮廓裁掉多余白边，保留 3% 安全留白。白发、白衣、细发丝和半透明装饰都参与轮廓判断。裁剪只收缩画布，不拉伸角色、不重新补成方图，因此最终尺寸和宽高比随主体变化；“渲染长边”表示裁剪前的画布长边。在“高级镜头”可调整输出留白，也可关闭自动裁剪以保留原画幅。每张图的实际尺寸和裁剪框会写入清单。中间透明图片会在处理完成后清理。

A Pose 会先清除旧骨骼偏移，再根据角色正面方向让双臂伸直、向下约45°；预览、姿势按钮和所有渲染共用这一姿势。渲染期间临时停用骨骼约束和已有布料、软体、刚体模拟，结束、取消或失败时恢复用户状态。面部归零依据 MMD 面部分类和常用形态键名称，保留体型与服装开关。已有 Action/NLA 动画需先在角色副本中解除，插件不会删除用户动画。

异环明音使用柔和的头发与颈部皮肤明暗，避免通用二档阴影造成大块灰褐色分界；眉睫归入眼部材质保留原底色。升级后在角色上重新点击一次“应用三渲二”更新已有材质，原模型与贴图不改动。

绝区零菲欧妮的前后头发和皮肤使用柔和法线补光，背光处保留底色，消除后脑的大块硬灰影；透明头发高光使用原色叠加，衣服仍保留二档明暗，减轻暗部以便观察背面结构。选择“绝区零”并开启“头发 / 脸部优化”，重新应用三渲二后生效。

菲欧妮的丝袜层保留原贴图、纹样与材质透明度，改用柔和明暗和Alpha混合，去除会把白色袜面推至过曝的原MMD加法球形高光。更改只作用于生成的绝区零材质，不覆盖源图、源PMX或原材质。

鸣潮莉莉补充“顔”“前髪”“後髪”“瞳”等日文材质识别，脸部改用柔和法线补光，消除下巴的通用硬灰影。“身体・脚”是皮肤与衣物共用的图集，鸣潮风格仅在其中明亮、低至中等饱和度的暖肤色区域混入柔和补光；深色束衣、冷色布料和高饱和度金属保留原二档明暗。此遮罩仅用于该明确材质类型，不改原贴图像素或UV。选择“鸣潮”并开启“头发 / 脸部优化”，重新应用三渲二后生效。

旧工程中的动作和表情配置在读取时清理；不再读取历史本地动作缓存。源 PMX、贴图、现有工程和历史输出不被修改。

## 开发验证

普通检查不渲染图片：

```powershell
python -m unittest discover -s tests
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_smoke.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_static_render.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_apose.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_framing.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_lower_body.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_output.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_modal.py
& 'D:/Program Files/Blender 5.2/blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_captioning.py
```

需要出图测试时，默认仅对指定模型渲染正面、侧面、背面各一张：

```powershell
python tools/run_three_views.py --games WUWA --source test_output/three_views_040 --output test_output/apose_three_views --group full_body
```

不在普通测试中运行 16 张完整输出；全量输出须显式点击或使用 `ml.render_dataset(test_only=False)`。详细清单见 [docs/shot_plan.md](docs/shot_plan.md)。
