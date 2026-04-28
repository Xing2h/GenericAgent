# Vision API SOP

## ⚠️ 前置规则（必须遵守）

1. **先枚举窗口**：调用 vision 前必须先用 `pygetwindow` 枚举窗口标题，确认目标窗口存在且已激活到前台。窗口不存在就不要截图。
2. **🚫 禁止全屏截图**：必须先利用ljqCtrl截取窗口区域。能截局部（如标题栏）就不截整窗口，能截窗口就绝不全屏。全屏截图在任何场景下都不允许。
3. **能不用 vision 就不用**：如果窗口标题/本地 OCR（`ocr_utils.py`）能获取所需信息，就不要调用 vision API，省 token 且更可靠。Vision 是最后手段。

## 快速用法

```python
from vision_api import ask_vision
result = ask_vision(image, prompt="描述图片内容", timeout=60, max_pixels=1_440_000)
# image: 文件路径(str/Path) 或 PIL Image
# backend: 默认 'modelscope'；也可显式传 'openai' / 'claude'
# 返回 str：成功为模型回复，失败为 'Error: ...'（错误信息会脱敏）
```

## 当前实现（2026-04-24 已测试）

- `memory/vision_api.py` 保持入口 `ask_vision(image_input, prompt, timeout, max_pixels, backend)`；默认 `DEFAULT_BACKEND='modelscope'`。
- ModelScope 走 OpenAI-compatible Chat Completions：`https://api-inference.modelscope.cn/v1/chat/completions`，模型 `Qwen/Qwen3-VL-235B-A22B-Instruct`，图片以 `data:image/jpeg;base64,...` 放入 `image_url`。
- ModelScope token 读取顺序：环境变量 `MODELSCOPE_API_KEY` → 文件内本机常量 `MODELSCOPE_API_KEY` → `keychain.keys.modelscope_api_key.use()`；禁止在记忆/日志中输出 token 明文。
- 已用 `py_compile` 验证语法，并用小图 `vision_rewrite_test.png` 测试默认 `ask_vision(...)` 成功返回图片文字 `OK7`。
- 若连续调用返回 429，是 ModelScope 模型限流；等待后重试，勿误判为代码错误。

## 如果没有 `vision_api.py`，初次构建vision能力

1. 复制 `memory/vision_api.template.py` → `memory/vision_api.py`
2. 只改头部"用户配置区"：去 `mykey.py` 里扫描变量名（⚠️ 只看名字，禁止输出 apikey 值），尝试找能用配置名填入 `CLAUDE_CONFIG_KEY` / `OPENAI_CONFIG_KEY`，`DEFAULT_BACKEND` 选后端，并测试
3. 保底：没有可用 config 时去 `https://modelscope.cn/my/myaccesstoken` 申请 token；优先存入 `keychain.keys.modelscope_api_key` 或环境变量 `MODELSCOPE_API_KEY`，不要把 token 明文写进记忆/日志。
4. ModelScope 使用 `Authorization: Bearer <token>`；若返回 401 `Please bind your Alibaba Cloud account before use`，说明 token 被识别但账号未绑定阿里云，需用户完成绑定或更换已绑定账号 token 后再测。
