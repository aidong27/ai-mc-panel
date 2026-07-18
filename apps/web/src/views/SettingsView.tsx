import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Bot,
  CheckCircle2,
  KeyRound,
  RefreshCw,
  Save,
  ServerCog,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { api } from "../api";
import type { AiUsage, OperationRunner } from "../types";

interface SettingsViewProps {
  refreshKey: number;
  operationBusy: boolean;
  onOperation: OperationRunner;
  onMessage: (message: string, tone?: "success" | "warning" | "error") => void;
}

interface AiSettings {
  enabled: boolean;
  configured: boolean;
  api_base_url: string | null;
  api_key_configured: boolean;
  model: string | null;
  temperature: number;
  max_tokens: number;
  timeout_seconds: number;
  requests_per_minute: number;
  requests_per_day: number;
  tokens_per_day: number;
}

type PropertyValue = string | number | boolean;

interface PropertyDefinition {
  label: string;
  help: string;
}

const propertyDefinitions: Record<string, PropertyDefinition> = {
  difficulty: { label: "游戏难度", help: "普通适合大多数好友服；更改后通常立即生效。" },
  gamemode: { label: "默认游戏模式", help: "新玩家第一次进入时使用，不会强制改变现有玩家。" },
  "max-players": { label: "最大玩家数", help: "允许同时在线的人数上限；提高上限不会增加机器性能。" },
  pvp: { label: "玩家互相伤害", help: "关闭后玩家之间不能造成直接伤害。" },
  "view-distance": { label: "视野距离", help: "服务器发送给玩家的区块范围，越高越占用资源。" },
  "simulation-distance": { label: "活动距离", help: "生物、机器和红石保持活动的范围。" },
  "white-list": { label: "启用白名单", help: "开启后只有白名单里的玩家可以进入。" },
  motd: { label: "服务器列表名称", help: "朋友在 Minecraft 多人游戏列表里看到的文字。" },
};

const propertyOrder = Object.keys(propertyDefinitions);

function aiEditable(settings: AiSettings | null): Record<string, unknown> | null {
  if (!settings) return null;
  return {
    enabled: settings.enabled,
    model: settings.model ?? "",
    temperature: settings.temperature,
    max_tokens: settings.max_tokens,
    timeout_seconds: settings.timeout_seconds,
    requests_per_minute: settings.requests_per_minute,
    requests_per_day: settings.requests_per_day,
    tokens_per_day: settings.tokens_per_day,
  };
}

function percent(value: number, limit: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(limit) || limit <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((value / limit) * 100)));
}

