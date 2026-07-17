import { useState, type FormEvent } from "react";
import { Box, Eye, EyeOff, LogIn, ShieldCheck } from "lucide-react";

interface LoginViewProps {
  busy: boolean;
  error: string;
  onLogin: (username: string, password: string) => Promise<void>;
}

export function LoginView({ busy, error, onLogin }: LoginViewProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    await onLogin(username, password);
  }

  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="login-title">
        <div className="brand-mark" aria-hidden="true">
          <Box size={28} strokeWidth={2.2} />
        </div>
        <p className="brand-name">方块管家</p>
        <h1 id="login-title">欢迎回来</h1>
        <p className="muted">登录后就能用普通中文管理你的 Minecraft 服务器。</p>

        <form onSubmit={submit} className="form-stack">
          <label>
            管理员账号
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="请输入账号"
              required
            />
          </label>
          <label>
            密码
            <span className="password-input">
              <input
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="请输入密码"
                required
              />
              <button
                type="button"
                className="icon-button embedded"
                onClick={() => setShowPassword((value) => !value)}
                aria-label={showPassword ? "隐藏密码" : "显示密码"}
                title={showPassword ? "隐藏密码" : "显示密码"}
              >
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </span>
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="button primary wide" type="submit" disabled={busy}>
            <LogIn size={18} />
            {busy ? "正在登录…" : "登录"}
          </button>
        </form>

        <p className="login-security">
          <ShieldCheck size={16} />
          登录会话通过加密连接传输，敏感操作需要再次确认
        </p>
      </section>
    </main>
  );
}
