"""插件热生效适配。

优先级：
1. 插件实例自己实现 ``model_hub_apply(changes: dict[str, Any])``（可为协程）→ 交给插件自己处理；
2. 下表内置适配：已知会把 Provider ID 缓存到实例属性的插件，原地同步缓存，不重载插件；
3. 其余插件按策略：``reload``（与 AstrBot 配置页「保存」相同，热重载插件）或 ``inplace``（只改共享配置对象）。

内置适配只碰对方插件的公开属性名，属性不存在时静默跳过；对方插件升级改名后会退回到「仅改共享配置」，
这种情况在页面上会显示为 inplace，可把该插件加入 force_reload_plugins 改走重载。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .discovery import set_path

Adapter = Callable[[Any, Mapping[str, Any]], list[str]]

HOOK_NAME = "model_hub_apply"


def _meme_manager(star: Any, changes: Mapping[str, Any]) -> list[str]:
    notes: list[str] = []
    if "semantic.vision_provider_id" in changes:
        value = changes["semantic.vision_provider_id"]
        if hasattr(star, "semantic_vision_provider_id"):
            star.semantic_vision_provider_id = value
            notes.append("semantic_vision_provider_id")
        stm_cfg = getattr(getattr(star, "semantic_task_manager", None), "config", None)
        if isinstance(stm_cfg, dict):
            stm_cfg["vision_provider_id"] = value
            notes.append("semantic_task_manager.config")
    if "semantic.embedding_provider_id" in changes:
        value = changes["semantic.embedding_provider_id"]
        if hasattr(star, "semantic_embedding_provider_id"):
            star.semantic_embedding_provider_id = value
            notes.append("semantic_embedding_provider_id")
        stm_cfg = getattr(getattr(star, "semantic_task_manager", None), "config", None)
        if isinstance(stm_cfg, dict):
            stm_cfg["embedding_provider_id"] = value
    if "auto_collect.vision_provider_id" in changes:
        acm = getattr(star, "auto_collect_manager", None)
        if acm is not None and hasattr(acm, "vision_provider_id"):
            acm.vision_provider_id = changes["auto_collect.vision_provider_id"]
            notes.append("auto_collect_manager.vision_provider_id")
    if "generation.emotion.llm.provider_id" in changes and hasattr(star, "emotion_llm_provider_id"):
        star.emotion_llm_provider_id = changes["generation.emotion.llm.provider_id"]
        notes.append("emotion_llm_provider_id")
    return notes


def _merged_config_copy(star: Any, changes: Mapping[str, Any]) -> list[str]:
    """插件把配置合并成了自己的 dict（如 video_reference_vision），把改动同步进那份副本。"""
    cfg = getattr(star, "config", None)
    if not isinstance(cfg, dict):
        return []
    for key, value in changes.items():
        set_path(cfg, key, value)
    return ["star.config"]


def _wave_memory(star: Any, changes: Mapping[str, Any]) -> list[str]:
    routing = getattr(star, "llm_routing", None)
    cfg = getattr(star, "config", None)
    if routing is None or not isinstance(cfg, Mapping):
        return []
    notes: list[str] = []
    if "tag_llm_provider_id" in changes:
        value = cfg.get("tag_llm_provider_id", "")
        if hasattr(routing, "_tag_primary"):
            routing._tag_primary = value
        if hasattr(star, "tag_llm_provider_id"):
            star.tag_llm_provider_id = value
        notes.append("llm_routing.tag")
    if any(key.startswith("MetaThinking_Settings.") for key in changes) and hasattr(routing, "_meta_cfg"):
        meta = cfg.get("MetaThinking_Settings")
        routing._meta_cfg = meta if isinstance(meta, Mapping) else {}
        notes.append("llm_routing.meta")
    reload = getattr(routing, "reload", None)
    if callable(reload):
        reload(cfg)
        notes.append("llm_routing.reload")
    return notes


def _noop(star: Any, changes: Mapping[str, Any]) -> list[str]:
    return ["shared-config"]


# 插件名或目录名 -> 适配函数。只有「改共享配置对象不够」或「重载代价过高」的插件才需要登记。
BUILTIN_ADAPTERS: dict[str, Adapter] = {
    "meme_manager": _meme_manager,
    "astrbot_plugin_meme_manager": _meme_manager,
    "astrbot_plugin_video_reference_vision": _merged_config_copy,
    # ConfigManager 持有同一个 AstrBotConfig，按需读取，改共享对象即生效
    "astrbot_plugin_qq_group_daily_analysis": _noop,
    "astrbot_plugin_wave_memory": _wave_memory,
}


def find_adapter(*names: str) -> Adapter | None:
    for name in names:
        if name and name in BUILTIN_ADAPTERS:
            return BUILTIN_ADAPTERS[name]
    return None
