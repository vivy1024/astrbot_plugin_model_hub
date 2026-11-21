# astrbot_plugin_model_hub · 模型总控台

在一个地方查看和切换 AstrBot 本体与所有插件用到的大模型。

## 功能

- **自动发现模型设置**：不写死任何插件。
  - AstrBot 本体：每份配置文件里的对话模型、回退链、上下文压缩、图片转述、群聊图片转述、语音识别 / 合成（来自 AstrBot 自身的配置元数据）。
  - 插件：`_conf_schema.json` 里 `_special` 为 `select_provider*` 的字段。
  - 向量模型默认隐藏（换向量模型通常要重建索引）。
- **一键全切**：把所有已填写的对话类模型切到同一个模型。
  - 留空的设置保持留空（留空在各插件里通常表示「跟随上级 / 用默认」），可勾选一起写死。
  - 回退链只有显式填写时才改；语音与向量模型不动。
- **方案**：
  - 全切方案：保存「目标模型 + 可选回退链」，以后新装的插件也会被覆盖到。
  - 快照方案：把当前每一项的值原样存下，适合精细组合（例如压缩单独用小模型）。
  - 页面会标出当前与哪个方案一致。
- **逐项设置**：每个设置一个下拉框，统一保存；引用了未加载 Provider 的设置会标出来，写入前会校验 Provider 是否存在。

## 改完怎么生效

所有改动都写回各自的配置文件，然后：

| 对象 | 方式 |
|---|---|
| AstrBot 配置文件 | 重载该配置的消息流水线（与 AstrBot 配置页保存相同） |
| 插件实现了 `model_hub_apply(changes)` | 交给插件自己热更新 |
| 内置适配的插件（meme_manager、video_reference_vision、qq_group_daily_analysis、wave_memory） | 原地同步它们缓存的模型 ID，不重载 |
| 其他插件 | 热重载该插件（与 AstrBot 配置页保存相同），可在配置里改为只改共享配置 |

### 让你的插件接入

插件实例上实现一个方法即可（可以是协程），参数是 `{配置点分路径: 新值}`，返回值会显示在结果里：

```python
async def model_hub_apply(self, changes: dict) -> list[str]:
    self.my_client.provider_id = self.config["llm"]["provider_id"]
    return ["my_client"]
```

## 使用

- WebUI：AstrBot 插件页面 → 模型总控台。
- 聊天指令（管理员）：
  - `/模型总控`：查看当前各处的对话类模型与方案
  - `/模型总控 全切 <provider_id>`
  - `/模型总控 方案 <方案名>`

## 配置项

| 键 | 说明 |
|---|---|
| `unknown_plugin_apply` | 未适配插件的生效方式：`reload`（默认）/ `inplace` |
| `force_reload_plugins` | 即使有内置适配也改走热重载的插件 |
| `excluded_plugins` | 不接管的插件 |
| `include_embedding_slots` | 显示向量模型设置（一键全切永远不改它们） |
| `enable_chat_command` | 启用 `/模型总控` |
| `presets` | 已保存的方案（由页面维护） |

## 开发

```bash
python -m pytest tests -q   # 不依赖 astrbot 包
```

要求 AstrBot >= 4.17（插件页面 `pages/` 与 `register_web_api`）。

## 许可

AGPL-3.0
