"""模型槽位发现：AstrBot 本体配置元数据 + 各插件 _conf_schema 里声明的 Provider 选择字段。

不写死任何插件或角色：凡是 schema 里 ``_special`` 为 ``select_provider*`` 的字段都是一个槽位。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

# _special -> 槽位类别
KIND_BY_SPECIAL = {
    "select_provider": "chat",
    "select_providers": "chat_chain",
    "select_provider_stt": "stt",
    "select_provider_tts": "tts",
    "select_provider_embedding": "embedding",
}
CHAT_KINDS = frozenset({"chat", "chat_chain"})


def kind_of(special: Any) -> str | None:
    if not isinstance(special, str) or not special.startswith("select_provider"):
        return None
    return KIND_BY_SPECIAL.get(special, special.removeprefix("select_provider_") or "chat")


@dataclass(frozen=True)
class SlotSpec:
    id: str
    scope: str  # core | plugin
    target: str  # core: conf_id；plugin: 插件名
    target_label: str
    key: str  # 配置内的点分路径
    kind: str
    title: str
    hint: str = ""
    section: str = ""
    is_list: bool = False


def get_path(data: Any, dotted: str) -> Any:
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


def set_path(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def iter_core_fields(metadata: Any, section: str = "") -> Iterator[tuple[str, Mapping[str, Any], str]]:
    """遍历 AstrBot ``CONFIG_METADATA_3``：其 items 的键本身就是点分绝对路径。"""
    if not isinstance(metadata, Mapping):
        return
    for key, node in metadata.items():
        if not isinstance(node, Mapping):
            continue
        if kind_of(node.get("_special")):
            yield str(key), node, section
            continue
        label = node.get("description") or node.get("name") or section
        for child_key in ("metadata", "items"):
            if isinstance(node.get(child_key), Mapping):
                yield from iter_core_fields(node[child_key], str(label))


def iter_schema_fields(schema: Any, prefix: str = "", section: str = "") -> Iterator[tuple[str, Mapping[str, Any], str]]:
    """遍历插件 ``_conf_schema.json``：object 嵌套用 items，路径逐级拼接；template_list 内部不进入。"""
    if not isinstance(schema, Mapping):
        return
    for key, node in schema.items():
        if not isinstance(node, Mapping):
            continue
        path = f"{prefix}{key}"
        if kind_of(node.get("_special")):
            yield path, node, section
        elif node.get("type") == "object" and isinstance(node.get("items"), Mapping):
            yield from iter_schema_fields(node["items"], f"{path}.", str(node.get("description") or section))


def _make_slot(scope: str, target: str, target_label: str, key: str, node: Mapping[str, Any], section: str) -> SlotSpec:
    kind = kind_of(node.get("_special")) or "chat"
    return SlotSpec(
        id=f"{scope}:{target}:{key}",
        scope=scope,
        target=target,
        target_label=target_label,
        key=key,
        kind=kind,
        title=str(node.get("description") or key),
        hint=str(node.get("hint") or ""),
        section=section,
        is_list=kind == "chat_chain" or node.get("type") == "list",
    )


def discover_core_slots(core_metadata: Any, confs: Iterable[tuple[str, str]]) -> list[SlotSpec]:
    """confs: (conf_id, 显示名)。同一组字段对每份 AstrBot 配置各生成一份槽位。"""
    fields: dict[str, tuple[Mapping[str, Any], str]] = {}
    for key, node, section in iter_core_fields(core_metadata):
        fields.setdefault(key, (node, section))
    return [
        _make_slot("core", conf_id, label, key, node, section)
        for conf_id, label in confs
        for key, (node, section) in fields.items()
    ]


def discover_plugin_slots(plugin_name: str, plugin_label: str, schema: Any) -> list[SlotSpec]:
    return [_make_slot("plugin", plugin_name, plugin_label, key, node, section) for key, node, section in iter_schema_fields(schema)]


def normalize_value(slot: SlotSpec, raw: Any) -> str | list[str]:
    if slot.is_list:
        if isinstance(raw, str):
            items = [x.strip() for x in raw.replace("，", ",").replace("\n", ",").split(",")]
        elif isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw]
        else:
            items = []
        return list(dict.fromkeys(x for x in items if x))
    return "" if raw is None else str(raw).strip()


def provider_ids_of(value: str | list[str]) -> list[str]:
    """单值字段也可能写成「a, b」回退链（例如 WaveMemory 的模型分工）。"""
    if isinstance(value, list):
        return value
    return [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
