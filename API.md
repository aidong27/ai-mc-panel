# API

基础路径为 `/api/v1`。所有响应使用 JSON；下载日志或备份不属于 MVP。除登录和健康探针外，所有接口均要求认证。

## 通用响应

成功：

```json
{
  "ok": true,
  "data": {},
  "request_id": "req_..."
}
```

失败：

```json
{
  "ok": false,
  "error": {
    "code": "confirmation_required",
    "message": "需要确认后才能重启服务器",
    "details": {}
  },
  "request_id": "req_..."
}
```

错误信息面向用户时使用普通中文；内部异常和路径不直接返回前端。

## 认证

- `POST /auth/login`：登录，设置 opaque session Cookie。
- `POST /auth/logout`：撤销当前会话。
- `POST /auth/change-password`：验证当前密码、更换哈希并撤销其他会话。
- `GET /auth/me`：当前用户、角色和 CSRF token。
- `POST /auth/ws-ticket`：创建一次性 WebSocket ticket。

登录接口不返回会话 token。所有状态变更接口要求 `X-CSRF-Token`。

## 首页与状态

- `GET /dashboard`：总体状态、玩家、通俗资源结论、自动体检和最近风险提示。
- `GET /diagnostics`：不调用 AI 的确定性服务、磁盘、备份和玩家检查。
- `GET /server/status`：systemd、Minecraft 启动阶段、版本和受控运行信息。
- `GET /server/metrics`：CPU、内存、磁盘、TPS 的当前值和通俗分级。
- `GET /server/players`：在线人数和玩家名。
- `GET /server/player-activity?days=7`：最近 1 到 30 天的持久化登录活跃、覆盖范围和采集健康度；不保存或返回 IP。
- `GET /server/detection`：只读检测结果、每个版本字段的证据来源和 fingerprint；无法确认时返回 `unknown`。

指标为当前快照；MVP 不保存时序监控数据。

## 生命周期与控制台

- `POST /server/start`
- `POST /server/stop`
- `POST /server/restart`
- `POST /console/commands`：仅 owner，命令长度和字符严格限制。
- `POST /console/announcements`：发送普通游戏内公告。
- `GET /logs/recent?source=latest&limit=200&severity=WARN,ERROR`
- `GET /crash-reports`
- `GET /crash-reports/{report_id}`
- `WS /ws/console?ticket=...`

控制台 WebSocket 只发送服务端日志，不附着交互式 Shell 或 screen 终端。

## 配置与玩家权限

- `GET /properties`
- `PATCH /properties`：仅允许 schema 中列出的键。
- `GET /whitelist`
- `POST /whitelist/{player}`
- `DELETE /whitelist/{player}`
- `GET /operators`
- `POST /operators/{player}`
- `DELETE /operators/{player}`

路径中的玩家名必须符合 Minecraft 用户名规则，不能包含空格、斜杠、控制字符或命令片段。

## 模组

- `GET /mods`
- `POST /mods/uploads`：上传到隔离区，限制大小和扩展名。
- `POST /mods/{upload_id}/install`
- `POST /mods/{mod_id}/disable`

MVP 不提供模组重新启用或自动兼容修复，不允许后端按任意 URL 下载模组，也不自动替换重复项。

## 备份

- `POST /backups`
- `GET /backups`
- `POST /backups/{backup_id}/restore`
- `GET /backup-schedule`
- `GET /backup-status`：自动备份的上次结果、退出码、完成时间和下次执行时间。
- `PATCH /backup-schedule`

备份 ID 是后端根据固定备份目录产生的不透明编号，不是路径。恢复端点始终要求高风险二次确认。

## 确认状态机

- `POST /operations/preview`：生成动作预览和风险等级。
- `POST /operations`：提交动作；低风险执行，中高风险创建确认对象。
- `POST /confirmations/{id}/confirm`：第一次确认。
- `POST /confirmations/{id}/confirm-again`：高风险第二次确认。

确认对象绑定用户、会话、动作、规范化参数哈希和服务器 fingerprint，默认 5 分钟过期，不能跨动作重放。

## AI

- `POST /ai/chat`：普通对话或工具提议。
- `GET /ai/usage`：今日调用和 token 额度。
- `GET /ai/settings`：不含密钥的配置状态。
- `PATCH /ai/settings`：普通参数和 AI 开关；密钥只能在服务器文件配置。

AI 响应包含普通中文解释和只读工具证据；写工具只形成建议，实际提交仍走 `/operations` 与确认状态机。证据来自工具结果，不接受模型自报。

## 审计

- `GET /audit-events?limit=100`：owner 可查看最近的脱敏事件，最大 500 条。

审计接口不返回密码、密钥、Cookie、原始 Authorization、完整客户端 IP 或完整日志上下文。

## 限流

登录按 IP 与用户名组合限流；AI 有每分钟、每日请求数和每日 token 上限。MVP 尚未提供通用 `Idempotency-Key`，因此前端在操作进入执行状态后必须禁用重复提交。

## 主要错误码

- `authentication_required`
- `permission_denied`
- `csrf_failed`
- `origin_failed`
- `rate_limited`
- `validation_failed`
- `server_structure_changed`
- `confirmation_required`
- `confirmation_expired`
- `operation_in_progress`
- `insufficient_disk_space`
- `backup_verification_failed`
- `minecraft_unavailable`
- `ai_disabled`
- `ai_budget_exceeded`
- `provider_unavailable`
