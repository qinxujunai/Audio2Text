import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Settings2, X, LoaderCircle, Check, Download } from "lucide-react";
import { localRequest } from "../api";
import { isDesktopApp } from "../desktop";
import "./local-tools.css";

type Preferences = {
  device: string; engine: string; sdk_path: string; model_path: string; cuda_available: boolean; model_ready: boolean;
  default_paths: Record<string, string>;
  available_model_paths: string[]; base_url: string; has_api_key: boolean; model: string;
  runtime: { actual_device: string | null; selected_device: string; model: string };
};
type Edit = { start: number; end: number; before: string; after: string; reason: string };
type Saved = { text: string; edits: Edit[]; saved_at: string };
type Job = { id: string; status: string; progress: number; edits: Edit[]; message: string; completed_chunks?: number; total_chunks?: number; error_code?: string };
type Proof = { job: Job | null; saved: Saved | null };
const runtimeSelection = (p: Preferences) => JSON.stringify([p.device, p.engine, p.sdk_path, p.model_path]);

function PendingLabel({ pending, label, waiting }: { pending: boolean; label: string; waiting: string }) {
  return <>{pending && <LoaderCircle className="is-spinning" size={16} aria-hidden="true" />}{pending ? waiting : label}</>;
}

function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    dialog?.showModal();
    return () => { dialog?.close(); trigger?.focus(); };
  }, []);
  return <dialog ref={ref} className="local-tools-dialog" aria-label={title} onCancel={(e) => { e.preventDefault(); e.stopPropagation(); onClose(); }}>
    <div className="local-tools-heading"><h2>{title}</h2><button type="button" onClick={onClose} aria-label={`关闭${title}`}><X size={20} /></button></div>
    {children}
  </dialog>;
}

