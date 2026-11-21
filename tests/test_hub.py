from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.discovery import discover_plugin_slots, iter_core_fields  # noqa: E402
from services.hub import ModelHub  # noqa: E402

CORE_METADATA = {
    "ai_group": {
        "name": "AI 配置",
        "metadata": {
            "ai": {
                "description": "模型",
                "type": "object",
                "items": {
                    "agent_runner.config.model.provider_id": {"description": "对话模型", "type": "string", "_special": "select_provider"},
                    "agent_runner.config.model.fallback_provider_ids": {
                        "description": "回退对话模型", "type": "list", "_special": "select_providers",
                    },
                    "provider_stt_settings.provider_id": {"description": "语音识别", "type": "string", "_special": "select_provider_stt"},
                    "provider_settings.enable": {"description": "启用", "type": "bool"},
                },
            },
            "compress": {
                "description": "上下文压缩",
                "type": "object",
                "items": {
                    "agent_runner.config.compression.provider_id": {"description": "压缩模型", "type": "string", "_special": "select_provider"},
                },
            },
        },
    }
}

PLUGIN_SCHEMA = {
    "llm": {
        "type": "object",
        "description": "LLM",
        "items": {
            "llm_provider_id": {"type": "string", "_special": "select_provider", "description": "主模型"},
            "topic_provider_id": {"type": "string", "_special": "select_provider", "description": "话题模型（留空跟随）"},
        },
    },
    "semantic": {
        "type": "object",
        "items": {"embedding_provider_id": {"type": "string", "_special": "select_provider_embedding"}},
    },
    "templates": {"type": "template_list", "templates": {}},
    "enabled": {"type": "bool"},
}


class FakeConfig(dict):
    def __init__(self, data=None, schema=None):
        super().__init__(data or {})
        self.schema = schema
        self.saves = 0

    def save_config(self, replace_config=None, *, indent=2):
        self.saves += 1


class FakeRuntime:
    def __init__(self):
        self.pipelines: list[str] = []
        self.reloads: list[str] = []

    async def reload_pipeline(self, conf_id):
        self.pipelines.append(conf_id)
        return "pipeline"

    async def reload_plugin(self, name):
        self.reloads.append(name)
        return "reload"


def _provider(pid):
    return SimpleNamespace(meta=lambda: SimpleNamespace(id=pid, model=pid.split("/")[-1]))


CHAT_IDS = ["nug/g4h", "nug/g4h-low", "nug-us/g38f", "nug-us/g38f-med"]


def make_hub(settings=None, extra_stars=()):
    yushu = FakeConfig({
        "agent_runner": {"config": {
            "model": {"provider_id": "nug/g4h", "fallback_provider_ids": ["nug/g4h-low"]},
            "compression": {"provider_id": "nug/g4h"},
        }},
        "provider_stt_settings": {"provider_id": "stt-1"},
    })
    default = FakeConfig({"agent_runner": {"config": {"model": {"provider_id": ""}}}})
    daily = FakeConfig({"llm": {"llm_provider_id": "nug/g4h", "topic_provider_id": ""}}, schema=PLUGIN_SCHEMA)
    meme_star = SimpleNamespace(semantic_vision_provider_id="nug/g4h", config=None)
    meme_cfg = FakeConfig({"semantic": {"vision_provider_id": "nug/g4h"}}, schema={
        "semantic": {"type": "object", "items": {"vision_provider_id": {"type": "string", "_special": "select_provider"}}},
    })
    meme_star.config = meme_cfg
    other_cfg = FakeConfig({"audit": "nug/g4h"}, schema={"audit": {"type": "string", "_special": "select_provider"}})
    self_cfg = FakeConfig({}, schema={"x": {"type": "string", "_special": "select_provider"}})
    stars = [
        SimpleNamespace(name="astrbot_plugin_qq_group_daily_analysis", root_dir_name="astrbot_plugin_qq_group_daily_analysis",
                        display_name="群日报", activated=True, config=daily, star_cls=SimpleNamespace()),
        SimpleNamespace(name="meme_manager", root_dir_name="astrbot_plugin_meme_manager", display_name="表情包",
                        activated=True, config=meme_cfg, star_cls=meme_star),
        SimpleNamespace(name="astrbot_plugin_other", root_dir_name="astrbot_plugin_other", display_name="其他",
                        activated=True, config=other_cfg, star_cls=SimpleNamespace()),
        SimpleNamespace(name="astrbot_plugin_model_hub", root_dir_name="astrbot_plugin_model_hub", display_name="自己",
                        activated=True, config=self_cfg, star_cls=SimpleNamespace()),
        SimpleNamespace(name="disabled", root_dir_name="disabled", activated=False, config=other_cfg, star_cls=None),
        *extra_stars,
    ]
    context = SimpleNamespace(
        astrbot_config_mgr=SimpleNamespace(
            confs={"default": default, "uuid-yushu": yushu},
            abconf_data={"uuid-yushu": {"name": "羽书", "path": "abconf_uuid-yushu.json"}},
        ),
        get_all_stars=lambda: stars,
        get_all_providers=lambda: [_provider(pid) for pid in CHAT_IDS],
        get_all_stt_providers=lambda: [_provider("stt-1")],
        get_all_tts_providers=lambda: [],
        get_all_embedding_providers=lambda: [_provider("emb")],
    )
    runtime = FakeRuntime()
    settings = FakeConfig(settings or {"presets": "{}"})
    hub = ModelHub(context, runtime, settings, CORE_METADATA)
    return hub, SimpleNamespace(yushu=yushu, default=default, daily=daily, meme_cfg=meme_cfg, meme_star=meme_star,
                                other=other_cfg, runtime=runtime, settings=settings)


