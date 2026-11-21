"""模型总控台：集中查看 / 切换 AstrBot 本体与所有插件的大模型配置。"""

from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import request

from .services.hub import ModelHub
from .services.runtime import AstrBotRuntime

PLUGIN_NAME = "astrbot_plugin_model_hub"


def _core_metadata():
    try:
        from astrbot.core.config.default import CONFIG_METADATA_3

        return CONFIG_METADATA_3
    except Exception as exc:  # AstrBot 版本差异：拿不到元数据时只管理插件
        logger.warning(f"[ModelHub] 读取 AstrBot 配置元数据失败，本体槽位不可用: {exc}")
        return None


class ModelHubPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.hub = ModelHub(context, AstrBotRuntime(context), self.config, _core_metadata())
        routes = [
            ("state", self._api_state, ["GET"], "读取全部模型槽位、可用 Provider 与方案"),
            ("apply", self._api_apply, ["POST"], "逐项修改模型槽位"),
            ("switch-all", self._api_switch_all, ["POST"], "一键把对话类模型切到同一个模型"),
            ("presets/save", self._api_save_preset, ["POST"], "保存模型方案"),
            ("presets/delete", self._api_delete_preset, ["POST"], "删除模型方案"),
            ("presets/apply", self._api_apply_preset, ["POST"], "应用模型方案"),
        ]
        for route, handler, methods, desc in routes:
            context.register_web_api(f"/{PLUGIN_NAME}/{route}", handler, methods, desc)

    @staticmethod
    async def _body() -> dict:
        data = await request.json(default={})
        return data if isinstance(data, dict) else {}

    async def _api_state(self):
        return self.hub.snapshot()

    async def _api_apply(self):
        body = await self._body()
        changes = body.get("changes")
        if not isinstance(changes, dict):
            return {"ok": False, "error": "changes 必须是 {槽位ID: 值} 对象"}
        return await self.hub.apply(changes, label="逐项修改")

    async def _api_switch_all(self):
        body = await self._body()
        return await self.hub.switch_all(
            str(body.get("model") or ""), body.get("fallbacks"), bool(body.get("include_empty")),
        )

    async def _api_save_preset(self):
        body = await self._body()
        return self.hub.save_preset(
            str(body.get("name") or ""),
            model=str(body.get("model") or ""),
            fallbacks=body.get("fallbacks"),
            include_empty=bool(body.get("include_empty")),
            capture_current=bool(body.get("capture_current")),
            note=str(body.get("note") or ""),
        )

    async def _api_delete_preset(self):
        return self.hub.delete_preset(str((await self._body()).get("name") or ""))

    async def _api_apply_preset(self):
        return await self.hub.apply_preset(str((await self._body()).get("name") or ""))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("模型总控")
    async def model_hub_command(self, event: AstrMessageEvent):
        """/模型总控 [全切 <provider_id> | 方案 <方案名>]"""
        if not self.config.get("enable_chat_command", True):
            return
        parts = event.message_str.strip().split()
        if parts and parts[0].lstrip("/").startswith("模型总控"):
            parts = parts[1:]
        if len(parts) >= 2 and parts[0] in ("全切", "切换"):
            result = await self.hub.switch_all(parts[1])
        elif len(parts) >= 2 and parts[0] in ("方案", "预设"):
            result = await self.hub.apply_preset(" ".join(parts[1:]))
        else:
            yield event.plain_result(self.hub.describe())
            return
        text = result.get("message") or ""
        if result.get("error"):
            text = f"{text}\n失败：{result['error']}".strip()
        yield event.plain_result(text)
