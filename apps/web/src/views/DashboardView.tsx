import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Archive,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Copy,
  Gauge,
  HardDrive,
  MemoryStick,
  PackagePlus,
  Play,
  RefreshCw,
  Send,
  Server,
  Share2,
  ShieldCheck,
  Square,
  Users,
  X,
} from "lucide-react";
import { api, ApiClientError } from "../api";
import type { AiReply, DashboardData, OperationPreview, OperationRunner, ViewId } from "../types";

interface DashboardViewProps {
  data: DashboardData;
  operationBusy: boolean;
  onNavigate: (view: ViewId) => void;
  onOperation: OperationRunner;
  onMessage: (message: string, tone?: "success" | "warning" | "error") => void;
}

function formatBytes(value: number): string {
  const gib = value / 1024 ** 3;
  return `${gib.toFixed(gib >= 10 ? 0 : 1)} GB`;
}

function percentage(used: number, total: number): number {
  if (!Number.isFinite(used) || !Number.isFinite(total) || total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((used / total) * 100)));
}

function knownIdentity(value: string): boolean {
  return !["", "unknown", "未知"].includes(value.trim().toLowerCase());
}

export function serverIdentityLabel(server: DashboardData["server"]): string {
  const parts = [
    knownIdentity(server.minecraft_version)
      ? `Minecraft ${server.minecraft_version}`
      : "Minecraft 版本待识别",
  ];
  if (knownIdentity(server.loader)) {
    parts.push(
      knownIdentity(server.loader_version)
        ? `${server.loader} ${server.loader_version}`
        : server.loader,
    );
  }
  return parts.join(" · ");
}

export function readableAiAnswer(value: string): string {
  return value
    .replace(/\*\*([^*\n]+)\*\*/g, "$1")
    .replace(/__([^_\n]+)__/g, "$1")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/^#{1,6}\s+/gm, "");
}

const evidenceNames: Record<string, string> = {
  get_server_status: "服务器状态",
  get_system_metrics: "机器资源",
  check_disk_space: "磁盘空间",
  list_players: "当前玩家",
  get_player_activity: "玩家活跃记录",
  list_backups: "备份列表",
  read_recent_logs: "最近日志",
  read_crash_report: "崩溃报告",
  check_mod_compatibility: "模组兼容线索",
};

