(() => {
  "use strict";

  const HOT_LABEL = {
    pipeline: "已重载对话流水线",
    reload: "已热重载插件",
    adapter: "已原地同步",
    hook: "插件自行热更新",
    inplace: "已改共享配置",
    pending: "已落盘，重启后生效",
  };
  const KIND_LABEL = { chat: "对话类", chat_chain: "回退链", stt: "语音识别", tts: "语音合成", embedding: "向量" };

  const $ = (id) => document.getElementById(id);
  let state = null;
  const dirty = new Map();
  let busy = false;
  let toastTimer = 0;

  const bridge = () => window.AstrBotPluginView || window.AstrBotPluginPage;
  const apiGet = (endpoint, params) => bridge().apiGet(endpoint, params || {});
  const apiPost = (endpoint, body) => bridge().apiPost(endpoint, body || {});

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function toast(message, isError) {
    const box = $("toast");
    box.textContent = message;
    box.classList.toggle("error", Boolean(isError));
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, isError ? 9000 : 5000);
  }

  const fmt = (value) => (Array.isArray(value) ? value.join(", ") : value || "");
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const parseChain = (text) => [...new Set(String(text || "").split(/[,，\n]/).map((x) => x.trim()).filter(Boolean))];

  function setBusy(flag) {
    busy = flag;
    document.querySelectorAll("button").forEach((btn) => { btn.disabled = flag; });
  }

  function describeResult(result) {
    const lines = [result.message || (result.ok ? "完成" : "失败")];
    for (const target of result.targets || []) {
      const hot = target.error ? `失败：${target.error}` : HOT_LABEL[target.hot] || target.hot;
      lines.push(`· ${target.label}：${target.slots.length} 项，${hot}`);
    }
    if (result.error && !(result.targets || []).length) lines.push(result.error);
    return lines.join("\n");
  }

  async function run(action) {
    if (busy) return;
    setBusy(true);
    try {
      const result = await action();
      if (result && typeof result === "object") {
        if (result.state) applyState(result.state);
        else if ("ok" in result && result.ok && !result.slots) await load(true);
        toast(describeResult(result), result.ok === false);
      }
    } catch (error) {
      toast(`请求失败：${error?.message || error}`, true);
    } finally {
      setBusy(false);
    }
  }

  async function load(silent) {
    try {
      applyState(await apiGet("state"));
    } catch (error) {
      if (!silent) $("summary").textContent = `读取失败：${error?.message || error}`;
      throw error;
    }
  }

  function applyState(next) {
    state = next;
    dirty.clear();
    render();
  }

  function providerOptions(kind, current) {
    const list = (state.providers[kind] || []);
    const ids = new Set(list.map((p) => p.id));
    const options = [el("option", { value: "", text: "（留空 / 跟随默认）" })];
    if (current && !ids.has(current)) options.push(el("option", { value: current, text: `${current}（未加载）` }));
    for (const p of list) options.push(el("option", { value: p.id, text: p.model && p.model !== p.id ? `${p.id} · ${p.model}` : p.id }));
    return options;
  }

  function render() {
    const slots = state.slots || [];
    const missing = slots.filter((s) => s.missing.length).length;
    $("summary").textContent = `${state.targets.length} 处配置 · ${slots.length} 个模型槽位` + (missing ? ` · ${missing} 个引用了未加载的模型` : "");

    const chat = state.providers.chat || [];
    const switchSelect = $("switch-model");
    const previous = switchSelect.value;
    switchSelect.replaceChildren(el("option", { value: "", text: "选择模型…" }), ...chat.map((p) => el("option", { value: p.id, text: p.id })));
    if (chat.some((p) => p.id === previous)) switchSelect.value = previous;
    $("chat-providers").replaceChildren(...chat.map((p) => el("option", { value: p.id })));
    $("switch-diff").hidden = true;

    renderPresets();
    renderTargets();
    renderDirty();
  }

  function renderPresets() {
    const box = $("presets");
    const presets = state.presets || [];
    if (!presets.length) {
      box.replaceChildren(el("p", { class: "empty", text: "还没有方案。可以在上方选好模型后「存为方案」，或把当前配置整体存下来。" }));
      return;
    }
    box.replaceChildren(...presets.map((preset) => {
      const desc = preset.model
        ? `全切到 ${preset.model}` + (preset.fallbacks ? `，回退链 ${preset.fallbacks.join(", ") || "清空"}` : "") + (preset.include_empty ? "，含留空槽位" : "")
        : `快照 ${Object.keys(preset.slots || {}).length} 项`;
      return el("div", { class: "preset" },
        el("span", { class: "name", text: preset.name }),
        preset.matched ? el("span", { class: "badge ok", text: "当前生效" }) : null,
        el("span", { class: "desc", text: preset.note ? `${desc} · ${preset.note}` : desc }),
        el("button", { type: "button", text: "应用", onclick: () => confirmRun(`应用方案「${preset.name}」？`, () => apiPost("presets/apply", { name: preset.name })) }),
        el("button", { type: "button", class: "ghost", text: "删除", onclick: () => confirmRun(`删除方案「${preset.name}」？`, () => apiPost("presets/delete", { name: preset.name })) }),
      );
    }));
  }

  function slotEditor(slot) {
    const current = dirty.has(slot.id) ? dirty.get(slot.id) : slot.value;
    const onChange = (value) => {
      if (same(value, slot.value)) dirty.delete(slot.id);
      else dirty.set(slot.id, value);
      renderDirty();
      editor.closest(".slot").classList.toggle("dirty-row", dirty.has(slot.id));
    };
    let editor;
    if (slot.is_list) {
      editor = el("input", { value: fmt(current), list: "chat-providers", "aria-label": slot.title, placeholder: "逗号分隔，按顺序回退" });
      editor.addEventListener("change", () => onChange(parseChain(editor.value)));
    } else {
      editor = el("select", { "aria-label": `${slot.target_label} ${slot.title}` }, providerOptions(slot.kind, current));
      editor.value = current;
      editor.addEventListener("change", () => onChange(editor.value));
    }
    return editor;
  }

  function renderTargets() {
    const keyword = $("filter").value.trim().toLowerCase();
    const openKeys = new Set([...document.querySelectorAll(".target[open]")].map((d) => d.dataset.key));
    const firstRender = !document.querySelector(".target");
    const blocks = [];
    for (const target of state.targets) {
      const slots = state.slots.filter((s) => s.target_key === target.key && (!keyword ||
        [target.label, s.title, s.key, s.section, fmt(s.value)].join(" ").toLowerCase().includes(keyword)));
      if (!slots.length) continue;
      const missing = slots.filter((s) => s.missing.length).length;
      const details = el("details", { class: "target", "data-key": target.key, open: keyword || openKeys.has(target.key) || (firstRender && target.scope === "core") },
        el("summary", {},
          el("span", { class: "title", text: target.label }),
          el("span", { class: "badge", text: `${slots.length} 项` }),
          el("span", { class: "badge", text: HOT_LABEL[target.apply] ? `改后：${HOT_LABEL[target.apply]}` : target.apply }),
          missing ? el("span", { class: "badge warn", text: `${missing} 项未加载` }) : null,
        ),
        slots.map((slot) => el("div", { class: dirty.has(slot.id) ? "slot dirty-row" : "slot" },
          el("div", { class: "label" },
            el("span", { text: slot.title }),
            el("span", { class: "meta" }, `${KIND_LABEL[slot.kind] || slot.kind} · `, el("code", { text: slot.key })),
            slot.hint ? el("span", { class: "meta", text: slot.hint }) : null,
          ),
          el("div", { class: "label" },
            slotEditor(slot),
            slot.missing.length ? el("span", { class: "missing", text: `未加载：${slot.missing.join(", ")}` }) : null,
          ),
        )),
      );
      blocks.push(details);
    }
    $("targets").replaceChildren(...(blocks.length ? blocks : [el("p", { class: "empty", text: keyword ? "没有匹配的槽位" : "没有发现任何模型槽位" })]));
  }

  function renderDirty() {
    $("dirty-bar").hidden = dirty.size === 0;
    $("dirty-count").textContent = `${dirty.size} 项待保存`;
  }

  // 插件页运行在不带 allow-modals 的沙箱 iframe 里，window.confirm 会被静默忽略
  let pendingConfirm = null;
  function closeConfirm(ok) {
    const action = pendingConfirm;
    pendingConfirm = null;
    $("confirm").hidden = true;
    if (ok && action) run(action);
  }
  function confirmRun(message, action) {
    pendingConfirm = action;
    $("confirm-text").textContent = message;
    $("confirm").hidden = false;
    $("confirm-ok").focus();
  }
  $("confirm-ok").addEventListener("click", () => closeConfirm(true));
  $("confirm-cancel").addEventListener("click", () => closeConfirm(false));
  $("confirm").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeConfirm(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pendingConfirm) closeConfirm(false); });

  function switchPayload() {
    const model = $("switch-model").value;
    const fallbackText = $("switch-fallbacks").value.trim();
    return {
      model,
      fallbacks: fallbackText ? parseChain(fallbackText) : null,
      include_empty: $("switch-include-empty").checked,
    };
  }

  // 与后端 plan_switch_all 同一规则，仅用于预览；真正写入以后端计算为准
  function previewSwitch({ model, fallbacks, include_empty }) {
    const changes = [];
    for (const slot of state.slots) {
      let next;
      if (slot.kind === "chat" && model) {
        const target = slot.is_list ? [model] : model;
        if ((slot.value.length || include_empty) && !same(slot.value, target)) next = target;
      } else if (slot.kind === "chat_chain" && fallbacks && !same(slot.value, fallbacks)) {
        next = fallbacks;
      }
      if (next !== undefined) changes.push({ slot, next });
    }
    return changes;
  }

  function showPreview() {
    const payload = switchPayload();
    if (!payload.model) return toast("先选择目标模型", true);
    const changes = previewSwitch(payload);
    const list = $("switch-diff");
    list.replaceChildren(...(changes.length
      ? changes.map(({ slot, next }) => el("li", {}, `${slot.target_label} · ${slot.title}：`, el("code", { text: fmt(slot.value) || "留空" }), " → ", el("code", { text: fmt(next) })))
      : [el("li", { text: "没有需要改动的槽位" })]));
    list.hidden = false;
    return changes;
  }

  $("refresh").addEventListener("click", () => run(async () => { await load(); return null; }));
  $("filter").addEventListener("input", renderTargets);
  $("switch-preview").addEventListener("click", showPreview);
  $("switch-apply").addEventListener("click", () => {
    const changes = showPreview();
    if (!changes) return;
    if (!changes.length) return toast("没有需要改动的槽位");
    confirmRun(`将修改 ${changes.length} 个槽位并立即生效，继续？`, () => apiPost("switch-all", switchPayload()));
  });
  $("switch-save").addEventListener("click", () => {
    const payload = switchPayload();
    const name = $("switch-preset-name").value.trim();
    if (!payload.model || !name) return toast("请选择目标模型并填写方案名", true);
    run(() => apiPost("presets/save", { name, ...payload }));
  });
  $("capture-save").addEventListener("click", () => {
    const name = $("capture-name").value.trim();
    if (!name) return toast("请填写方案名", true);
    run(() => apiPost("presets/save", { name, capture_current: true }));
  });
  $("dirty-reset").addEventListener("click", () => { dirty.clear(); render(); });
  $("dirty-save").addEventListener("click", () => {
    if (!dirty.size) return;
    confirmRun(`保存 ${dirty.size} 项修改并立即生效？`, () => apiPost("apply", { changes: Object.fromEntries(dirty) }));
  });

  (async () => {
    try {
      await bridge().ready();
    } catch {
      /* ready 只在宿主握手失败时拒绝，后续请求会给出具体错误 */
    }
    load().catch(() => {});
  })();
})();
