# API 图片打标

最终标签为插件本地拼接：`@3d, 角色名, API图片详细描述`。角色名由用户填写，不让模型猜测，也不作为文字字段发往API。

每张图片对应一个同目录、同文件名的UTF-8 TXT，例如：

```text
mingyin_portrait_01.png
mingyin_portrait_01.txt
```

TXT内容示例仅说明格式，实际描述来自配置的视觉模型：

```text
@3d, 明音, A front-facing 3D anime character with long blonde hair, pink eyes, a dark headband, blue musical-note hair clips, a white collared shirt and a black choker. ...
```

## 配置

在“渲染与输出”填写角色名，开启“渲染后API打标”，展开“API打标与标签”：

- **Base URL**、**API Key**、**视觉模型**由用户填写。接口需兼容多图Chat Completions和JSON对象输出。
- Base URL示例：`https://your-provider.example/v1`；也可填写完整的`/chat/completions`地址。没有内置付费账户或默认供应商。
- Key以密码框显示，仅保存在本机Blender偏好设置，不写入角色工程、清单、缓存或源码。“保存API设置”不会请求模型。
- 可选择英文或中文描述，默认英文。描述关注当前图可见的外观、服装、饰物、表情、动作、取景及背景，禁止补写当前视角看不到的部位。

接口图像输入形式参考[官方多图与Base64输入说明](https://developers.openai.com/api/docs/guides/images-vision)。本插件仅依赖兼容的HTTP协议，不安装SDK。

## 请求与响应

原始4K图片保持不变。插件在内存中保持比例缩放并补白为1024×1024，压缩为PNG/Base64；不生成额外缩略图文件。

单个请求最多8张，16张完整输出分成8＋8两批；单张预览或不足8张的末批按实际数量发送。JSON请求包含`model`、`messages`、`response_format: {"type":"json_object"}`和`max_tokens: 4096`，图片位于用户消息的`image_url`数据URI中，逐张附带不含角色名和文件路径的ID。

模型返回的描述内容须为：

```json
{
  "images": [
    {"id": "image_001", "description": "Only a detailed description of this image."},
    {"id": "image_002", "description": "Only a detailed description of the second image."}
  ]
}
```

兼容返回`choices[0].message.content`内的JSON，或直接返回以上对象。响应顺序可以改变，插件按ID匹配原图；缺图、重复ID、错误ID、空描述或截断结果会使本批失败，不把描述错配到其他图片。

## 自动打标与补打标

渲染前检查配置，渲染结束后再请求描述。API模式下不会用简单模板冒充详细描述：成功后才生成TXT，失败时保留PNG，在界面和清单中标明失败。

“补打标目录”可选择一次渲染的输出目录，或直接包含图片的目录，点击“打标已有图片／重试失败项”，不重新渲染。留空时使用当前工程记住的最近一次输出目录。为避免混用不同角色的名字，不自动递归扫描多个角色的输出目录。

有效描述保存于输出目录的`caption_cache.json`，按原图内容、接口/模型、语言和提示版本核对。改角色名只在本地更新TXT前缀；原图与接口配置未变时复用成功描述，不重复请求API。失败项可单独重试。Key不参与缓存。

交互模式使用后台线程请求网络，界面显示完成数和失败数。“当前批次后停止打标”会保存当前批次结果，然后停止后续请求。关闭插件或打开其他工程时停止跟踪旧任务。普通回归测试只使用本机模拟HTTP服务，不访问用户接口、不渲染图片。
