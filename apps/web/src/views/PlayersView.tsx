import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import {
  Activity,
  CalendarDays,
  Crown,
  RefreshCw,
  ShieldCheck,
  UserMinus,
  UserPlus,
  Users,
} from "lucide-react";
import { api } from "../api";
import { formatTimestamp } from "../format";
import type { OperationRunner, PlayerActivity, Players } from "../types";

interface PlayersViewProps {
  players: Players;
  refreshKey: number;
  operationBusy: boolean;
  onOperation: OperationRunner;
}

export function PlayersView({
  players,
  refreshKey,
  operationBusy,
  onOperation,
}: PlayersViewProps) {
  const [whitelist, setWhitelist] = useState<string[]>([]);
  const [operators, setOperators] = useState<string[]>([]);
  const [activity, setActivity] = useState<PlayerActivity | null>(null);
  const [whitelistEnabled, setWhitelistEnabled] = useState<boolean | null>(null);
  const [whitelistName, setWhitelistName] = useState("");
  const [operatorName, setOperatorName] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const playersUnknown = players.stale === true;

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setLoadError("");
    void Promise.all([
      api.get<string[]>("/whitelist"),
      api.get<string[]>("/operators"),
      api.get<PlayerActivity>("/server/player-activity?days=7"),
      api.get<Record<string, string | number | boolean>>("/properties"),
    ]).then(
      ([nextWhitelist, nextOperators, nextActivity, properties]) => {
        if (disposed) return;
        setWhitelist(nextWhitelist);
        setOperators(nextOperators);
        setActivity(nextActivity);
        setWhitelistEnabled(
          typeof properties["white-list"] === "boolean"
            ? properties["white-list"]
            : null,
        );
      },
      (error: unknown) => {
        if (!disposed) setLoadError(error instanceof Error ? error.message : "玩家信息读取失败");
      },
    ).finally(() => {
      if (!disposed) setLoading(false);
    });
    return () => { disposed = true; };
  }, [refreshKey, reloadKey]);

  async function addWhitelist(event: FormEvent) {
    event.preventDefault();
    if (await onOperation("add_whitelist_player", { player: whitelistName })) {
      setWhitelistName("");
    }
  }

  async function addOperator(event: FormEvent) {
    event.preventDefault();
    if (await onOperation("add_operator", { player: operatorName })) {
      setOperatorName("");
    }
  }

  const maxSessions = Math.max(1, ...(activity?.daily.map((item) => item.sessions) ?? [1]));

  return (
    <div className="view">
      <header className="page-heading">
        <div><p className="eyebrow">好友与权限</p><h1>玩家管理</h1></div>
        <span className={playersUnknown ? "status-pill warning" : "status-pill healthy"}>
          <Users size={17} />
          {playersUnknown ? "在线状态待确认" : `${players.online} / ${players.maximum} 在线`}
        </span>
      </header>

      {loadError && (
        <div className="load-error" role="alert">
          <span>{loadError}</span>
          <button className="button secondary compact-button" onClick={() => setReloadKey((value) => value + 1)}><RefreshCw size={15} />重试</button>
        </div>
      )}

      <section className="panel-section player-online-section">
        <div className="section-title-row compact">
          <div><p className="eyebrow">现在在线</p><h2>玩家列表</h2></div>
        </div>
        {playersUnknown ? (
          <p className="empty-copy">日志信息暂时不完整，面板不会把“未知”当成“无人在线”。</p>
        ) : players.players.length ? (
          <div className="player-table">
            {players.players.map((player) => (
              <div key={player}><span className="player-avatar">{player.slice(0, 1).toUpperCase()}</span><strong>{player}</strong><span className="small-badge success">在线</span></div>
            ))}
          </div>
        ) : <p className="empty-copy">当前没有玩家在线。</p>}
      </section>

      <section className="activity-section" aria-labelledby="activity-title">
        <div className="section-title-row compact">
          <div>
            <p className="eyebrow">最近 7 天</p>
            <h2 id="activity-title">
              {activity ? `${activity.unique_players} 位朋友，${activity.sessions} 次进入` : "玩家活跃"}
            </h2>
          </div>
          {activity?.history.persisted ? (
            <span className="small-badge success"><ShieldCheck size={14} />历史已保存</span>
          ) : <Activity size={22} />}
        </div>
        {loading && !activity ? (
          <p className="empty-copy">正在读取受限的登录记录…</p>
        ) : activity ? (
          <>
            {!activity.history.collector_healthy && (
              <p className="notice warning"><CalendarDays size={17} />活跃记录采集刚才出现异常；已保存的历史仍可查看，新记录可能延迟。</p>
            )}
            {!activity.coverage_complete && (
              <p className="notice neutral"><CalendarDays size={17} />已保存的记录没有完整覆盖 7 天；以下只展示能确认的部分，不会把缺失当成无人上线。</p>
            )}
            {activity.history.last_imported_at && (
              <p className="activity-history-meta">最近同步 {formatTimestamp(activity.history.last_imported_at)}</p>
            )}
            <div className="activity-days" aria-label="每日进入次数">
              {activity.daily.map((day) => {
                const height = day.sessions === 0 ? 6 : Math.max(18, Math.round((day.sessions / maxSessions) * 100));
                const style = { "--activity-height": `${height}%` } as CSSProperties;
                return (
                  <div key={day.date} className="activity-day">
                    <span className={day.sessions ? "activity-bar active" : "activity-bar"} style={style} title={`${day.sessions} 次进入`} />
                    <strong>{day.sessions}</strong>
                    <small>{new Date(`${day.date}T00:00:00`).toLocaleDateString("zh-CN", { weekday: "short" })}</small>
                  </div>
                );
              })}
            </div>
            {activity.recent_players.length > 0 ? (
              <div className="recent-player-list">
                {activity.recent_players.slice(0, 6).map((player) => (
                  <span key={player.name}><strong>{player.name}</strong><small>最近进入 {formatTimestamp(player.last_joined_at)}</small></span>
                ))}
              </div>
            ) : <p className="empty-copy">在可用日志中没有找到玩家进入记录。</p>}
          </>
        ) : null}
      </section>

      <div className="two-column-layout">
        <section className="panel-section">
          <div className="section-title-row compact">
            <div><p className="eyebrow">进入权限</p><h2>白名单</h2></div><ShieldCheck size={22} />
          </div>
          <p className="help-copy">白名单：只有名单里的人能进入服务器，可以防止陌生人加入。</p>
          <p className={whitelistEnabled ? "notice neutral" : "notice warning"}>
            {whitelistEnabled === null ? "暂时无法确认白名单开关。" : whitelistEnabled ? "白名单已启用。" : "白名单当前未启用，名单不会阻止陌生人进入。"}
          </p>
          <form className="inline-form" onSubmit={addWhitelist}>
            <input value={whitelistName} onChange={(event) => setWhitelistName(event.target.value)} placeholder="玩家名称" pattern="[A-Za-z0-9_]{3,16}" required />
            <button className="button primary" type="submit" disabled={operationBusy}><UserPlus size={18} />加入</button>
          </form>
          <div className="simple-list">
            {whitelist.length ? whitelist.map((player) => (
              <div key={player}><span>{player}</span><button className="icon-button" disabled={operationBusy} title="移出白名单" aria-label={`移出白名单 ${player}`} onClick={() => void onOperation("remove_whitelist_player", { player })}><UserMinus size={18} /></button></div>
            )) : <p className="empty-copy">白名单目前为空。</p>}
          </div>
        </section>

        <section className="panel-section">
          <div className="section-title-row compact">
            <div><p className="eyebrow">游戏管理员</p><h2>OP 权限</h2></div><Crown size={22} />
          </div>
          <p className="notice warning">当前服务器是离线模式，用户名可能被冒用。只给完全信任的人 OP。</p>
          <form className="inline-form" onSubmit={addOperator}>
            <input value={operatorName} onChange={(event) => setOperatorName(event.target.value)} placeholder="玩家名称" pattern="[A-Za-z0-9_]{3,16}" required />
            <button className="button primary" type="submit" disabled={operationBusy}><UserPlus size={18} />授予</button>
          </form>
          <div className="simple-list">
            {operators.length ? operators.map((player) => (
              <div key={player}><span>{player}</span><button className="icon-button" disabled={operationBusy} title="移除 OP" aria-label={`移除 OP ${player}`} onClick={() => void onOperation("remove_operator", { player })}><UserMinus size={18} /></button></div>
            )) : <p className="empty-copy">当前没有 OP 玩家。</p>}
          </div>
        </section>
      </div>
    </div>
  );
}