def run(coro):
    return asyncio.run(coro)


def test_discovery_core_and_plugin_paths():
    keys = [k for k, _, _ in iter_core_fields(CORE_METADATA)]
    assert keys == [
        "agent_runner.config.model.provider_id",
        "agent_runner.config.model.fallback_provider_ids",
        "provider_stt_settings.provider_id",
        "agent_runner.config.compression.provider_id",
    ]
    slots = discover_plugin_slots("p", "P", PLUGIN_SCHEMA)
    assert [(s.key, s.kind) for s in slots] == [
        ("llm.llm_provider_id", "chat"),
        ("llm.topic_provider_id", "chat"),
        ("semantic.embedding_provider_id", "embedding"),
    ]


def test_snapshot_lists_all_targets_without_self_disabled_or_embedding():
    hub, _ = make_hub()
    state = hub.snapshot()
    labels = [t["label"] for t in state["targets"]]
    assert labels == ["AstrBot · 羽书", "AstrBot · 默认配置", "群日报", "表情包", "其他"]
    assert not any(s["kind"] == "embedding" for s in state["slots"])
    apply_modes = {t["name"]: t["apply"] for t in state["targets"]}
    assert apply_modes["uuid-yushu"] == "pipeline"
    assert apply_modes["meme_manager"] == "adapter"
    assert apply_modes["astrbot_plugin_other"] == "reload"
    fb = next(s for s in state["slots"] if s["id"] == "core:uuid-yushu:agent_runner.config.model.fallback_provider_ids")
    assert fb["value"] == ["nug/g4h-low"] and fb["is_list"]


def test_switch_all_keeps_empty_inherit_slots_and_chains_and_voice():
    hub, env = make_hub()
    result = run(hub.switch_all("nug-us/g38f"))
    assert result["ok"], result
    y = env.yushu["agent_runner"]["config"]
    assert y["model"]["provider_id"] == "nug-us/g38f"
    assert y["compression"]["provider_id"] == "nug-us/g38f"
    assert y["model"]["fallback_provider_ids"] == ["nug/g4h-low"]  # 未给 fallbacks 不动回退链
    assert env.yushu["provider_stt_settings"]["provider_id"] == "stt-1"  # 语音不动
    assert env.default["agent_runner"]["config"]["model"]["provider_id"] == ""  # 留空保持跟随
    assert env.daily["llm"] == {"llm_provider_id": "nug-us/g38f", "topic_provider_id": ""}
    assert env.meme_cfg["semantic"]["vision_provider_id"] == "nug-us/g38f"
    assert env.meme_star.semantic_vision_provider_id == "nug-us/g38f"  # 内置适配同步缓存
    assert env.other["audit"] == "nug-us/g38f"
    assert env.runtime.pipelines == ["uuid-yushu"]
    assert env.runtime.reloads == ["astrbot_plugin_other"]  # 只有未适配插件走热重载
    assert env.yushu.saves == 1 and env.daily.saves == 1 and env.default.saves == 0


def test_switch_all_with_fallbacks_rewrites_chain():
    hub, env = make_hub()
    run(hub.switch_all("nug-us/g38f", fallbacks="nug-us/g38f-med, nug/g4h-low"))
    assert env.yushu["agent_runner"]["config"]["model"]["fallback_provider_ids"] == ["nug-us/g38f-med", "nug/g4h-low"]


def test_apply_validates_everything_before_writing():
    hub, env = make_hub()
    result = run(hub.apply({
        "core:uuid-yushu:agent_runner.config.model.provider_id": "nug-us/g38f",
        "plugin:meme_manager:semantic.vision_provider_id": "not-loaded",
    }))
    assert not result["ok"] and "not-loaded" in result["error"]
    assert env.yushu["agent_runner"]["config"]["model"]["provider_id"] == "nug/g4h"
    assert env.yushu.saves == 0
    assert not run(hub.apply({"plugin:nope:x": "a"}))["ok"]


