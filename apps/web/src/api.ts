import type { ApiEnvelope, OperationPreview, OperationResult, User } from "./types";

export const AUTH_EXPIRED_EVENT = "mc-panel-auth-expired";

export class ApiClientError extends Error {
  code: string;
  details: Record<string, unknown>;

  constructor(code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

class ApiClient {
  private csrfToken = "";

  setCsrfToken(token: string): void {
    this.csrfToken = token;
  }

  private async request<T>(path: string, init: RequestInit = {}, stateChange = false): Promise<T> {
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    if (stateChange) {
      headers.set("X-CSRF-Token", this.csrfToken);
    }
    const response = await fetch(`/api/v1${path}`, {
      ...init,
      headers,
      credentials: "same-origin",
    });
    let body: ApiEnvelope<T>;
    try {
      body = (await response.json()) as ApiEnvelope<T>;
    } catch {
      throw new ApiClientError(
        "invalid_server_response",
        response.ok ? "服务器返回了无法识别的内容" : "面板服务暂时异常，请稍后重试",
      );
    }
    if (!response.ok || !body.ok) {
      const code = body.error?.code ?? "request_failed";
      if (code === "authentication_required") {
        this.csrfToken = "";
        window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      }
      throw new ApiClientError(
        code,
        body.error?.message ?? "请求失败",
        body.error?.details,
      );
    }
    return body.data;
  }

  async login(username: string, password: string): Promise<User> {
    const data = await this.request<{ user: Omit<User, "csrf_token">; csrf_token: string }>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
    );
    this.csrfToken = data.csrf_token;
    return { ...data.user, csrf_token: data.csrf_token };
  }

  async me(): Promise<User> {
    const user = await this.request<User>("/auth/me");
    this.csrfToken = user.csrf_token;
    return user;
  }

  async logout(): Promise<{ logged_out: boolean }> {
    const result = await this.request<{ logged_out: boolean }>(
      "/auth/logout",
      { method: "POST" },
      true,
    );
    this.csrfToken = "";
    return result;
  }

  async changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<{ changed: boolean; csrf_token: string; sessions_revoked: boolean }> {
    const data = await this.request<{
      changed: boolean;
      csrf_token: string;
      sessions_revoked: boolean;
    }>(
      "/auth/change-password",
      {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      },
      true,
    );
    this.csrfToken = data.csrf_token;
    return data;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>(path);
  }

  mutate<T>(path: string, method: "POST" | "PATCH" | "DELETE", body?: unknown): Promise<T> {
    return this.request<T>(
      path,
      { method, body: body === undefined ? undefined : JSON.stringify(body) },
      true,
    );
  }

  requestOperation(action: string, params: Record<string, unknown> = {}): Promise<OperationPreview | OperationResult> {
    return this.mutate("/operations", "POST", { action, params });
  }

  confirm(
    confirmationId: string,
    second: boolean,
    serverName?: string,
  ): Promise<OperationResult> {
    const suffix = second ? "confirm-again" : "confirm";
    return this.mutate(`/confirmations/${confirmationId}/${suffix}`, "POST", {
      server_name: serverName ?? null,
    });
  }

  async uploadMod(file: File): Promise<Record<string, unknown>> {
    const form = new FormData();
    form.append("file", file);
    return this.request("/mods/uploads", { method: "POST", body: form }, true);
  }
}

export const api = new ApiClient();
