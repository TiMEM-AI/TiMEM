import { useEffect, useState } from "react";

const PRESETS = ["qwen3.7-flash", "glm-5.3-flash"];
const LEVELS = [
  { id: "", label: "全部" },
  { id: "L1", label: "L1 片段" },
  { id: "L2", label: "L2 会话" },
  { id: "L3", label: "L3 日" },
  { id: "L4", label: "L4 周" },
  { id: "L5", label: "L5 月" },
];
const PANELS = [
  { id: "chat", label: "对话" },
  { id: "memory", label: "记忆" },
  { id: "models", label: "模型" },
];

export default function App() {
  const [panel, setPanel] = useState("chat");
  const [userId, setUserId] = useState("local-user");
  const [apiKey, setApiKey] = useState("");
  const [needsKey, setNeedsKey] = useState(false);
  const [message, setMessage] = useState("");
  const [log, setLog] = useState([]);
  const [modelTarget, setModelTarget] = useState("reply");
  const [slots, setSlots] = useState({
    reply: { base_url: "https://kspmas.ksyun.com/v1", model: "qwen3.7-flash", api_key_set: false },
    memory: { base_url: "https://kspmas.ksyun.com/v1", model: "glm-5.3-flash", api_key_set: false },
    embed: { base_url: "https://kspmas.ksyun.com/v1", model: "qwen3-embedding-8b", dimensions: 2048, api_key_set: false },
  });
  const [draftKeys, setDraftKeys] = useState({ reply: "", memory: "", embed: "" });
  const [embedModel, setEmbedModel] = useState("qwen3-embedding-8b");
  const [embedDim, setEmbedDim] = useState(2048);
  const [settingsNote, setSettingsNote] = useState("");
  const [checkNote, setCheckNote] = useState("");
  const [memories, setMemories] = useState([]);
  const [memoryNote, setMemoryNote] = useState("");
  const [memoryLevel, setMemoryLevel] = useState("");
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memoryStart, setMemoryStart] = useState("");
  const [memoryEnd, setMemoryEnd] = useState("");
  const [memoryPage, setMemoryPage] = useState(1);
  const [memoryHasMore, setMemoryHasMore] = useState(false);
  const [memoryTick, setMemoryTick] = useState(0);
  const [selectedMemory, setSelectedMemory] = useState(null);

  function headers(json = true) {
    const result = {};
    if (json) result["Content-Type"] = "application/json";
    if (needsKey && apiKey) result["X-Api-Key"] = apiKey;
    return result;
  }

  useEffect(() => {
    fetch("/v1/bootstrap")
      .then((response) => response.json())
      .then((data) => setNeedsKey(!!data.chat_requires_key))
      .catch(() => setNeedsKey(false));
  }, []);

  useEffect(() => {
    if (needsKey && !apiKey) return;
    fetch("/v1/settings/models", { headers: headers() })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data) return;
        if (data.reply && data.memory && data.embed) {
          setSlots({ reply: data.reply, memory: data.memory, embed: data.embed });
        }
        setEmbedModel(data.embed_model || "qwen3-embedding-8b");
        setEmbedDim(data.embed_dim ?? 2048);
      })
      .catch(() => {});
  }, [needsKey, apiKey]);

  async function loadMemories() {
    const params = new URLSearchParams({
      user_id: userId.trim() || "local-user",
      limit: "20",
      page: String(memoryPage),
    });
    if (memoryLevel) params.set("level", memoryLevel);
    if (memoryQuery.trim()) params.set("q", memoryQuery.trim());
    if (memoryStart && memoryEnd) {
      params.set("start", memoryStart);
      params.set("end", memoryEnd);
    }
    const response = await fetch(`/v1/memories?${params.toString()}`, {
      headers: headers(false),
    });
    if (!response.ok) {
      setMemories([]);
      setMemoryHasMore(false);
      setMemoryNote(response.status === 401 ? "查看记忆需要 API Key。" : "暂时读不到记忆。");
      return;
    }
    const payload = await response.json();
    const rows = payload.data || [];
    setMemories(rows);
    setMemoryHasMore(!!payload.has_more);
    setSelectedMemory((current) => rows.find((item) => item.id === current?.id) || null);
    if (!rows.length) {
      setMemoryNote(memoryLevel || memoryQuery.trim() || (memoryStart && memoryEnd) ? "这个条件下没有记忆。" : "这个用户还没有记忆。");
    } else {
      setMemoryNote("");
    }
  }

  useEffect(() => {
    if (panel === "memory") loadMemories();
  }, [panel, userId, apiKey, needsKey, memoryLevel, memoryPage, memoryTick]);

  function chooseLevel(level) {
    setSelectedMemory(null);
    setMemoryPage(1);
    setMemoryLevel(level);
    if (memoryPage === 1 && memoryLevel === level) setMemoryTick((value) => value + 1);
  }

  function searchMemories(event) {
    event.preventDefault();
    setSelectedMemory(null);
    setMemoryPage(1);
    setMemoryTick((value) => value + 1);
  }

  function patchSlot(field, value) {
    setSlots((prev) => ({ ...prev, [modelTarget]: { ...prev[modelTarget], [field]: value } }));
  }

  async function saveModels(event) {
    event.preventDefault();
    const slot = slots[modelTarget];
    const response = await fetch("/v1/settings/models", {
      method: "PUT",
      headers: headers(),
      body: JSON.stringify({
        target: modelTarget,
        model: slot.model,
        base_url: slot.base_url,
        api_key: draftKeys[modelTarget],
        dimensions: Number(slot.dimensions) || 2048,
      }),
    });
    const payload = await response.json();
    if (response.ok && payload[modelTarget]) {
      setSlots((prev) => ({ ...prev, [modelTarget]: payload[modelTarget] }));
      setDraftKeys((prev) => ({ ...prev, [modelTarget]: "" }));
      setSettingsNote("已保存。这一项下次就会用新的配置。");
    } else {
      setSettingsNote(`保存失败：${JSON.stringify(payload)}`);
    }
  }

  async function verifyCurrent() {
    const slot = slots[modelTarget];
    setCheckNote("验证中…");
    const response = await fetch("/v1/settings/models/verify", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        target: modelTarget,
        model: slot.model,
        base_url: slot.base_url,
        api_key: draftKeys[modelTarget],
        dimensions: Number(slot.dimensions) || 2048,
      }),
    });
    const payload = await response.json();
    setCheckNote(payload.message || (payload.ok ? "模型可用" : "模型不可用"));
  }

  async function onSubmit(event) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;
    setLog((prev) => [...prev, { role: "you", text }]);
    setMessage("");
    const response = await fetch("/v1/chat", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ user_id: userId.trim() || "local-user", message: text }),
    });
    const payload = await response.json();
    setLog((prev) => [
      ...prev,
      response.ok
        ? { role: "timem", text: payload.reply || "" }
        : { role: "error", text: JSON.stringify(payload) },
    ]);
  }

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <div>
            <strong>TiMem</strong>
            <small>时序记忆</small>
          </div>
        </div>
        <label className="user-field">
          当前用户
          <input value={userId} onChange={(event) => setUserId(event.target.value)} />
        </label>
        {needsKey ? (
          <label className="user-field">
            API Key
            <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
          </label>
        ) : null}
        <nav>
          {PANELS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={panel === item.id ? "nav active" : "nav"}
              onClick={() => setPanel(item.id)}
            >
              <span className="ticks" aria-hidden="true">
                <i /><i /><i /><i /><i />
              </span>
              {item.label}
            </button>
          ))}
        </nav>
        <p className="rail-foot">同一份记忆。页面聊天，Agent 打接口。</p>
      </aside>
      <section className="stage">
        {panel === "chat" ? (
          <>
            <header className="stage-head">
              <h1>对话</h1>
              <p>这句话会先检索已有记忆，再写回这个用户。</p>
            </header>
            <div className="transcript">
              {log.length === 0 ? <p className="empty">从侧边栏确认用户，然后说第一句。</p> : null}
              {log.map((item, index) => (
                <article key={index} className={item.role}>
                  <span>{item.role === "you" ? "你" : item.role === "timem" ? "TiMem" : "失败"}</span>
                  <p>{item.text}</p>
                </article>
              ))}
            </div>
            <form className="composer" onSubmit={onSubmit}>
              <textarea
                rows={3}
                value={message}
                placeholder="说一句，会进入这个用户的记忆"
                onChange={(event) => setMessage(event.target.value)}
              />
              <button type="submit">发送</button>
            </form>
          </>
        ) : null}
        {panel === "memory" ? (
          <>
            <header className="stage-head">
              <h1>记忆</h1>
              <p>按当前用户查看。L1 是当轮片段，往上收到会话、日、周、月。</p>
            </header>
            <div className="levels">
              {LEVELS.map((item) => (
                <button
                  key={item.id || "all"}
                  type="button"
                  className={memoryLevel === item.id ? "on" : ""}
                  onClick={() => chooseLevel(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <form className="memory-tools" onSubmit={searchMemories}>
              <input
                value={memoryQuery}
                placeholder="搜索标题或内容"
                onChange={(event) => setMemoryQuery(event.target.value)}
              />
              <input type="date" value={memoryStart} onChange={(event) => setMemoryStart(event.target.value)} />
              <input type="date" value={memoryEnd} onChange={(event) => setMemoryEnd(event.target.value)} />
              <button type="submit">查询</button>
            </form>
            {memoryNote ? <p className="empty">{memoryNote}</p> : null}
            {selectedMemory ? (
              <article className="memory-detail">
                <span>{selectedMemory.level || "记忆"}</span>
                <h2>{selectedMemory.title || "未命名"}</h2>
                <time>{selectedMemory.created_at || ""}</time>
                <p>{selectedMemory.content}</p>
              </article>
            ) : null}
            <ul className="memory-list">
              {memories.map((item) => (
                <li key={item.id}>
                  <button type="button" className={selectedMemory?.id === item.id ? "on" : ""} onClick={() => setSelectedMemory(item)}>
                    <span>{item.level || "记忆"}</span>
                    <strong>{item.title || "未命名"}</strong>
                    <time>{item.created_at || ""}</time>
                    <p>{item.content}</p>
                  </button>
                </li>
              ))}
            </ul>
            <div className="pager">
              <button type="button" disabled={memoryPage <= 1} onClick={() => setMemoryPage((value) => Math.max(1, value - 1))}>上一页</button>
              <span>第 {memoryPage} 页</span>
              <button type="button" disabled={!memoryHasMore} onClick={() => setMemoryPage((value) => value + 1)}>下一页</button>
            </div>
          </>
        ) : null}
        {panel === "models" ? (
          <>
            <header className="stage-head">
              <h1>模型</h1>
              <p>按 OpenAI 兼容接口填写地址和 Key。对话模型负责页面里的回复，记忆模型负责写入和检索。</p>
            </header>
            <form className="settings" onSubmit={saveModels}>
              <div className="switch">
                <button type="button" className={modelTarget === "reply" ? "on" : ""} onClick={() => { setModelTarget("reply"); setCheckNote(""); setSettingsNote(""); }}>
                  对话模型
                </button>
                <button type="button" className={modelTarget === "memory" ? "on" : ""} onClick={() => { setModelTarget("memory"); setCheckNote(""); setSettingsNote(""); }}>
                  记忆模型
                </button>
                <button type="button" className={modelTarget === "embed" ? "on" : ""} onClick={() => { setModelTarget("embed"); setCheckNote(""); setSettingsNote(""); }}>
                  向量模型
                </button>
              </div>
              <label>
                接口地址
                <input
                  value={slots[modelTarget].base_url}
                  onChange={(event) => patchSlot("base_url", event.target.value)}
                  placeholder="https://api.openai.com/v1"
                />
              </label>
              <label>
                API Key
                <input
                  type="password"
                  value={draftKeys[modelTarget]}
                  placeholder={slots[modelTarget].api_key_set ? "已保存，留空则不修改" : "sk-..."}
                  onChange={(event) => setDraftKeys((prev) => ({ ...prev, [modelTarget]: event.target.value }))}
                />
              </label>
              <label>
                模型名
                <input
                  list="model-presets"
                  value={slots[modelTarget].model}
                  onChange={(event) => patchSlot("model", event.target.value)}
                />
              </label>
              {modelTarget === "embed" ? (
                <>
                  <label>
                    向量维度
                    <input
                      type="number"
                      value={slots.embed.dimensions || 2048}
                      onChange={(event) => patchSlot("dimensions", Number(event.target.value) || 0)}
                    />
                  </label>
                  <p className="embed">更换向量模型或维度后，已有向量对不上，需要重建向量库。</p>
                </>
              ) : null}
              <datalist id="model-presets">
                {PRESETS.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
              <button type="button" onClick={verifyCurrent}>验证是否可用</button>
              {checkNote ? <p className="embed">{checkNote}</p> : null}
              <button type="submit">保存当前模型</button>
              {settingsNote ? <p className="embed">{settingsNote}</p> : null}
            </form>
          </>
        ) : null}
      </section>
    </div>
  );
}