def test_apply_skips_unchanged_values():
    hub, env = make_hub()
    result = run(hub.apply({"core:uuid-yushu:agent_runner.config.model.provider_id": "nug/g4h"}))
    assert result["ok"] and result["applied"] == []
    assert env.yushu.saves == 0 and env.runtime.pipelines == []


def test_presets_switch_and_snapshot_roundtrip():
    hub, env = make_hub()
    assert hub.save_preset("3.8f", model="nug-us/g38f")["ok"]
    assert hub.save_preset("当前", capture_current=True)["ok"]
    stored = json.loads(env.settings["presets"])
    assert stored["3.8f"]["model"] == "nug-us/g38f"
    assert stored["当前"]["slots"]["core:uuid-yushu:agent_runner.config.compression.provider_id"] == "nug/g4h"
    assert env.settings.saves == 2

    matched = {p["name"]: p["matched"] for p in hub.snapshot()["presets"]}
    assert matched == {"3.8f": False, "当前": True}

    assert run(hub.apply_preset("3.8f"))["ok"]
    matched = {p["name"]: p["matched"] for p in hub.snapshot()["presets"]}
    assert matched == {"3.8f": True, "当前": False}

    assert run(hub.apply_preset("当前"))["ok"]
    assert env.yushu["agent_runner"]["config"]["compression"]["provider_id"] == "nug/g4h"
    assert env.meme_star.semantic_vision_provider_id == "nug/g4h"
    assert hub.delete_preset("当前")["ok"] and not hub.delete_preset("当前")["ok"]


def test_plugin_hook_takes_priority_and_settings_respected():
    calls = []

    class HookStar:
        async def model_hub_apply(self, changes):
            calls.append(changes)
            return ["custom"]

    cfg = FakeConfig({"m": "nug/g4h"}, schema={"m": {"type": "string", "_special": "select_provider"}})
    hooked = SimpleNamespace(name="hooked", root_dir_name="hooked", display_name="钩子", activated=True,
                             config=cfg, star_cls=HookStar())
    hub, env = make_hub(settings={"presets": "{}", "excluded_plugins": ["astrbot_plugin_other"],
                                  "force_reload_plugins": ["meme_manager"]}, extra_stars=[hooked])
    result = run(hub.switch_all("nug-us/g38f"))
    assert result["ok"]
    assert calls == [{"m": "nug-us/g38f"}]
    assert env.other["audit"] == "nug/g4h"  # 被排除
    assert env.runtime.reloads == ["meme_manager"]  # 强制重载替代适配
    hot = {r["key"]: r["hot"] for r in result["targets"]}
    assert hot["plugin:hooked"] == "hook" and hot["plugin:meme_manager"] == "reload"


def test_wave_memory_adapter_reloads_router():
    from services.adapters import find_adapter

    reloaded = []
    routing = SimpleNamespace(_tag_primary="old", _meta_cfg={}, reload=lambda cfg: reloaded.append(dict(cfg)))
    cfg = {"tag_llm_provider_id": "new", "MetaThinking_Settings": {"default_model": "m"}}
    star = SimpleNamespace(llm_routing=routing, config=cfg, tag_llm_provider_id="old")
    notes = find_adapter("astrbot_plugin_wave_memory")(
        star, {"tag_llm_provider_id": "new", "MetaThinking_Settings.default_model": "m"},
    )
    assert routing._tag_primary == "new" and star.tag_llm_provider_id == "new"
    assert routing._meta_cfg == {"default_model": "m"}
    assert reloaded and "llm_routing.reload" in notes


def test_failed_target_reported_but_others_applied():
    hub, env = make_hub()

    async def boom(name):
        raise RuntimeError("reload failed")

    env.runtime.reload_plugin = boom
    result = run(hub.switch_all("nug-us/g38f"))
    assert not result["ok"] and "reload failed" in result["error"]
    assert env.yushu["agent_runner"]["config"]["model"]["provider_id"] == "nug-us/g38f"
    failed = next(r for r in result["targets"] if r["key"] == "plugin:astrbot_plugin_other")
    assert failed["persisted"] and failed["error"]


def test_describe_text():
    hub, _ = make_hub()
    text = hub.describe()
    assert "【AstrBot · 羽书】" in text and "对话模型: nug/g4h" in text and "留空/跟随" in text
    assert "语音识别" not in text


@pytest.mark.parametrize("raw,expected", [("a, b，a", ["a", "b"]), (["a", " ", "b"], ["a", "b"]), (None, [])])
def test_chain_normalization(raw, expected):
    from services.discovery import SlotSpec, normalize_value

    slot = SlotSpec(id="x", scope="core", target="t", target_label="T", key="k", kind="chat_chain", title="t", is_list=True)
    assert normalize_value(slot, raw) == expected
