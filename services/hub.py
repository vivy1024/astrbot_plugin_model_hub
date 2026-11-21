"""全栈模型总控：读取全部槽位、一键全切、方案（预设）管理、写入 + 热生效。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from .adapters import HOOK_NAME, find_adapter
from .discovery import (
    CHAT_KINDS,
    SlotSpec,
    discover_core_slots,
    discover_plugin_slots,
    get_path,
    normalize_value,
    provider_ids_of,
    set_path,
)
from .runtime import maybe_await

logger = logging.getLogger("astrbot")

SELF_NAMES = frozenset({"astrbot_plugin_model_hub"})
APPLY_STRATEGIES = ("reload", "inplace")


@dataclass
class Target:
    key: str  # core:<conf_id> | plugin:<name>
    scope: str
    name: str
    label: str
    config: Any
    star: Any = None
    aliases: tuple[str, ...] = ()
    slots: list[SlotSpec] = field(default_factory=list)


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


class ModelHub:
    def __init__(self, context: Any, runtime: Any, settings: Any, core_metadata: Any = None):
        self.context = context
        self.runtime = runtime
        self.settings = settings if settings is not None else {}
        self.core_metadata = core_metadata
        self._lock = asyncio.Lock()

    # ---------- 设置 ----------
    def _setting(self, key: str, default: Any) -> Any:
        value = self.settings.get(key, default) if isinstance(self.settings, Mapping) else default
        return default if value is None else value

    @property
    def excluded_plugins(self) -> set[str]:
        return set(_as_list(self._setting("excluded_plugins", []))) | SELF_NAMES

    @property
    def include_embedding(self) -> bool:
        return bool(self._setting("include_embedding_slots", False))

    @property
    def unknown_plugin_apply(self) -> str:
        value = str(self._setting("unknown_plugin_apply", "reload"))
        return value if value in APPLY_STRATEGIES else "reload"

    @property
    def force_reload_plugins(self) -> set[str]:
        return set(_as_list(self._setting("force_reload_plugins", [])))

    def _save_settings(self) -> None:
        save = getattr(self.settings, "save_config", None)
        if callable(save):
            save()

    # ---------- 发现 ----------
    def _core_confs(self) -> list[tuple[str, str, Any]]:
        acm = getattr(self.context, "astrbot_config_mgr", None)
        confs = getattr(acm, "confs", None)
        if not isinstance(confs, Mapping):
            return []
        names = getattr(acm, "abconf_data", None) or {}
        result = []
        for conf_id, conf in confs.items():
            meta = names.get(conf_id) if isinstance(names, Mapping) else None
            name = meta.get("name") if isinstance(meta, Mapping) else None
            label = str(name or ("默认配置" if conf_id == "default" else conf_id))
            result.append((str(conf_id), label, conf))
        result.sort(key=lambda item: (item[0] == "default", item[1]))
        return result

    def targets(self) -> list[Target]:
        targets: list[Target] = []
        if self.core_metadata is not None:
            confs = self._core_confs()
            slots = discover_core_slots(self.core_metadata, [(cid, label) for cid, label, _ in confs])
            for conf_id, label, conf in confs:
                targets.append(Target(
                    key=f"core:{conf_id}", scope="core", name=conf_id, label=f"AstrBot · {label}", config=conf,
                    slots=[s for s in slots if s.target == conf_id],
                ))
        excluded = self.excluded_plugins
        get_all = getattr(self.context, "get_all_stars", None)
        for meta in (get_all() if callable(get_all) else None) or ():
            name = str(getattr(meta, "name", "") or "")
            root = str(getattr(meta, "root_dir_name", "") or "")
            if not getattr(meta, "activated", True) or excluded & {name, root}:
                continue
            config = getattr(meta, "config", None)
            schema = getattr(config, "schema", None)
            if config is None or not schema:
                continue
            label = str(getattr(meta, "display_name", "") or name or root)
            slots = discover_plugin_slots(name or root, label, schema)
            if slots:
                targets.append(Target(
                    key=f"plugin:{name or root}", scope="plugin", name=name or root, label=label, config=config,
                    star=getattr(meta, "star_cls", None), aliases=(name, root), slots=slots,
                ))
        if not self.include_embedding:
            for target in targets:
                target.slots = [s for s in target.slots if s.kind != "embedding"]
        return [t for t in targets if t.slots]

    def providers(self) -> dict[str, list[dict[str, str]]]:
        getters = {
            "chat": "get_all_providers",
            "stt": "get_all_stt_providers",
            "tts": "get_all_tts_providers",
            "embedding": "get_all_embedding_providers",
        }
        result: dict[str, list[dict[str, str]]] = {}
        for kind, attr in getters.items():
            items: dict[str, dict[str, str]] = {}
            getter = getattr(self.context, attr, None)
            try:
                for prov in (getter() if callable(getter) else None) or ():
                    meta = prov.meta()
                    if meta.id and meta.id not in items:
                        items[meta.id] = {"id": str(meta.id), "model": str(getattr(meta, "model", "") or "")}
            except Exception as exc:
                logger.warning("[ModelHub] 读取 %s Provider 列表失败: %s", kind, exc)
            result[kind] = sorted(items.values(), key=lambda p: p["id"])
        result["chat_chain"] = result["chat"]
        return result

    @staticmethod
    def _read(target: Target, slot: SlotSpec) -> str | list[str]:
        return normalize_value(slot, get_path(target.config, slot.key))

    def _index(self) -> dict[str, tuple[Target, SlotSpec]]:
        return {slot.id: (target, slot) for target in self.targets() for slot in target.slots}

    # ---------- 状态 ----------
    def snapshot(self) -> dict[str, Any]:
        providers = self.providers()
        targets = self.targets()
        slots_payload = []
        for target in targets:
            for slot in target.slots:
                value = self._read(target, slot)
                known = {p["id"] for p in providers.get(slot.kind, [])}
                missing = [pid for pid in provider_ids_of(value) if known and pid not in known]
                slots_payload.append({**asdict(slot), "target_key": target.key, "value": value, "missing": missing})
        presets = self.presets()
        index = {s["id"]: s for s in slots_payload}
        for preset in presets:
            preset["matched"] = bool(index) and not self._plan_preset(preset, index)
        return {
            "ok": True,
            "generated_at": time.time(),
            "targets": [
                {"key": t.key, "scope": t.scope, "name": t.name, "label": t.label, "apply": self._apply_mode(t)}
                for t in targets
            ],
            "slots": slots_payload,
            "providers": providers,
            "presets": presets,
            "settings": {
                "include_embedding_slots": self.include_embedding,
                "unknown_plugin_apply": self.unknown_plugin_apply,
                "excluded_plugins": sorted(self.excluded_plugins - SELF_NAMES),
            },
        }

    def _apply_mode(self, target: Target) -> str:
        if target.scope == "core":
            return "pipeline"
        if target.name in self.force_reload_plugins:
            return "reload"
        if callable(getattr(target.star, HOOK_NAME, None)):
            return "hook"
        if find_adapter(*target.aliases):
            return "adapter"
        return self.unknown_plugin_apply

    # ---------- 规划 ----------
    def plan_switch_all(
        self,
        model: str,
        fallbacks: Any = None,
        include_empty: bool = False,
        index: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """把所有「对话类」槽位切到 model，只返回真正有变化的槽位。

        - 只动 kind=chat 的已填写槽位：留空在各插件里普遍表示「跟随上级 / 使用默认」，保持留空才不会破坏继承；
          include_empty=True 时连留空的一起写死。
        - 回退链（chat_chain）只在显式给出 fallbacks 时改写。
        - 语音 / 向量槽位不动。
        """
        model = str(model or "").strip()
        index = index if index is not None else {s["id"]: s for s in self.snapshot()["slots"]}
        chain = None if fallbacks is None else list(dict.fromkeys(_as_list(fallbacks)))
        plan: dict[str, Any] = {}
        for sid, state in index.items():
            if state["kind"] == "chat" and model:
                target_value = [model] if state["is_list"] else model
                if (state["value"] or include_empty) and state["value"] != target_value:
                    plan[sid] = target_value
            elif state["kind"] == "chat_chain" and chain is not None and state["value"] != chain:
                plan[sid] = chain
        return plan

    def _plan_preset(self, preset: Mapping[str, Any], index: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        plan: dict[str, Any] = {}
        if preset.get("model"):
            plan.update(self.plan_switch_all(
                preset["model"], preset.get("fallbacks"), bool(preset.get("include_empty")), index=index,
            ))
        for sid, raw in (preset.get("slots") or {}).items():
            state = index.get(sid)
            if state is None:
                continue
            value = normalize_value(_slot_from_state(state), raw)
            if state["value"] != value:
                plan[sid] = value
            else:
                plan.pop(sid, None)
        return plan

    # ---------- 方案 ----------
    def _preset_store(self) -> dict[str, dict[str, Any]]:
        raw = self._setting("presets", "{}")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            logger.warning("[ModelHub] presets 不是合法 JSON，已忽略")
            return {}
        if not isinstance(data, Mapping):
            return {}
        return {str(k): dict(v) for k, v in data.items() if isinstance(v, Mapping)}

    def _write_store(self, store: Mapping[str, Any]) -> None:
        self.settings["presets"] = json.dumps(store, ensure_ascii=False)
        self._save_settings()

    def presets(self) -> list[dict[str, Any]]:
        return [{"name": name, **body} for name, body in self._preset_store().items()]

    def save_preset(
        self,
        name: str,
        *,
        model: str = "",
        fallbacks: Any = None,
        include_empty: bool = False,
        capture_current: bool = False,
        note: str = "",
    ) -> dict[str, Any]:
        """两种方案：
        - 「全切」方案：model (+ fallbacks)，应用时按 plan_switch_all 计算，新装插件也会自动覆盖；
        - 「快照」方案：capture_current=True，把当前每个槽位的值原样存下，用于精细组合（如压缩用 medium）。
        """
        name = str(name or "").strip()
        if not name:
            return {"ok": False, "error": "方案名不能为空"}
        body: dict[str, Any] = {"note": str(note or "").strip(), "updated_at": time.time()}
        if capture_current:
            body["slots"] = {s["id"]: s["value"] for s in self.snapshot()["slots"] if s["kind"] != "embedding"}
        elif str(model or "").strip():
            body.update({"model": str(model).strip(), "include_empty": bool(include_empty)})
            if fallbacks is not None:
                body["fallbacks"] = _as_list(fallbacks)
        else:
            return {"ok": False, "error": "请指定目标模型，或选择保存当前配置"}
        store = self._preset_store()
        store[name] = body
        self._write_store(store)
        return {"ok": True, "message": f"已保存方案「{name}」"}

    def delete_preset(self, name: str) -> dict[str, Any]:
        store = self._preset_store()
        if store.pop(str(name), None) is None:
            return {"ok": False, "error": f"没有方案「{name}」"}
        self._write_store(store)
        return {"ok": True, "message": f"已删除方案「{name}」"}

    async def apply_preset(self, name: str) -> dict[str, Any]:
        preset = self._preset_store().get(str(name))
        if preset is None:
            return {"ok": False, "error": f"没有方案「{name}」"}
        index = {s["id"]: s for s in self.snapshot()["slots"]}
        return await self.apply(self._plan_preset(preset, index), label=f"方案「{name}」")

    async def switch_all(self, model: str, fallbacks: Any = None, include_empty: bool = False) -> dict[str, Any]:
        if not str(model or "").strip():
            return {"ok": False, "error": "请选择目标模型"}
        return await self.apply(self.plan_switch_all(model, fallbacks, include_empty), label=f"全部切到 {model}")

    # ---------- 写入 ----------
    async def apply(self, changes: Mapping[str, Any], label: str = "") -> dict[str, Any]:
        """先整体校验（任一槽位非法则一个都不写），再按配置对象分组：写入 → 落盘 → 热生效。"""
        async with self._lock:
            index = self._index()
            providers = self.providers()
            errors: list[str] = []
            grouped: dict[str, tuple[Target, dict[str, Any], list[str]]] = {}
            for sid, raw in (changes or {}).items():
                if sid not in index:
                    errors.append(f"未知槽位: {sid}")
                    continue
                target, slot = index[sid]
                value = normalize_value(slot, raw)
                known = {p["id"] for p in providers.get(slot.kind, [])}
                unknown = [pid for pid in provider_ids_of(value) if known and pid not in known]
                if unknown:
                    errors.append(f"{target.label} · {slot.title}: 未加载的 Provider {', '.join(unknown)}")
                    continue
                if value == self._read(target, slot):
                    continue
                entry = grouped.setdefault(target.key, (target, {}, []))
                entry[1][slot.key] = value
                entry[2].append(sid)
            if errors:
                return {"ok": False, "error": "；".join(errors), "errors": errors}
            if not grouped:
                return {"ok": True, "message": "没有需要变更的槽位", "applied": [], "targets": [], "state": self.snapshot()}

            reports = [await self._apply_target(t, values, sids) for t, values, sids in grouped.values()]
            failed = [r for r in reports if r.get("error")]
            applied = [sid for r in reports if r["persisted"] for sid in r["slots"]]
            summary = f"{label or '已更新'}：{len(applied)} 个槽位，涉及 {len(reports)} 处配置"
            if failed:
                summary += f"，{len(failed)} 处失败"
            logger.info("[ModelHub] %s", summary)
            return {
                "ok": not failed,
                "message": summary,
                "error": "；".join(f"{r['label']}: {r['error']}" for r in failed) or None,
                "applied": applied,
                "targets": reports,
                "state": self.snapshot(),
            }

    async def _apply_target(self, target: Target, values: Mapping[str, Any], sids: list[str]) -> dict[str, Any]:
        report: dict[str, Any] = {
            "key": target.key, "label": target.label, "slots": sids, "persisted": False, "hot": "", "notes": [],
        }
        try:
            for key, value in values.items():
                set_path(target.config, key, value)
            save = getattr(target.config, "save_config", None)
            if not callable(save):
                raise RuntimeError("配置对象不支持落盘")
            save()
            report["persisted"] = True
            if target.scope == "core":
                report["hot"] = await self.runtime.reload_pipeline(target.name)
                return report
            mode = self._apply_mode(target)
            if mode == "hook":
                report["notes"] = list(await maybe_await(getattr(target.star, HOOK_NAME)(dict(values))) or [])
            elif mode == "adapter" and target.star is not None:
                report["notes"] = find_adapter(*target.aliases)(target.star, values)
            elif mode == "reload":
                mode = await self.runtime.reload_plugin(target.name)
            elif target.star is None:
                mode = "pending"
            report["hot"] = mode
        except Exception as exc:
            logger.exception("[ModelHub] 应用 %s 失败", target.label)
            report["error"] = str(exc)
        return report

    # ---------- 文本摘要（聊天指令） ----------
    def describe(self) -> str:
        state = self.snapshot()
        lines: list[str] = []
        for target in state["targets"]:
            slots = [s for s in state["slots"] if s["target_key"] == target["key"] and s["kind"] in CHAT_KINDS]
            if not slots:
                continue
            lines.append(f"【{target['label']}】")
            for s in slots:
                value = ", ".join(s["value"]) if isinstance(s["value"], list) else s["value"]
                flag = "（未加载）" if s["missing"] else ""
                lines.append(f"  {s['title']}: {value or '留空/跟随'}{flag}")
        if state["presets"]:
            lines.append("方案: " + "、".join(
                f"{p['name']}{'（当前）' if p.get('matched') else ''}" for p in state["presets"]
            ))
        return "\n".join(lines) or "没有发现任何模型槽位"


def _slot_from_state(state: Mapping[str, Any]) -> SlotSpec:
    return SlotSpec(**{k: state[k] for k in SlotSpec.__dataclass_fields__})