function PreferencesDialog({ onClose }: { onClose: () => void }) {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [aiMessage, setAiMessage] = useState("");
  const [runtimeMessage, setRuntimeMessage] = useState("");
  const [checked, setChecked] = useState(false);
  const [tone, setTone] = useState<"success" | "error" | "neutral">("neutral");
  const [savedRuntime, setSavedRuntime] = useState("");
  useEffect(() => { localRequest<Preferences>("/preferences").then(p => { setPrefs(p); setSavedRuntime(runtimeSelection(p)); }).catch(e => setMessage(e.message)); }, []);
  const update = (value: Partial<Preferences>) => setPrefs(p => p ? { ...p, ...value } : p);
  const updateAI = (value: Partial<Preferences>) => { update(value); setChecked(false); setAiMessage(""); setTone("neutral"); };
  const run = async (name: string, section: "ai" | "runtime", action: () => Promise<void>) => {
    setBusy(name);
    if (section === "ai") { setAiMessage(""); setTone("neutral"); } else setRuntimeMessage("");
    try { await action(); } catch (e) {
      const text = e instanceof Error ? e.message : "暂时未完成，请重试。";
      if (section === "ai") { setAiMessage(text); setTone("error"); } else setRuntimeMessage(text);
    } finally { setBusy(""); }
  };
  const disabled = Boolean(busy);
  const runtimeChanged = Boolean(prefs && runtimeSelection(prefs) !== savedRuntime);
  return <Dialog title="本地设置" onClose={onClose}>
    {!prefs ? <div className="local-tools-body"><p role={message ? "alert" : "status"}>{!message && <LoaderCircle className="is-spinning" size={16} aria-hidden="true" />} {message || "正在读取设置…"}</p></div> : <div className="local-tools-body">
      <section aria-labelledby="proof-settings-title" aria-busy={disabled}><div className="local-tools-section-title"><h3 id="proof-settings-title">AI 校对</h3><span className={`local-tools-status ${checked ? "is-ready" : ""}`}>{checked ? <><Check size={14} aria-hidden="true" />已通过测试</> : "尚未测试"}</span></div>
        <p>检查明确错字，保留每一句原话。修改由你确认。</p>
        <label>兼容 API 地址<input type="url" placeholder="例如 https://api.deepseek.com" value={prefs.base_url} disabled={disabled} onChange={e => { updateAI({ base_url: e.target.value }); setModels([]); }} /></label>
        <label>API 密钥<input type="password" autoComplete="off" placeholder={prefs.has_api_key ? "已加密保存，留空保留" : "输入服务提供的密钥"} value={key} disabled={disabled} onChange={e => { setKey(e.target.value); setChecked(false); setAiMessage(""); }} /></label>
        <div className="local-model-field"><label htmlFor="proof-model">文字模型</label><button type="button" disabled={disabled || !prefs.base_url.trim()} onClick={() => void run("models", "ai", async () => {
          const data = await localRequest<{ models: string[] }>("/models", { base_url: prefs.base_url, api_key: key || null });
          setModels(data.models); setAiMessage(data.models.length ? `检测到 ${data.models.length} 个模型，选择后测试校对。` : "服务未返回模型，可手动填写后测试校对。");
        })}><PendingLabel pending={busy === "models"} label="检测模型" waiting="正在检测…" /></button>
        <input id="proof-model" list="proof-models" value={prefs.model} disabled={disabled} placeholder="搜索已检测模型，或手动填写" onChange={e => updateAI({ model: e.target.value })} aria-describedby="proof-model-hint" /></div>
        <datalist id="proof-models">{models.map(model => <option key={model} value={model} />)}</datalist>
        <p className="local-tools-note" id="proof-model-hint">模型列表可读取，并不代表校对可用。测试只发送一段示例文字。</p>
        <div className="local-tools-actions">
          <button disabled={disabled || !prefs.base_url.trim() || !prefs.model.trim()} type="button" onClick={() => void run("check", "ai", async () => {
            setChecked(false);
            const result = await localRequest<{ model: string; elapsed_ms: number }>("/proofreading-check", { base_url: prefs.base_url, api_key: key || null, model: prefs.model });
            setChecked(true); setTone("success"); setAiMessage(`校对验证通过 · ${(result.elapsed_ms / 1000).toFixed(1)} 秒。保存后即可使用。`);
          })}><PendingLabel pending={busy === "check"} label="测试校对" waiting="正在测试…" /></button>
          <button disabled={disabled || !prefs.base_url.trim() || !prefs.model.trim()} type="button" className="is-primary" onClick={() => void run("save-ai", "ai", async () => {
            const data = await localRequest<Pick<Preferences, "base_url" | "model" | "has_api_key">>("/preferences", { section: "proofreading", base_url: prefs.base_url, api_key: key || null, model: prefs.model }, "PATCH");
            update(data); setKey(""); setTone("success"); setAiMessage(checked ? "校对设置已保存，验证通过。" : "校对设置已保存。测试后可确认服务是否可用。");
          })}><PendingLabel pending={busy === "save-ai"} label="保存校对设置" waiting="正在保存…" /></button>
        </div>
        {aiMessage && <p className={`local-tools-message is-${tone}`} role={tone === "error" ? "alert" : "status"}>{aiMessage}</p>}
        <p className="local-tools-note">密钥在本机加密保存。开始校对时，原文会发送给所选服务，费用由该服务收取。</p>
      </section>
      <details className="local-runtime-settings"><summary>本地转写与运行组件<span>{prefs.device === "cuda" ? "CUDA" : prefs.device === "cpu" ? "CPU" : "自动"}</span></summary>
        <section><p>需要识别语音时，使用你选择的本地模型和设备。</p>
          <label>识别模型<select value={prefs.engine} disabled={disabled} onChange={e => update({ engine: e.target.value, model_path: prefs.default_paths[e.target.value === "sensevoice" ? `sensevoice_${prefs.device === "cuda" ? "cuda" : "cpu"}` : "faster_whisper"] })}><option value="sensevoice">SenseVoice Small</option><option value="faster_whisper">Whisper · 备用模型</option></select></label>
          <label>处理设备<select value={prefs.device} disabled={disabled} onChange={e => update({ device: e.target.value, model_path: prefs.default_paths[prefs.engine === "sensevoice" ? `sensevoice_${e.target.value === "cuda" ? "cuda" : "cpu"}` : "faster_whisper"] })}><option value="cuda" disabled={!prefs.cuda_available}>NVIDIA 显卡 · CUDA</option><option value="auto">自动选择</option><option value="cpu">CPU 兼容模式</option></select></label>
          <p className="local-tools-note">最近实际设备：{prefs.runtime.actual_device?.toUpperCase() || "尚未转写"}。显式 CUDA 模式不会自动退回 CPU。</p>
          {runtimeChanged && <p className="local-tools-message" role="status">先保存转写设置，再验证实际使用的设备。</p>}
          <details><summary>模型与运行组件目录</summary><label>本地模型目录<input value={prefs.model_path} disabled={disabled} onChange={e => update({ model_path: e.target.value })} list="local-model-paths" /></label>{prefs.engine === "sensevoice" && prefs.device === "cuda" && <label>CUDA 运行组件目录<input value={prefs.sdk_path} disabled={disabled} onChange={e => update({ sdk_path: e.target.value })} /></label>}</details>
          <datalist id="local-model-paths">{prefs.available_model_paths.map(p => <option key={p} value={p} />)}</datalist>
          <div className="local-tools-actions"><button disabled={disabled} type="button" onClick={() => void run("save-runtime", "runtime", async () => {
            const data = await localRequest<Preferences>("/preferences", { section: "transcription", device: prefs.device, engine: prefs.engine, sdk_path: prefs.sdk_path, model_path: prefs.model_path }, "PATCH");
            setPrefs(p => p ? { ...p, device: data.device, engine: data.engine, sdk_path: data.sdk_path, model_path: data.model_path, runtime: data.runtime } : p); setSavedRuntime(runtimeSelection(data)); setRuntimeMessage("本地转写设置已保存，将用于下一次转写。");
          })}><PendingLabel pending={busy === "save-runtime"} label="保存转写设置" waiting="正在保存…" /></button><button disabled={disabled || runtimeChanged || prefs.device !== "cuda"} type="button" onClick={() => void run("cuda", "runtime", async () => {
            const result = await localRequest<{ device: string; compute_type: string }>("/cuda-check", {});
            setRuntimeMessage(`真实推理验证通过：${result.device.toUpperCase()} · ${result.compute_type}`);
          })}><PendingLabel pending={busy === "cuda"} label="验证 CUDA" waiting="正在验证…" /></button></div>
          {runtimeMessage && <p className="local-tools-message" role="status">{runtimeMessage}</p>}
        </section>
      </details>
    </div>}
  </Dialog>;
}