export function DashboardView({
  data,
  operationBusy,
  onNavigate,
  onOperation,
  onMessage,
}: DashboardViewProps) {
  const [prompt, setPrompt] = useState("");
  const [asking, setAsking] = useState(false);
  const [reply, setReply] = useState<AiReply | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const inviteRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!inviteOpen) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setInviteOpen(false);
    }
    window.addEventListener("keydown", closeOnEscape);
    inviteRef.current?.focus();
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [inviteOpen]);

  const memoryPercent = percentage(data.metrics.memory_used_bytes, data.metrics.memory_total_bytes);
  const diskPercent = percentage(data.metrics.disk_used_bytes, data.metrics.disk_total_bytes);
  const playersUnknown = data.players.stale === true;

  async function askAi(event?: FormEvent) {
    event?.preventDefault();
    if (!prompt.trim()) return;
    setAsking(true);
    try {
      const response = await api.mutate<AiReply>("/ai/chat", "POST", { message: prompt.trim() });
      setReply(response);
    } catch (error) {
      if (error instanceof ApiClientError && error.code === "ai_disabled") {
        setReply({
          answer: "AI 功能当前关闭。状态、玩家、日志、备份和其他传统管理功能仍然可以正常使用。",
          evidence: [],
          proposed_actions: [],
          limits: {},
        });
      } else {
        onMessage(error instanceof Error ? error.message : "AI 暂时不可用", "error");
      }
    } finally {
      setAsking(false);
    }
  }

  async function copyAddress() {
    if (!data.connection_address) return;
    try {
      await navigator.clipboard.writeText(data.connection_address);
      onMessage("服务器地址已复制", "success");
    } catch {
      onMessage("浏览器没有允许自动复制，请手动选中地址", "warning");
    }
  }

  const lifecycleAction = data.server.state === "running" ? "stop_server" : "start_server";
  const LifecycleIcon = data.server.state === "running" ? Square : Play;

  return (
    <div className="view dashboard-view">
      <header className="page-heading">
        <div>
          <p className="eyebrow">{data.server_name}</p>
          <h1>服务器首页</h1>
        </div>
        <span className={data.server.healthy ? "status-pill healthy" : "status-pill warning"}>
          {data.server.healthy ? <CheckCircle2 size={17} /> : <CircleHelp size={17} />}
          {data.server.friendly_status}
        </span>
      </header>

      <section className={data.server.healthy ? "status-band healthy" : "status-band warning"}>
        <div className="status-main">
          <span className="status-symbol"><Server size={25} /></span>
          <div>
            <h2>{data.server.healthy ? "服务器正在稳定运行" : "服务器需要你的关注"}</h2>
            <p>{serverIdentityLabel(data.server)}</p>
          </div>
        </div>
        <button className="text-button" onClick={() => onNavigate("server")}>查看详情</button>
      </section>

      <section className={`care-overview ${data.diagnostics.level}`} aria-labelledby="care-title">
        <div className="care-heading">
          <span className="care-symbol"><ShieldCheck size={22} /></span>
          <div>
            <p className="eyebrow">自动体检</p>
            <h2 id="care-title">{data.diagnostics.headline}</h2>
          </div>
        </div>
        <div className="care-checks">
          {data.diagnostics.checks.map((check) => (
            <button key={check.id} className={`care-check ${check.tone}`} onClick={() => onNavigate(check.target)}>
              <span>
                <strong>{check.title}</strong>
                <small>{check.detail}</small>
              </span>
              <ChevronRight size={17} />
            </button>
          ))}
        </div>
        {data.diagnostics.signals.length > 0 && (
          <div className="care-signals">
            {data.diagnostics.signals.map((signal) => (
              <button key={signal.id} onClick={() => onNavigate(signal.target)}>{signal.title}</button>
            ))}
          </div>
        )}
      </section>

      <section className="ai-workspace" aria-labelledby="ai-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">AI 管家</p>
            <h2 id="ai-title">你想做什么？</h2>
          </div>
          <span className={data.ai.configured ? "small-badge success" : "small-badge neutral"}>
            {data.ai.configured ? "AI 已连接" : "AI 未配置"}
          </span>
        </div>
        <form className="ai-input-row" onSubmit={askAi}>
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="例如：为什么服务器这么卡？帮我看看刚才为什么崩服了。"
            rows={3}
          />
          <button className="button primary ai-send" type="submit" disabled={asking || !prompt.trim()}>
            {asking ? <RefreshCw className="spin" size={20} /> : <Send size={20} />}
            {asking ? "分析中" : "交给 AI"}
          </button>
        </form>
        <div className="prompt-examples" aria-label="示例问题">
          {["服务器为什么卡？", "过去一星期谁上线了？", "磁盘还够不够？", "看看最近报错"].map((item) => (
            <button key={item} onClick={() => setPrompt(item)}>{item}</button>
          ))}
        </div>
        {reply && (
          <div className="ai-answer" aria-live="polite">
            <span className="assistant-avatar"><Bot size={19} /></span>
            <div>
              <p>{readableAiAnswer(reply.answer)}</p>
              {reply.proposed_actions.length > 0 && (
                <div className="proposal-actions">
                  {reply.proposed_actions.map((proposal: OperationPreview) => (
                    <button
                      className="button secondary"
                      key={proposal.action}
                      disabled={operationBusy}
                      onClick={() => onOperation(proposal.action, proposal.params)}
                    >
                      查看并执行：{proposal.title}
                    </button>
                  ))}
                </div>
              )}
              {reply.evidence.length > 0 && (
                <details className="ai-evidence">
                  <summary>AI 查看了 {reply.evidence.length} 项实时信息</summary>
                  <ul>
                    {reply.evidence.map((item, index) => (
                      <li key={`${item.tool}-${index}`}>{evidenceNames[item.tool] ?? item.tool}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="dashboard-grid">
        <div className="player-summary panel-section">
          <div className="section-title-row compact">
            <div>
              <p className="eyebrow">当前玩家</p>
              <h2>
                {playersUnknown
                  ? "在线状态待确认"
                  : `${data.players.online} / ${data.players.maximum} 人在线`}
              </h2>
            </div>
            <Users size={24} />
          </div>
          {playersUnknown ? (
            <p className="empty-copy">暂时无法从日志确认在线玩家，面板不会猜测人数。</p>
          ) : data.players.players.length ? (
            <div className="player-list">
              {data.players.players.map((player) => <span key={player}>{player}</span>)}
            </div>
          ) : (
            <p className="empty-copy">现在没人在线，正适合做维护或备份。</p>
          )}
          <button className="text-button" onClick={() => onNavigate("players")}>管理玩家</button>
        </div>

        <div className="metrics-summary panel-section">
          <div className="section-title-row compact">
            <div>
              <p className="eyebrow">机器状态</p>
              <h2>{data.metrics.summary.performance}</h2>
            </div>
            <Gauge size={24} />
          </div>
          <div className="metric-row">
            <MemoryStick size={18} />
            <span><strong>{data.metrics.summary.memory}</strong><small>{memoryPercent}% 已使用</small></span>
            <progress max={100} value={memoryPercent} aria-label="内存使用率" />
          </div>
          <div className="metric-row">
            <HardDrive size={18} />
            <span><strong>{data.metrics.summary.disk}</strong><small>{formatBytes(data.metrics.disk_used_bytes)} 已使用</small></span>
            <progress max={100} value={diskPercent} aria-label="磁盘使用率" />
          </div>
        </div>
      </section>

      <section className="quick-actions" aria-labelledby="actions-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">常用操作</p>
            <h2 id="actions-title">一键管理</h2>
          </div>
        </div>
        <div className="action-grid">
          <button className="action-button" disabled={operationBusy} onClick={() => onOperation(lifecycleAction)}>
            <LifecycleIcon size={23} />
            <strong>{data.server.state === "running" ? "停止服务器" : "启动服务器"}</strong>
            <small>{data.server.state === "running" ? "玩家会断开，需要确认" : "加载世界和全部模组"}</small>
          </button>
          <button className="action-button" disabled={operationBusy} onClick={() => onOperation("restart_server")}>
            <RefreshCw size={23} />
            <strong>重启服务器</strong>
            <small>安全保存后重启，需要确认</small>
          </button>
          <button className="action-button" onClick={() => setInviteOpen(true)}>
            <Share2 size={23} />
            <strong>邀请朋友</strong>
            <small>复制服务器地址和进入说明</small>
          </button>
          <button className="action-button" onClick={() => onNavigate("players")}>
            <Users size={23} />
            <strong>查看玩家</strong>
            <small>在线玩家、白名单和管理员</small>
          </button>
          <button className="action-button" onClick={() => onNavigate("mods")}>
            <PackagePlus size={23} />
            <strong>安装模组</strong>
            <small>先检查，再确认安装</small>
          </button>
          <button className="action-button" disabled={operationBusy} onClick={() => onOperation("create_backup")}>
            <Archive size={23} />
            <strong>{operationBusy ? "正在处理…" : "创建备份"}</strong>
            <small>{operationBusy ? "请勿重复点击" : "会短暂停服约 1 到 2 分钟"}</small>
          </button>
        </div>
        <details className="inline-guide">
          <summary><CircleHelp size={17} />这些操作会发生什么？</summary>
          <p>备份相当于给世界创建存档点。重启、停服和修改设置会先展示影响并等待确认，恢复旧世界还需要第二次确认。</p>
        </details>
      </section>

      {inviteOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setInviteOpen(false);
        }}>
          <section ref={inviteRef} className="modal compact-modal" role="dialog" aria-modal="true" aria-labelledby="invite-title" tabIndex={-1}>
            <header className="modal-header">
              <span className="risk-icon low"><Share2 size={22} /></span>
              <div><p className="eyebrow">邀请朋友</p><h2 id="invite-title">服务器地址</h2></div>
              <button className="icon-button" onClick={() => setInviteOpen(false)} aria-label="关闭"><X size={20} /></button>
            </header>
            {data.connection_address ? (
              <div className="copy-address">
                <code>{data.connection_address}</code>
                <button className="button primary" onClick={copyAddress}><Copy size={18} />复制</button>
              </div>
            ) : (
              <p className="notice warning">管理员还没有配置对外服务器地址。游戏服务本身不受影响。</p>
            )}
            <p className="muted">这是 Java 版服务器。模组服玩家需要安装与服务器一致的客户端模组或整合包。</p>
          </section>
        </div>
      )}
    </div>
  );
}
