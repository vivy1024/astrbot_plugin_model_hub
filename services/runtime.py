"""与 AstrBot 运行时交互：定位 core_lifecycle、重载流水线 / 插件。全部做成可注入，便于单测。"""

from __future__ import annotations

import gc
import inspect
from typing import Any

_lifecycle_cache: Any = None


def _looks_like_lifecycle(obj: Any) -> bool:
    return (
        type(obj).__name__ == "AstrBotCoreLifecycle"
        and callable(getattr(obj, "reload_pipeline_scheduler", None))
    )


def find_core_lifecycle() -> Any:
    """插件 Context 不暴露 core_lifecycle；先尝试当前 Web 请求的 app.state，再退回一次性 gc 扫描并缓存。"""
    global _lifecycle_cache
    if _lifecycle_cache is not None:
        return _lifecycle_cache
    try:
        from astrbot.api.web import request

        raw = getattr(request._get_current(), "_request", None)
        candidate = getattr(getattr(getattr(raw, "app", None), "state", None), "core_lifecycle", None)
        if _looks_like_lifecycle(candidate):
            _lifecycle_cache = candidate
            return candidate
    except Exception:
        pass
    for obj in gc.get_objects():
        try:
            if _looks_like_lifecycle(obj):
                _lifecycle_cache = obj
                return obj
        except Exception:
            continue
    return None


async def maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class AstrBotRuntime:
    """生产实现：插件 Star 构造时注入 context。"""

    def __init__(self, context: Any):
        self.context = context

    async def reload_pipeline(self, conf_id: str) -> str:
        lifecycle = find_core_lifecycle()
        if lifecycle is None:
            return "pending"
        await lifecycle.reload_pipeline_scheduler(conf_id)
        return "pipeline"

    async def reload_plugin(self, plugin_name: str) -> str:
        manager = getattr(self.context, "_star_manager", None)
        reload = getattr(manager, "reload", None)
        if not callable(reload):
            return "pending"
        result = await maybe_await(reload(plugin_name))
        if isinstance(result, tuple) and result and result[0] is False:
            raise RuntimeError(str(result[1] if len(result) > 1 else "插件重载失败"))
        return "reload"
