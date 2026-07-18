import { useEffect, useState } from "react";
import {
  Archive,
  Box,
  ChevronDown,
  FileTerminal,
  History,
  Home,
  LogOut,
  Moon,
  Package,
  Settings,
  Sun,
  Users,
} from "lucide-react";
import type { ViewId } from "../types";

interface SidebarProps {
  current: ViewId;
  theme: "light" | "dark";
  onNavigate: (view: ViewId) => void;
  onToggleTheme: () => void;
  onLogout: () => void;
}

const primaryItems: Array<{ id: ViewId; label: string; icon: typeof Home }> = [
  { id: "home", label: "首页", icon: Home },
  { id: "players", label: "玩家", icon: Users },
  { id: "mods", label: "模组", icon: Package },
  { id: "backups", label: "备份", icon: Archive },
];

const advancedItems: Array<{ id: ViewId; label: string; icon: typeof Home }> = [
  { id: "server", label: "服务器工具", icon: FileTerminal },
  { id: "settings", label: "设置", icon: Settings },
  { id: "audit", label: "操作记录", icon: History },
];

export function Sidebar({
  current,
  theme,
  onNavigate,
  onToggleTheme,
  onLogout,
}: SidebarProps) {
  const advancedActive = advancedItems.some((item) => item.id === current);
  const [advancedOpen, setAdvancedOpen] = useState(advancedActive);

  useEffect(() => {
    if (advancedActive) setAdvancedOpen(true);
  }, [advancedActive]);

  function navItem(item: (typeof primaryItems)[number]) {
    const Icon = item.icon;
    return (
      <button
        key={item.id}
        className={current === item.id ? "nav-item active" : "nav-item"}
        onClick={() => onNavigate(item.id)}
        aria-current={current === item.id ? "page" : undefined}
      >
        <Icon size={19} />
        <span>{item.label}</span>
      </button>
    );
  }

  return (
    <aside className="sidebar">
      <button className="sidebar-brand" onClick={() => onNavigate("home")}>
        <Box size={25} />
        <span>方块管家</span>
      </button>
      <nav aria-label="主要导航">
        <span className="nav-section-label">日常管理</span>
        {primaryItems.map(navItem)}
        <button
          className={advancedActive ? "nav-group-toggle active" : "nav-group-toggle"}
          onClick={() => setAdvancedOpen((value) => !value)}
          aria-expanded={advancedOpen}
        >
          <span>高级工具</span>
          <ChevronDown size={17} className={advancedOpen ? "expanded" : ""} />
        </button>
        {advancedOpen && <div className="advanced-nav">{advancedItems.map(navItem)}</div>}
      </nav>
      <div className="sidebar-footer">
        <button className="nav-item" onClick={onToggleTheme} title="切换明暗模式">
          {theme === "light" ? <Moon size={19} /> : <Sun size={19} />}
          <span>{theme === "light" ? "深色模式" : "亮色模式"}</span>
        </button>
        <button className="nav-item" onClick={onLogout}>
          <LogOut size={19} />
          <span>退出登录</span>
        </button>
      </div>
    </aside>
  );
}
