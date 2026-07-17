import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock, History, RefreshCw, Search, XCircle } from "lucide-react";
import { api } from "../api";
import type { AuditEvent } from "../types";

interface AuditViewProps { refreshKey: number; }

const actionNames: Record<string, string> = {
  create_backup: "创建备份",
  restart_server: "重启服务器",
  stop_server: "停止服务器",
  start_server: "启动服务器",
  restore_backup: "恢复备份",
  edit_server_property: "修改服务器设置",
  send_console_command: "发送控制台命令",
  upload_mod: "上传模组",
  install_mod: "安装模组",
  disable_mod: "停用模组",
  update_ai_settings: "修改 AI 设置",
  change_password: "更改登录密码",
  add_whitelist_player: "加入白名单",
  remove_whitelist_player: "移出白名单",
  add_operator: "授予 OP 权限",
  remove_operator: "移除 OP 权限",
  set_backup_schedule: "修改备份计划",
};

const outcomeNames: Record<string, string> = {
  succeeded: "成功",
  quarantined: "已隔离检查",
  confirmation_required: "等待确认",
  second_confirmation_required: "等待二次确认",
  failed: "失败",
  rejected: "已拒绝",
  expired: "已过期",
};

function eventState(event: AuditEvent): "success" | "pending" | "failed" {
  if (["succeeded", "quarantined"].includes(event.outcome)) return "success";
  if (event.outcome.includes("required")) return "pending";
  return "failed";
}

export function AuditView({ refreshKey }: AuditViewProps) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [query, setQuery] = useState("");
  const [risk, setRisk] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setLoadError("");
    void api.get<AuditEvent[]>("/audit-events?limit=200").then(
      (items) => { if (!disposed) setEvents(items); },
      (error: unknown) => {
        if (!disposed) setLoadError(error instanceof Error ? error.message : "操作记录读取失败");
      },
    ).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [refreshKey, reloadKey]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return events.filter((event) => {
      if (risk && event.risk !== risk) return false;
      if (!normalized) return true;
      return [actionNames[event.action] ?? event.action, event.action, event.id, outcomeNames[event.outcome] ?? event.outcome]
        .some((value) => value.toLowerCase().includes(normalized));
    });
  }, [events, query, risk]);

  return (
    <div className="view">
      <header className="page-heading">
        <div><p className="eyebrow">可追踪、可核对</p><h1>操作记录</h1></div>
        <span className={loadError ? "status-pill warning" : "status-pill healthy"}><History size={17} />{loading ? "正在读取…" : `最近 ${events.length} 条`}</span>
      </header>
      <section className="panel-section">
        <p className="help-copy">记录谁在什么时候提出、确认和执行了什么。密码、API Key、Cookie 和完整日志不会出现在这里。</p>

        <div className="audit-toolbar">
          <label className="search-field">
            <Search size={17} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索操作、结果或记录号" />
          </label>
          <div className="segmented-control" aria-label="风险等级">
            {[
              { value: "", label: "全部" },
              { value: "low", label: "低风险" },
              { value: "medium", label: "中风险" },
              { value: "high", label: "高风险" },
            ].map((item) => <button key={item.value} className={risk === item.value ? "active" : ""} onClick={() => setRisk(item.value)}>{item.label}</button>)}
          </div>
        </div>

        {loadError && (
          <div className="load-error" role="alert"><span>{loadError}</span><button className="button secondary compact-button" onClick={() => setReloadKey((value) => value + 1)}><RefreshCw size={15} />重试</button></div>
        )}

        <div className="audit-list">
          {filtered.length ? filtered.map((event) => {
            const state = eventState(event);
            return (
              <div key={event.id}>
                <span className={`audit-icon ${state}`}>
                  {state === "success" ? <CheckCircle2 size={18} /> : state === "pending" ? <Clock size={18} /> : <XCircle size={18} />}
                </span>
                <span><strong>{actionNames[event.action] ?? event.action}</strong><small>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })} · {event.risk === "high" ? "高风险" : event.risk === "medium" ? "中风险" : "低风险"}</small></span>
                <span className="audit-outcome">{outcomeNames[event.outcome] ?? event.outcome}</span>
                <code title={event.id}>{event.id.slice(0, 18)}</code>
              </div>
            );
          }) : !loading && !loadError && <p className="empty-copy">{events.length ? "没有符合当前条件的记录。" : "还没有操作记录。"}</p>}
        </div>
      </section>
    </div>
  );
}