export function LocalSettingsButton() {
  const [open, setOpen] = useState(false);
  return <><button type="button" className="local-settings-button" title="本地设置" aria-label="本地设置" onClick={() => setOpen(true)}><Settings2 size={18} aria-hidden="true" /></button>{open && createPortal(<PreferencesDialog onClose={() => setOpen(false)} />, document.body)}</>;
}

async function downloadText(captureId: string, version: string) {
  const data = await localRequest<{ text: string }>(`/captures/${captureId}/proofreading/text?version=${version}`);
  if (isDesktopApp()) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("save_text_export", { captureId, version, text: data.text });
    return;
  }
  const url = URL.createObjectURL(new Blob(["\uFEFF", data.text], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = `${captureId}_${version === "original" ? "原文" : "校对版"}.txt`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ProofreadingDialog({ captureId, onClose }: { captureId: string; onClose: () => void }) {
  const [data, setData] = useState<Proof>({ job: null, saved: null });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [showSaved, setShowSaved] = useState(false);
  const [page, setPage] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const running = data.job?.status === "running";
  const requestRevision = useRef(0);
  const load = async () => {
    const revision = ++requestRevision.current;
    const result = await localRequest<Proof>(`/captures/${captureId}/proofreading`);
    if (revision === requestRevision.current) setData(result);
  };
  useEffect(() => { setSelected(new Set()); setPage(0); }, [data.job?.id]);
  useEffect(() => { void load().catch(e => setMessage(e.message)); }, [captureId]);
  useEffect(() => {
    if (!running || busy) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await load(); } catch (e) { if (!stopped) setMessage(e instanceof Error ? e.message : "暂时无法读取进度，请稍后重试。"); }
      finally { if (!stopped) timer = setTimeout(() => void poll(), 1500); }
    };
    timer = setTimeout(() => void poll(), 1500);
    return () => { stopped = true; clearTimeout(timer); };
  }, [running, busy, captureId]);
  const run = async (name: string, action: () => Promise<void>) => {
    ++requestRevision.current;
    setBusy(name); setMessage(""); try { await action(); } catch (e) { setMessage(e instanceof Error ? e.message : "暂时未完成，原文已保留。"); } finally { setBusy(""); }
  };
  const edits = data.job?.edits || [];
  const resumable = ["failed", "cancelled", "interrupted"].includes(data.job?.status || "") && Boolean(data.job?.completed_chunks);
  return <Dialog title="校对原文" onClose={onClose}><div className="local-tools-body">
    <p>保留全部原话，不润色、不总结。AI 只能提出建议，确认后另存校对版。</p>
    <p className="local-tools-note">开始后，原文将发送至你配置的模型服务，可能产生该服务的费用。</p>
    <div className="local-tools-actions"><button disabled={running || Boolean(busy)} type="button" onClick={() => setSettingsOpen(true)}><Settings2 size={16} />配置模型</button>
      <button disabled={Boolean(busy) || running} type="button" className="is-primary" onClick={() => void run("start", async () => {
        const job = await localRequest<Job>(`/captures/${captureId}/proofreading`, {}); setData(d => ({ ...d, job })); setSelected(new Set()); setPage(0); setShowSaved(false);
      })}><PendingLabel pending={busy === "start" || running} label={resumable ? "继续校对" : "开始校对"} waiting="正在校对…" /></button>
      {running && <button disabled={Boolean(busy)} type="button" onClick={() => void run("cancel", async () => { await localRequest(`/captures/${captureId}/proofreading/cancel`, { job_id: data.job?.id }); await load(); })}><PendingLabel pending={busy === "cancel"} label="取消校对" waiting="正在取消…" /></button>}
    </div>
    {running && <div role="status"><progress value={data.job?.progress || 0} max={100} /><p>正在检查 · {data.job?.progress}%{data.job?.total_chunks ? ` · ${data.job.completed_chunks || 0}/${data.job.total_chunks} 段` : ""}</p><p className="local-tools-note">可以关闭窗口，完成部分会保存在本机。</p></div>}
    {data.job?.status === "failed" && <p role="alert">{data.job.message}</p>}
    {data.job?.status === "cancelled" && <p role="status">校对已取消，原文未改变。</p>}
    {data.job?.status === "interrupted" && <p role="status">{data.job.message} 点击继续校对。</p>}
    {data.job?.status === "ready" && <>
      <p role="status">{edits.length ? `${edits.length} 处候选修正，请逐处核对。` : "未发现符合保守规则的明确错误。"}</p>
      <div className="local-tools-edits">{edits.slice(page * 30, (page + 1) * 30).map((edit, n) => {
        const i = page * 30 + n;
        return <label key={i} className="local-tools-edit"><input type="checkbox" disabled={Boolean(busy)} aria-label={`将“${edit.before}”改为“${edit.after}”`} checked={selected.has(i)} onChange={e => setSelected(s => { const next = new Set(s); e.target.checked ? next.add(i) : next.delete(i); return next; })} />
          <span><del>{edit.before}</del><ins>{edit.after}</ins><small>{edit.reason}</small></span></label>;
      })}</div>
      {edits.length > 30 && <div className="local-tools-actions"><button disabled={!page} onClick={() => setPage(p => p-1)}>上一页</button><span>{page+1} / {Math.ceil(edits.length/30)}</span><button disabled={(page+1)*30 >= edits.length} onClick={() => setPage(p => p+1)}>下一页</button></div>}
      <button disabled={Boolean(busy)} type="button" className="is-primary" onClick={() => void run("accept", async () => {
        const saved = await localRequest<Saved>(`/captures/${captureId}/proofreading/accept`, { indices: Array.from(selected), job_id: data.job?.id }); setData(d => ({ ...d, saved })); setShowSaved(true); setMessage("校对版已单独保存，原文未改变。");
      })}><PendingLabel pending={busy === "accept"} label={`保存所选修正 · ${selected.size} 处`} waiting="正在保存…" /></button>
    </>}
    {data.saved && <section><h3>已保存校对版</h3><p>{data.saved.edits.length} 处修正 · {data.saved.saved_at}</p><button type="button" onClick={() => setShowSaved(v => !v)}>{showSaved ? "收起校对版" : "查看校对版"}</button>{showSaved && <pre className="local-tools-preview">{data.saved.text}</pre>}</section>}
    <div className="local-tools-actions"><button disabled={Boolean(busy)} type="button" onClick={() => void run("original", async () => { await downloadText(captureId, "original"); setMessage(isDesktopApp() ? "原文已保存到 下载/万象成文。" : "原文已下载。"); })}>{busy !== "original" && <Download size={16} aria-hidden="true" />}<PendingLabel pending={busy === "original"} label="下载原文" waiting="正在保存原文…" /></button><button disabled={Boolean(busy) || !data.saved} type="button" onClick={() => void run("corrected", async () => { await downloadText(captureId, "corrected"); setMessage(isDesktopApp() ? "校对版已保存到 下载/万象成文。" : "校对版已下载。"); })}>{busy !== "corrected" && <Download size={16} aria-hidden="true" />}<PendingLabel pending={busy === "corrected"} label="下载校对版" waiting="正在保存校对版…" /></button></div>
    {message && <p role="status" className="local-tools-message">{message}</p>}
  </div>{settingsOpen && <PreferencesDialog onClose={() => setSettingsOpen(false)} />}</Dialog>;
}

