import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert, Clock3, LogOut, Menu, RefreshCw, UserRound, X } from "lucide-react";
import { api, ApiClientError, AUTH_EXPIRED_EVENT } from "./api";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { LoginView } from "./components/LoginView";
import { Sidebar } from "./components/Sidebar";
import type { DashboardData, OperationPreview, OperationResult, User, ViewId } from "./types";
import { AuditView } from "./views/AuditView";
import { BackupsView } from "./views/BackupsView";
import { DashboardView } from "./views/DashboardView";
import { ModsView } from "./views/ModsView";
import { PlayersView } from "./views/PlayersView";
import { ServerToolsView } from "./views/ServerToolsView";
import { SettingsView } from "./views/SettingsView";

interface Toast {
  message: string;
  tone: "success" | "warning" | "error";
}

interface RefreshOptions {
  silent?: boolean;
  cascade?: boolean;
}

function isConfirmation(value: OperationPreview | OperationResult): value is OperationPreview {
  return value.status === "confirmation_required" && "title" in value;
}

function initialTheme(): "light" | "dark" {
  try {
    const saved = window.localStorage?.getItem("mc-panel-theme");
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    // Theme persistence is optional; authentication and status must still load.
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function App() {
  const [booting, setBooting] = useState(true);
  const [user, setUser] = useState<User | null>(null);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [dashboardError, setDashboardError] = useState("");
  const [view, setView] = useState<ViewId>("home");
  const [theme, setTheme] = useState<"light" | "dark">(initialTheme);
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);
  const [pending, setPending] = useState<OperationPreview | null>(null);
  const [secondStep, setSecondStep] = useState(false);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [operationBusy, setOperationBusy] = useState(false);
  const operationLock = useRef(false);
  const confirmLock = useRef(false);
  const toastTimer = useRef<number | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage?.setItem("mc-panel-theme", theme);
    } catch {
      // The panel remains usable when the browser blocks local storage.
    }
  }, [theme]);

  const showMessage = useCallback((message: string, tone: Toast["tone"] = "success") => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    setToast({ message, tone });
    toastTimer.current = window.setTimeout(() => {
      setToast(null);
      toastTimer.current = null;
    }, 4200);
  }, []);

  useEffect(() => () => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
  }, []);

  useEffect(() => {
    function expireSession() {
      if (user) setLoginError("登录已过期，请重新登录。");
      setUser(null);
      setDashboard(null);
      setDashboardError("");
      setPending(null);
      setSecondStep(false);
      setView("home");
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, expireSession);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expireSession);
  }, [user]);

  const refresh = useCallback(async (options: RefreshOptions = {}): Promise<boolean> => {
    const { silent = false, cascade = true } = options;
    if (!silent) setRefreshing(true);
    try {
      const data = await api.get<DashboardData>("/dashboard");
      setDashboard(data);
      setDashboardError("");
      setLastUpdated(new Date());
      if (cascade) setRefreshKey((value) => value + 1);
      return true;
    } catch (error) {
      if (error instanceof ApiClientError && error.code === "authentication_required") {
        setUser(null);
        setDashboard(null);
        setDashboardError("");
      } else {
        const message = error instanceof Error ? error.message : "状态刷新失败";
        setDashboardError(message);
        if (!silent) showMessage(message, "error");
      }
      return false;
    } finally {
      if (!silent) setRefreshing(false);
    }
  }, [showMessage]);

  useEffect(() => {
    void api.me().then(
      async (currentUser) => {
        setUser(currentUser);
        await refresh({ cascade: true });
        setBooting(false);
      },
      () => setBooting(false),
    );
  }, [refresh]);

  useEffect(() => {
    if (!user) return;
    const timer = window.setInterval(() => {
      if (
        document.visibilityState === "visible"
        && !operationLock.current
        && !confirmLock.current
      ) {
        void refresh({ silent: true, cascade: false });
      }
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [refresh, user]);

  async function login(username: string, password: string) {
    setLoginBusy(true);
    setLoginError("");
    setDashboardError("");
    try {
      const currentUser = await api.login(username, password);
      setUser(currentUser);
      await refresh({ cascade: true });
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "登录失败");
    } finally {
      setLoginBusy(false);
    }
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      setUser(null);
      setDashboard(null);
      setDashboardError("");
      setView("home");
    }
  }

  async function runOperation(
    action: string,
    params: Record<string, unknown> = {},
  ): Promise<boolean> {
    if (operationLock.current || pending) {
      showMessage("已有操作正在处理，请稍候", "warning");
      return false;
    }
    operationLock.current = true;
    setOperationBusy(true);
    try {
      const result = await api.requestOperation(action, params);
      if (isConfirmation(result)) {
        setPending(result);
        setSecondStep(false);
        return true;
      }
      showMessage("操作已完成并通过后端验证", "success");
      await refresh({ cascade: true });
      return true;
    } catch (error) {
      showMessage(error instanceof Error ? error.message : "操作失败", "error");
      return false;
    } finally {
      operationLock.current = false;
      setOperationBusy(false);
    }
  }

  async function confirmOperation(serverName?: string) {
    if (!pending?.confirmation_id || confirmLock.current) return;
    confirmLock.current = true;
    setConfirmBusy(true);
    try {
      const result = await api.confirm(pending.confirmation_id, secondStep, serverName);
      if (result.status === "second_confirmation_required") {
        setSecondStep(true);
        return;
      }
      setPending(null);
      setSecondStep(false);
      showMessage("操作已完成并重新验证服务器状态", "success");
      await refresh({ cascade: true });
    } catch (error) {
      showMessage(error instanceof Error ? error.message : "确认失败", "error");
    } finally {
      confirmLock.current = false;
      setConfirmBusy(false);
    }
  }

  const title = useMemo(() => {
    const titles: Record<ViewId, string> = {
      home: "首页",
      players: "玩家",
      server: "服务器工具",
      mods: "模组",
      backups: "备份",
      settings: "设置",
      audit: "操作记录",
    };
    return titles[view];
  }, [view]);

  function navigate(next: ViewId) {
    setView(next);
    setMobileNav(false);
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }

  if (booting) {
    return <main className="boot-screen"><RefreshCw className="spin" size={26} /><p>正在安全读取服务器状态…</p></main>;
  }

  if (!user) {
    return <LoginView busy={loginBusy} error={loginError} onLogin={login} />;
  }

  if (!dashboard) {
    return (
      <main className="load-failure-page">
        <section className="load-failure-panel" role="alert">
          <span className="load-failure-icon"><CircleAlert size={26} /></span>
          <div><p className="eyebrow">登录已成功</p><h1>服务器状态暂时读不到</h1></div>
          <p>{dashboardError || "面板服务正在恢复，Minecraft 不依赖面板运行。"}</p>
          <div className="load-failure-actions">
            <button className="button primary" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={18} className={refreshing ? "spin" : ""} />{refreshing ? "重试中…" : "重试读取"}</button>
            <button className="button secondary" onClick={() => void logout()}><LogOut size={18} />退出登录</button>
          </div>
        </section>
      </main>
    );
  }

  let content;
  const operationUnavailable = operationBusy || pending !== null;
  switch (view) {
    case "players":
      content = <PlayersView players={dashboard.players} refreshKey={refreshKey} operationBusy={operationUnavailable} onOperation={runOperation} />;
      break;
    case "server":
      content = <ServerToolsView refreshKey={refreshKey} operationBusy={operationUnavailable} consoleCommandsEnabled={dashboard.capabilities.console_commands_enabled} onOperation={runOperation} onMessage={showMessage} />;
      break;
    case "mods":
      content = <ModsView refreshKey={refreshKey} operationBusy={operationUnavailable} onOperation={runOperation} onMessage={showMessage} />;
      break;
    case "backups":
      content = <BackupsView refreshKey={refreshKey} operationBusy={operationUnavailable} onOperation={runOperation} />;
      break;
    case "settings":
      content = <SettingsView refreshKey={refreshKey} operationBusy={operationUnavailable} onOperation={runOperation} onMessage={showMessage} />;
      break;
    case "audit":
      content = <AuditView refreshKey={refreshKey} />;
      break;
    default:
      content = <DashboardView data={dashboard} operationBusy={operationUnavailable} onNavigate={navigate} onOperation={runOperation} onMessage={showMessage} />;
  }

  return (
    <div className="app-shell">
      <div className={mobileNav ? "mobile-sidebar open" : "mobile-sidebar"}>
        <div className="mobile-backdrop" onClick={() => setMobileNav(false)} />
        <Sidebar current={view} theme={theme} onNavigate={navigate} onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")} onLogout={logout} />
      </div>
      <div className="desktop-sidebar">
        <Sidebar current={view} theme={theme} onNavigate={navigate} onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")} onLogout={logout} />
      </div>
      <main className="app-main" aria-busy={operationBusy || confirmBusy}>
        <header className="mobile-header">
          <button className="icon-button" onClick={() => setMobileNav(true)} aria-label="打开导航"><Menu size={21} /></button>
          <strong>{title}</strong>
          <button className="icon-button" onClick={() => void refresh()} aria-label="刷新状态"><RefreshCw size={19} className={refreshing ? "spin" : ""} /></button>
        </header>
        <div className="top-utility">
          {lastUpdated && <span className="freshness"><Clock3 size={15} />{lastUpdated.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })} 已更新</span>}
          <button className="refresh-button" onClick={() => void refresh()} disabled={refreshing}><RefreshCw size={17} className={refreshing ? "spin" : ""} />{refreshing ? "刷新中" : "刷新状态"}</button>
          <span className="user-chip"><UserRound size={17} />{user.username}</span>
        </div>
        {content}
      </main>

      {pending && (
        <ConfirmDialog
          operation={pending}
          secondStep={secondStep}
          serverName={dashboard.server_name}
          busy={confirmBusy}
          onCancel={() => { setPending(null); setSecondStep(false); }}
          onConfirm={confirmOperation}
        />
      )}

      {toast && (
        <div className={`toast ${toast.tone}`} role="status">
          <span>{toast.message}</span>
          <button className="icon-button" onClick={() => setToast(null)} aria-label="关闭提示"><X size={17} /></button>
        </div>
      )}
    </div>
  );
}
