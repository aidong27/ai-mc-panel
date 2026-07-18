import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Package,
  PackagePlus,
  Search,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { api } from "../api";
import type { ModInfo, OperationRunner } from "../types";

interface ModsViewProps {
  refreshKey: number;
  operationBusy: boolean;
  onOperation: OperationRunner;
  onMessage: (message: string, tone?: "success" | "warning" | "error") => void;
}

function needsAttention(mod: ModInfo): boolean {
  const loader = mod.loader.toLowerCase();
  return mod.duplicate || mod.version.toLowerCase() === "unknown"
    || ["fabric", "quilt", "neoforge"].includes(loader);
}

export function ModsView({
  refreshKey,
  operationBusy,
  onOperation,
  onMessage,
}: ModsViewProps) {
  const [mods, setMods] = useState<ModInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [upload, setUpload] = useState<Record<string, unknown> | null>(null);
  const [uploading, setUploading] = useState(false);
  const [query, setQuery] = useState("");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [visibleCount, setVisibleCount] = useState(40);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setLoadError("");
    void api.get<ModInfo[]>("/mods")
      .then((items) => {
        if (!disposed) setMods(items);
      })
      .catch((error: unknown) => {
        if (!disposed) setLoadError(error instanceof Error ? error.message : "模组列表读取失败");
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => { disposed = true; };
  }, [refreshKey]);

  useEffect(() => setVisibleCount(40), [query, attentionOnly]);

  const attentionCount = useMemo(() => mods.filter(needsAttention).length, [mods]);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return mods.filter((mod) => {
      if (attentionOnly && !needsAttention(mod)) return false;
      if (!normalized) return true;
      return [mod.name, mod.filename, mod.version, mod.loader]
        .some((value) => value.toLowerCase().includes(normalized));
    });
  }, [attentionOnly, mods, query]);
  const visibleMods = filtered.slice(0, visibleCount);

  async function uploadFile(file: File) {
    setUploading(true);
    try {
      const result = await api.uploadMod(file);
      setUpload(result);
      onMessage("模组已进入隔离区，尚未安装", "success");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "上传失败", "error");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="view">
      <header className="page-heading">
        <div><p className="eyebrow">大型模组服</p><h1>模组管理</h1></div>
        <span className={`status-pill ${loadError ? "warning" : "healthy"}`} aria-live="polite">
          <Package size={17} />{loading ? "正在识别模组…" : loadError ? "模组读取失败" : `${mods.length} 个已识别模组`}
        </span>
      </header>

      <section className="upload-band">
        <div><PackagePlus size={24} /><span><strong>安装新模组</strong><small>文件会先进入隔离区检查，不会直接碰生产模组目录。</small></span></div>
        <label className="button primary file-button">
          <Upload size={18} />{uploading ? "检查中…" : "选择 JAR"}
          <input ref={inputRef} type="file" accept=".jar,application/java-archive" disabled={uploading || operationBusy} onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void uploadFile(file);
          }} />
        </label>
      </section>

      {upload && (
        <section className="panel-section upload-result">
          <CheckCircle2 size={22} />
          <div><h2>文件检查通过</h2><p>{String(upload.filename)} · {Math.round(Number(upload.size_bytes) / 1024)} KB</p><code>{String(upload.sha256)}</code></div>
          <button className="button primary" disabled={operationBusy} onClick={() => void onOperation("install_mod", { upload_id: upload.upload_id })}>查看安装影响</button>
        </section>
      )}

      <p className="notice warning"><ShieldCheck size={17} />面板不会自动更新、删除或替换模组。“需要留意”只是元数据线索，不代表已确认冲突。</p>

      <section className="panel-section mods-section">
        <div className="section-title-row compact"><div><p className="eyebrow">当前目录</p><h2>已安装模组</h2></div></div>
        <div className="mod-toolbar">
          <label className="search-field">
            <Search size={17} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索模组名、文件名或版本" />
            {query && <button className="icon-button embedded" onClick={() => setQuery("")} aria-label="清空搜索"><X size={16} /></button>}
          </label>
          <div className="segmented-control" aria-label="模组筛选">
            <button className={!attentionOnly ? "active" : ""} onClick={() => setAttentionOnly(false)}>全部 {mods.length}</button>
            <button className={attentionOnly ? "active" : ""} onClick={() => setAttentionOnly(true)}>需要留意 {attentionCount}</button>
          </div>
        </div>

        <div className="data-table mods-table">
          <div className="table-header"><span>模组</span><span>版本</span><span>加载器</span><span>状态</span><span>操作</span></div>
          {loading && <div className="table-row single-row"><span><strong>正在读取模组元数据…</strong><small>大型模组服可能需要几秒钟。</small></span></div>}
          {!loading && loadError && <div className="table-row single-row"><span><strong>暂时无法读取模组</strong><small>{loadError}</small></span></div>}
          {!loading && !loadError && visibleMods.map((mod) => (
            <div className="table-row" key={mod.id}>
              <span><strong>{mod.name}</strong><small>{mod.filename}</small></span>
              <span data-label="版本">{mod.version}</span>
              <span data-label="加载器">{mod.loader}</span>
              <span data-label="状态">
                <span className={needsAttention(mod) ? "small-badge warning" : "small-badge success"}>
                  {mod.duplicate ? "标识重复" : needsAttention(mod) ? "元数据待确认" : "已启用"}
                </span>
              </span>
              <span data-label="操作">{mod.enabled ? <button className="text-button danger-text" disabled={operationBusy} onClick={() => void onOperation("disable_mod", { mod_id: mod.id })}>停用</button> : "保留中"}</span>
            </div>
          ))}
        </div>
        {!loading && !loadError && filtered.length === 0 && (
          <p className="empty-copy mod-empty"><AlertTriangle size={17} />没有符合当前条件的模组。</p>
        )}
        {visibleMods.length < filtered.length && (
          <div className="load-more-row">
            <span>已显示 {visibleMods.length} / {filtered.length}</span>
            <button className="button secondary" onClick={() => setVisibleCount((value) => value + 40)}>继续显示</button>
          </div>
        )}
      </section>
    </div>
  );
}