export function SettingsView({
  refreshKey,
  operationBusy,
  onOperation,
  onMessage,
}: SettingsViewProps) {
  const [properties, setProperties] = useState<Record<string, PropertyValue>>({});
  const [originalProperties, setOriginalProperties] = useState<Record<string, PropertyValue>>({});
  const [ai, setAi] = useState<AiSettings | null>(null);
  const [originalAi, setOriginalAi] = useState<AiSettings | null>(null);
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [savingAi, setSavingAi] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setLoadError("");
    void Promise.all([
      api.get<Record<string, PropertyValue>>("/properties"),
      api.get<AiSettings>("/ai/settings"),
      api.get<AiUsage>("/ai/usage"),
    ]).then(
      ([nextProperties, nextAi, nextUsage]) => {
        if (disposed) return;
        setProperties(nextProperties);
        setOriginalProperties(nextProperties);
        setAi(nextAi);
        setOriginalAi(nextAi);
        setUsage(nextUsage);
      },
      (error: unknown) => {
        if (!disposed) setLoadError(error instanceof Error ? error.message : "设置读取失败");
      },
    ).finally(() => {
      if (!disposed) setLoading(false);
    });
    return () => { disposed = true; };
  }, [refreshKey, reloadKey]);

  const aiDirty = useMemo(
    () => JSON.stringify(aiEditable(ai)) !== JSON.stringify(aiEditable(originalAi)),
    [ai, originalAi],
  );

  async function saveAi(event: FormEvent) {
    event.preventDefault();
    if (!ai || savingAi || !aiDirty) return;
    setSavingAi(true);
    try {
      const updated = await api.mutate<AiSettings>("/ai/settings", "PATCH", aiEditable(ai));
      setAi(updated);
      setOriginalAi(updated);
      onMessage("AI 设置已保存，密钥没有经过浏览器", "success");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "AI 设置保存失败", "error");
    } finally {
      setSavingAi(false);
    }
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    if (changingPassword) return;
    if (newPassword.length < 12) {
      onMessage("新密码至少需要 12 个字符", "warning");
      return;
    }
    if (newPassword !== confirmPassword) {
      onMessage("两次输入的新密码不一致", "warning");
      return;
    }
    setChangingPassword(true);
    try {
      await api.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      onMessage("密码已更新，其他已登录设备已退出", "success");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "密码更新失败", "error");
    } finally {
      setChangingPassword(false);
    }
  }

  function updateProperty(key: string, value: PropertyValue) {
    setProperties((current) => ({ ...current, [key]: value }));
  }

  function propertyInput(key: string, value: PropertyValue) {
    if (typeof value === "boolean") {
      return (
        <label className="switch-control">
          <input type="checkbox" aria-label={propertyDefinitions[key]?.label ?? key} checked={value} onChange={(event) => updateProperty(key, event.target.checked)} />
          <span aria-hidden="true" />
          <strong>{value ? "已开启" : "已关闭"}</strong>
        </label>
      );
    }
    if (["max-players", "view-distance", "simulation-distance"].includes(key)) {
      return (
        <input
          type="number"
          min={key === "max-players" ? 1 : 2}
          max={key === "max-players" ? 100 : 32}
          value={Number(value)}
          onChange={(event) => updateProperty(key, Number(event.target.value))}
        />
      );
    }
    if (key === "difficulty") {
      return (
        <select value={String(value)} onChange={(event) => updateProperty(key, event.target.value)}>
          <option value="peaceful">和平</option><option value="easy">简单</option><option value="normal">普通</option><option value="hard">困难</option>
        </select>
      );
    }
    if (key === "gamemode") {
      return (
        <select value={String(value)} onChange={(event) => updateProperty(key, event.target.value)}>
          <option value="survival">生存</option><option value="creative">创造</option><option value="adventure">冒险</option><option value="spectator">旁观</option>
        </select>
      );
    }
    return <input value={String(value)} maxLength={120} onChange={(event) => updateProperty(key, event.target.value)} />;
  }

  const orderedProperties = propertyOrder.filter((key) => key in properties);
  const usedTokens = (usage?.input_tokens ?? 0) + (usage?.output_tokens ?? 0);

  return (
    <div className="view">
      <header className="page-heading">
        <div><p className="eyebrow">高级工具</p><h1>服务器设置</h1></div>
        <span className="status-pill healthy"><ShieldCheck size={17} />只开放安全字段</span>
      </header>

      {loadError && (
        <div className="load-error" role="alert">
          <span>{loadError}</span>
          <button className="button secondary compact-button" onClick={() => setReloadKey((value) => value + 1)}><RefreshCw size={15} />重试</button>
        </div>
      )}

      <section className="panel-section settings-section">
        <div className="section-title-row compact"><div><p className="eyebrow">游戏规则</p><h2>常用服务器设置</h2></div><ServerCog size={22} /></div>
        <p className="help-copy">面板只允许修改下列日常设置。端口、世界目录、Java 和 JVM 参数不会在这里出现。</p>
        {loading && orderedProperties.length === 0 ? <p className="empty-copy">正在读取安全设置…</p> : (
          <div className="property-list">
            {orderedProperties.map((key) => {
              const value = properties[key]!;
              const definition = propertyDefinitions[key]!;
              const dirty = value !== originalProperties[key];
              return (
                <div key={key} className={dirty ? "property-row dirty" : "property-row"}>
                  <span><strong>{definition.label}</strong><small>{definition.help}</small></span>
                  <span className="property-control">
                    {propertyInput(key, value)}
                    <button
                      className="button secondary compact-button"
                      disabled={!dirty || operationBusy}
                      onClick={() => void onOperation("edit_server_property", { key, value })}
                    >
                      {dirty ? <Save size={16} /> : <CheckCircle2 size={16} />}{dirty ? "保存更改" : "已保存"}
                    </button>
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {ai && (
        <section className="panel-section settings-section">
          <div className="section-title-row compact"><div><p className="eyebrow">AI 供应商</p><h2>DeepSeek / OpenAI 兼容接口</h2></div><Bot size={22} /></div>
          <p className="notice neutral"><ShieldCheck size={17} />API 地址和密钥只能在服务器的受限环境文件中配置，浏览器看不到完整密钥。</p>

          <div className="usage-summary" aria-label="AI 今日用量">
            <div><small>今日请求</small><strong>{usage?.request_count ?? 0} / {ai.requests_per_day}</strong><progress max={100} value={percent(usage?.request_count ?? 0, ai.requests_per_day)} /></div>
            <div><small>今日 Token</small><strong>{usedTokens.toLocaleString("zh-CN")} / {ai.tokens_per_day.toLocaleString("zh-CN")}</strong><progress max={100} value={percent(usedTokens, ai.tokens_per_day)} /></div>
          </div>

          <form className="settings-form" onSubmit={saveAi}>
            <label className="toggle-row"><span><strong>启用 AI 功能</strong><small>关闭后状态、玩家、日志和备份等传统功能保持正常。</small></span><input type="checkbox" checked={ai.enabled} onChange={(event) => setAi({ ...ai, enabled: event.target.checked })} /></label>
            <label>API 服务地址<input value={ai.api_base_url ?? ""} readOnly aria-readonly="true" placeholder="请在服务器环境文件中配置" /></label>
            <label>模型名称<input value={ai.model ?? ""} onChange={(event) => setAi({ ...ai, model: event.target.value })} placeholder="deepseek-chat" /></label>
            <div className="form-row three">
              <label>回答随机度<input type="number" min={0} max={2} step={0.1} value={ai.temperature} onChange={(event) => setAi({ ...ai, temperature: Number(event.target.value) })} /></label>
              <label>单次最大 Token<input type="number" min={64} max={4096} value={ai.max_tokens} onChange={(event) => setAi({ ...ai, max_tokens: Number(event.target.value) })} /></label>
              <label>请求超时秒数<input type="number" min={5} max={120} value={ai.timeout_seconds} onChange={(event) => setAi({ ...ai, timeout_seconds: Number(event.target.value) })} /></label>
            </div>
            <div className="form-row three">
              <label>每分钟请求<input type="number" min={1} max={30} value={ai.requests_per_minute} onChange={(event) => setAi({ ...ai, requests_per_minute: Number(event.target.value) })} /></label>
              <label>每日请求<input type="number" min={1} max={1000} value={ai.requests_per_day} onChange={(event) => setAi({ ...ai, requests_per_day: Number(event.target.value) })} /></label>
              <label>每日 Token<input type="number" min={1000} max={10000000} value={ai.tokens_per_day} onChange={(event) => setAi({ ...ai, tokens_per_day: Number(event.target.value) })} /></label>
            </div>
            <div className="settings-save-row">
              <span className={ai.api_key_configured ? "small-badge success" : "small-badge warning"}>{ai.api_key_configured ? "密钥已安全配置" : "服务器尚未配置密钥"}</span>
              <button className="button primary" type="submit" disabled={!aiDirty || savingAi}>{savingAi ? <RefreshCw className="spin" size={18} /> : <SlidersHorizontal size={18} />}{savingAi ? "保存中…" : aiDirty ? "保存 AI 设置" : "AI 设置已保存"}</button>
            </div>
          </form>
        </section>
      )}

      <section className="panel-section settings-section">
        <div className="section-title-row compact"><div><p className="eyebrow">账号安全</p><h2>更改登录密码</h2></div><KeyRound size={22} /></div>
        <p className="help-copy">新密码至少 12 个字符。修改后当前浏览器保持登录，其他已登录设备会被安全退出。</p>
        <form className="password-change-form" onSubmit={changePassword}>
          <label>当前密码<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label>
          <label>新密码<input type="password" autoComplete="new-password" minLength={12} maxLength={256} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label>
          <label>再输入一次<input type="password" autoComplete="new-password" minLength={12} maxLength={256} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required /></label>
          <button className="button secondary" type="submit" disabled={changingPassword || !currentPassword || !newPassword || !confirmPassword}>{changingPassword ? <RefreshCw className="spin" size={18} /> : <KeyRound size={18} />}{changingPassword ? "更新中…" : "更新密码"}</button>
        </form>
      </section>
    </div>
  );
}