export function ProofreadingButton({ captureId }: { captureId: string }) {
  const [open, setOpen] = useState(false);
  return <><button type="button" className="copy-action" onClick={() => setOpen(true)}><Check size={15} />校对</button>{open && <ProofreadingDialog key={captureId} captureId={captureId} onClose={() => setOpen(false)} />}</>;
}

type SummaryPoint = { point: string; quote: string; start: number; end: number };
type SummaryState = { job: { id: string; status: string; progress: number; message: string } | null; saved: { overview: string; overview_ids: number[]; points: SummaryPoint[]; model: string } | null };

function SummaryDialog({ captureId, onClose }: { captureId: string; onClose: () => void }) {
  const [data, setData] = useState<SummaryState>({ job: null, saved: null });
  const [config, setConfig] = useState<{ base_url: string; model: string; config_id: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const running = data.job?.status === "running";
  const revision = useRef(0);
  const load = async () => {
    const current = ++revision.current;
    const result = await localRequest<SummaryState>(`/captures/${captureId}/summary`);
    if (current === revision.current) setData(result);
  };
  useEffect(() => {
    void load().catch(e => setMessage(e.message));
    setConfig(null);
    void localRequest<{ base_url: string; model: string; config_id: string }>("/summary/config").then(setConfig).catch(e => setMessage(e.message));
  }, [captureId, settingsOpen]);
  useEffect(() => {
    if (!running || busy) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await load(); } catch (e) { if (!cancelled) setMessage(e instanceof Error ? e.message : "无法读取进度"); }
      if (!cancelled) timer = setTimeout(() => void poll(), 1500);
    };
    timer = setTimeout(() => void poll(), 1500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [running, busy, captureId]);
  const run = async (action: () => Promise<void>) => {
    ++revision.current; setBusy(true); setMessage("");
    try { await action(); } catch (e) { setMessage(e instanceof Error ? e.message : "摘要未完成"); }
    finally { setBusy(false); }
  };
  return <Dialog title="摘要与要点" onClose={onClose}><div className="local-tools-body">
    <p>AI 根据原文生成要点，引用原文供核对；请以原文为准。</p>
    <p className="local-tools-note">点击生成后，全文将分段发送至 {config?.base_url || "尚未配置的模型服务"}（模型：{config?.model || "未配置"}），可能产生服务费用。原文不会被修改。</p>
    <div className="local-tools-actions">
      <button type="button" disabled={busy || running} onClick={() => setSettingsOpen(true)}><Settings2 size={16} />配置模型</button>
      <button type="button" className="is-primary" disabled={busy || running || !config} onClick={() => void run(async () => {
        const job = await localRequest<NonNullable<SummaryState["job"]>>(`/captures/${captureId}/summary`, { config_id: config?.config_id });
        setData(d => ({ ...d, job }));
      })}>{data.saved ? "重新生成" : "生成摘要"}</button>
      {running && <button type="button" disabled={busy} onClick={() => void run(async () => {
        await localRequest(`/captures/${captureId}/summary/cancel`, { job_id: data.job?.id }); await load();
      })}>取消摘要</button>}
    </div>
    {running && <div role="status"><progress value={data.job?.progress || 0} max={100} /><p>正在整理 · {data.job?.progress}%</p><p>取消会停止后续分段；当前已进入发送流程的请求可能仍会发出或完成，无法撤回。</p></div>}
    {data.job?.status === "failed" && <p role="alert">{data.job.message}</p>}
    {data.job?.status === "cancelled" && <p role="status">摘要已取消，原文未改变。</p>}
    {data.saved && <section><h3>{data.job?.status === "ready" ? "AI 生成的摘要与要点" : "上一次成功的摘要与要点"}</h3><p>{data.saved.overview}</p><details><summary>查看概述的原文依据</summary>{data.saved.overview_ids.map(i => <blockquote key={i}>{data.saved?.points[i]?.quote}</blockquote>)}</details>{data.saved.points.map((item, i) => <div className="local-tools-message" key={`${item.start}-${i}`}><p>{item.point}</p><details><summary>查看原文依据</summary><blockquote>{item.quote}</blockquote></details></div>)}</section>}
    {message && <p role="alert">{message}</p>}
  </div>{settingsOpen && <PreferencesDialog onClose={() => setSettingsOpen(false)} />}</Dialog>;
}

export function SummaryButton({ captureId }: { captureId: string }) {
  const [open, setOpen] = useState(false);
  return <><button type="button" className="copy-action" onClick={() => setOpen(true)}>AI 摘要</button>{open && <SummaryDialog key={captureId} captureId={captureId} onClose={() => setOpen(false)} />}</>;
}
