import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  AlertTriangle,
  FileWarning,
  LockKeyhole,
  Radio,
  RefreshCw,
  Send,
  Terminal,
  X,
} from "lucide-react";
import { api } from "../api";
import { formatTimestamp } from "../format";
import type { OperationRunner } from "../types";

interface CrashReport {
  id: string;
  created_at: string | number;
  size_bytes: number;
  summary?: string;
  filename?: string;
}

interface CrashDetail extends CrashReport {
  excerpt?: string;
}

interface ServerToolsViewProps {
  refreshKey: number;
  operationBusy: boolean;
  consoleCommandsEnabled: boolean;
  onOperation: OperationRunner;
  onMessage: (message: string, tone?: "success" | "warning" | "error") => void;
}

type LogMode = "concise" | "all" | "warning" | "error";

function isRepetitiveInfo(line: string): boolean {
  return /\/INFO\].*AllTheLeaks/i.test(line);
}

function logQuery(mode: LogMode): string {
  if (mode === "warning") return "?limit=250&severity=WARN";
  if (mode === "error") return "?limit=250&severity=ERROR,FATAL";
  return "?limit=250";
}

export function ServerToolsView({
  refreshKey,
  operationBusy,
  consoleCommandsEnabled,
  onOperation,
  onMessage,
}: ServerToolsViewProps) {
  const [logs, setLogs] = useState<string[]>([]);
  const [crashes, setCrashes] = useState<CrashReport[]>([]);
  const [command, setCommand] = useState("");
  const [logMode, setLogMode] = useState<LogMode>("concise");
  const [connected, setConnected] = useState(false);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [logError, setLogError] = useState("");
  const [crashError, setCrashError] = useState("");
  const [loadingLogs, setLoadingLogs] = useState(true);
  const [selectedCrash, setSelectedCrash] = useState<CrashDetail | null>(null);
  const [crashDetailLoading, setCrashDetailLoading] = useState(false);
  const [crashDetailError, setCrashDetailError] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const modeRef = useRef<LogMode>(logMode);

  useEffect(() => { modeRef.current = logMode; }, [logMode]);

  const loadLogs = useCallback(async () => {
    setLoadingLogs(true);
    setLogError("");
    try {
      const data = await api.get<{ lines: string[] }>(`/logs/recent${logQuery(logMode)}`);
      setLogs(data.lines);
    } catch (error) {
      setLogError(error instanceof Error ? error.message : "日志读取失败");
    } finally {
      setLoadingLogs(false);
    }
  }, [logMode]);

  const loadCrashes = useCallback(async () => {
    setCrashError("");
    try {
      setCrashes(await api.get<CrashReport[]>("/crash-reports"));
    } catch (error) {
      setCrashError(error instanceof Error ? error.message : "崩溃报告读取失败");
    }
  }, []);

  useEffect(() => { void loadLogs(); }, [loadLogs, refreshKey]);
  useEffect(() => { void loadCrashes(); }, [loadCrashes, refreshKey]);

  useEffect(() => {
    let disposed = false;
    setConnected(false);
    void api.mutate<{ ticket: string }>("/auth/ws-ticket", "POST").then(
      ({ ticket }) => {
        if (disposed) return;
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws/console?ticket=${encodeURIComponent(ticket)}`);
        socketRef.current = socket;
        socket.onopen = () => {
          if (!disposed) setConnected(true);
        };
        socket.onclose = () => {
          if (disposed) return;
          setConnected(false);
          reconnectTimer.current = window.setTimeout(() => {
            setConnectionAttempt((value) => value + 1);
          }, 5_000);
        };
        socket.onmessage = (event) => {
          try {
            const payload = JSON.parse(String(event.data)) as { type?: unknown; lines?: unknown };
            if (
              payload.type === "logs"
              && Array.isArray(payload.lines)
              && payload.lines.every((line) => typeof line === "string")
              && ["concise", "all"].includes(modeRef.current)
            ) {
              setLogs(payload.lines);
            }
          } catch {
            setConnected(false);
            socket.close();
          }
        };
      },
      (error: unknown) => {
        if (!disposed) onMessage(error instanceof Error ? error.message : "实时日志连接失败", "warning");
      },
    );
    return () => {
      disposed = true;
      if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connectionAttempt, onMessage]);

  useEffect(() => {
    if (!selectedCrash) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !crashDetailLoading) setSelectedCrash(null);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [crashDetailLoading, selectedCrash]);

  const visibleLogs = useMemo(
    () => logMode === "concise" ? logs.filter((line) => !isRepetitiveInfo(line)) : logs,
    [logMode, logs],
  );
  const hiddenLogCount = logs.length - visibleLogs.length;
  const emptyLogMessage = loadingLogs
    ? "正在读取日志…"
    : logMode === "concise" && hiddenLogCount > 0
      ? "最近日志都是已隐藏的重复插件信息。服务器日志仍在正常更新，可切换到“全部”查看。"
      : "当前筛选条件下没有日志。";

  async function submitCommand(event: FormEvent) {
    event.preventDefault();
    const normalized = command.trim();
    if (!normalized) return;
    if (await onOperation("send_console_command", { command: normalized })) setCommand("");
  }

  async function openCrash(report: CrashReport) {
    setSelectedCrash(report);
    setCrashDetailLoading(true);
    setCrashDetailError("");
    try {
      const detail = await api.get<Partial<CrashDetail>>(`/crash-reports/${report.id}`);
      setSelectedCrash({ ...report, ...detail });
    } catch (error) {
      setCrashDetailError(error instanceof Error ? error.message : "崩溃报告读取失败");
    } finally {
      setCrashDetailLoading(false);
    }
  }

  function reconnect() {
    if (reconnectTimer.current !== null) window.clearTimeout(reconnectTimer.current);
    if (socketRef.current) socketRef.current.onclose = null;
    socketRef.current?.close();
    setConnectionAttempt((value) => value + 1);
  }

  return (
    <div className="view">
      <header className="page-heading">
        <div><p className="eyebrow">高级工具</p><h1>服务器工具</h1></div>
        <button className={connected ? "status-pill healthy connection-status" : "status-pill warning connection-status"} onClick={reconnect} title="重新连接实时日志">
          {connected ? <Radio size={17} /> : <Terminal size={17} />}{connected ? "实时日志已连接" : "普通日志模式"}
        </button>
      </header>

      <section className="panel-section console-section">
        <div className="section-title-row compact">
          <div><p className="eyebrow">实时控制台</p><h2>服务器输出</h2></div>
          <button className="icon-button" onClick={() => void loadLogs()} title="刷新日志" aria-label="刷新日志"><RefreshCw size={18} className={loadingLogs ? "spin" : ""} /></button>
        </div>
        <div className="console-toolbar">
          <div className="segmented-control" aria-label="日志显示方式">
            {[
              { value: "concise", label: "简洁" },
              { value: "all", label: "全部" },
              { value: "warning", label: "警告" },
              { value: "error", label: "错误" },
            ].map((item) => (
              <button key={item.value} className={logMode === item.value ? "active" : ""} onClick={() => setLogMode(item.value as LogMode)}>{item.label}</button>
            ))}
          </div>
          {logMode === "concise" && hiddenLogCount > 0 && <span className="filter-note">已隐藏 {hiddenLogCount} 条重复插件信息</span>}
        </div>
        {logError && <div className="load-error compact" role="alert"><span>{logError}</span><button className="text-button" onClick={() => void loadLogs()}>重试</button></div>}
        <pre className="console-output" aria-label="Minecraft 日志" aria-live="polite">{visibleLogs.join("\n") || emptyLogMessage}</pre>
        {consoleCommandsEnabled ? (
          <>
            <form className="console-command" onSubmit={submitCommand}>
              <input value={command} onChange={(event) => setCommand(event.target.value)} maxLength={200} placeholder="输入 Minecraft 控制台命令，例如：say 维护将在 5 分钟后开始" required />
              <button className="button primary" type="submit" disabled={operationBusy || !command.trim()}><Send size={18} />发送</button>
            </form>
            <p className="help-copy">命令只会发送给 Minecraft 控制台，不会进入 Linux Shell；确认页会逐字展示将发送的内容。</p>
          </>
        ) : (
          <p className="help-copy console-disabled"><LockKeyhole size={17} />通用命令入口已由服务器管理员关闭，日志读取不受影响。</p>
        )}
      </section>

      <section className="panel-section">
        <div className="section-title-row compact">
          <div><p className="eyebrow">故障记录</p><h2>崩溃报告</h2></div><FileWarning size={22} />
        </div>
        {crashError && <div className="load-error compact" role="alert"><span>{crashError}</span><button className="text-button" onClick={() => void loadCrashes()}>重试</button></div>}
        {crashes.length ? (
          <div className="report-list">
            {crashes.map((report) => (
              <div key={report.id}>
                <AlertTriangle size={19} />
                <span><strong>{report.summary ?? report.filename ?? "服务器崩溃"}</strong><small>{formatTimestamp(report.created_at)} · {Math.max(1, Math.round(report.size_bytes / 1024))} KB</small></span>
                <button className="text-button" onClick={() => void openCrash(report)}>查看</button>
              </div>
            ))}
          </div>
        ) : !crashError && <p className="empty-copy">没有发现崩溃报告。</p>}
      </section>

      {selectedCrash && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !crashDetailLoading) setSelectedCrash(null);
        }}>
          <section className="modal crash-modal" role="dialog" aria-modal="true" aria-labelledby="crash-title">
            <header className="modal-header">
              <span className="risk-icon high"><FileWarning size={22} /></span>
              <div><p className="eyebrow">崩溃报告</p><h2 id="crash-title">{selectedCrash.filename ?? "故障详情"}</h2></div>
              <button className="icon-button" onClick={() => setSelectedCrash(null)} disabled={crashDetailLoading} aria-label="关闭"><X size={20} /></button>
            </header>
            <p className="help-copy crash-meta">{formatTimestamp(selectedCrash.created_at)} · {Math.max(1, Math.round(selectedCrash.size_bytes / 1024))} KB</p>
            {crashDetailLoading ? <p className="empty-copy">正在安全读取报告…</p> : crashDetailError ? (
              <div className="load-error" role="alert"><span>{crashDetailError}</span><button className="text-button" onClick={() => void openCrash(selectedCrash)}>重试</button></div>
            ) : <pre className="crash-excerpt">{selectedCrash.excerpt ?? "报告中没有可显示的内容。"}</pre>}
          </section>
        </div>
      )}
    </div>
  );
}
