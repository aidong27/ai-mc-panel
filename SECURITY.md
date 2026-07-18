# Security

## 安全目标

1. 面板被攻破时，不应获得任意 root Shell。
2. AI 被提示注入时，只能调用其角色允许的固定工具。
3. 用户误操作时，中高风险动作可拦截、追踪和回滚。
4. 密钥、密码、Cookie 和敏感日志不进入前端或普通日志。
5. 面板停止、崩溃或卸载时，Minecraft 继续独立运行。

## 部署前审计

每台主机的 SSH、sudoers、Minecraft 身份模式、文件权限和备份实现都不同。部署者必须先完成只读审计，确认现有备份可恢复，并检查计划授予面板的每一条 ACL 和 systemd 路径。项目不会自动修改 SSH、防火墙、云安全组、Java、服务端核心、模组或世界。

离线模式服务器中的 Minecraft 用户名不能视为强身份。现有备份命令若使用不安全临时文件、跟随软链接或缺少完整性校验，不得直接授权给 helper。

## 身份认证

- 首个管理员通过服务器本地 CLI 创建，不提供默认密码。
- 密码使用 Argon2id，记录算法参数以支持重哈希。
- 会话令牌使用 CSPRNG 生成，数据库只存 SHA-256 哈希。
- Cookie 为 HttpOnly、SameSite=Strict、Path=/；HTTPS 模式必须设置 Secure。
- 会话有空闲超时和绝对超时，退出后立即撤销。
- 登录按 IP 和规范化用户名限速；15 分钟内 5 次失败后拒绝继续尝试。
- MVP 只有 `owner` 和 `viewer` 两个角色；危险操作仅 owner 可用。

## CSRF、XSS 与 WebSocket

- 所有改变状态的浏览器请求要求 CSRF token；带有 Origin 时必须同源。生产环境强制要求 Origin 的收口仍在后续安全迭代中。
- WebSocket 握手校验会话、Origin 和一次性短期 ticket。
- React 不使用 `dangerouslySetInnerHTML` 展示日志或 AI 内容。
- AI 返回按纯文本或受限 Markdown 渲染，禁止 HTML、脚本和危险链接协议。
- 响应设置 CSP、frame-ancestors、nosniff、Referrer-Policy 和严格缓存头。

## 公网入口

- API 默认只监听 `127.0.0.1:18080`，公网不应绕过反向代理直连 API。
- 公开访问必须使用受信任 HTTPS 证书，并把 `MC_PANEL_PUBLIC_ORIGIN` 设置为唯一规范 Origin。
- 生产 Cookie 强制 Secure、HttpOnly 和 SameSite=Strict。
- `MC_PANEL_PUBLIC_ORIGIN` 只接受不带路径和凭据的 HTTPS Origin，WebSocket 仅放行该 Origin 与本机管理 Origin。
- 示例 Caddy/HAProxy 配置不会自动安装、开放端口或修改防火墙；部署者必须单独审核网络变更。

## 权限边界

`mc-panel` 是无登录、无 sudo 组、无 Minecraft 组的服务用户。API 进程不以 root 运行。只读部署的 systemd 单元启用：

- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `PrivateDevices=true`
- `ProtectKernelTunables=true`
- `ProtectKernelModules=true`
- `ProtectControlGroups=true`
- `RestrictSUIDSGID=true`
- `LockPersonality=true`
- `MemoryDenyWriteExecute=true`
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`

API 进程只拥有 `/var/lib/mc-panel` 和 `/run/mc-panel` 的 Unix 写权限。只读安装不会把 Minecraft 路径加入 `ReadWritePaths`。启用 helper 时，脚本从 root 所有的 `helper.json` 生成精确 systemd drop-in，并只把 `NoNewPrivileges` 改为允许固定 sudo 程序工作；`mc-panel` Unix 用户仍没有这些路径的直接写权限，其他 hardening 保持不变。

## Helper 规则

- root 所有、0755、父目录不可由 `mc-panel` 写入。
- `/etc/mc-panel/helper.json` 必须为 `root:root`、0600、普通文件；额外字段、相对路径、空白路径、路径穿越、非法 unit、端口、账号、screen 名称和 glob 全部拒绝。
- 输入最大 64 KiB，只接受单个 JSON 对象。
- operation 必须在编译时枚举中。
- 所有字符串限制长度、字符集和换行；所有数值限制范围。
- 路径由后端对象 ID 映射，调用方不得提供绝对路径或 `..`。
- 文件对象由后端 ID 映射到固定根目录，关键根目录和目标对象会检查类型并拒绝软链接；不接受用户路径。
- 子进程使用 argv 数组、清空环境、固定 PATH、超时和输出上限。
- `server.properties` 使用同目录临时文件、fsync、原子替换并保持所有者和模式；模组使用校验后的 staging 与原子移动，systemd drop-in 失败时恢复原文件。
- 备份校验文件和归档通过 `O_NOFOLLOW` 文件描述符有界读取；校验凭据保存在备份根目录下 root 所有的 `.mc-panel-verifications/`。
- 校验凭据绑定大小、mtime、ctime、设备、inode 和 SHA-256；恢复从同一已验证文件身份提取，文件被替换或修改即拒绝。
- helper 不提供通用 `run`、`shell`、`read_file`、`write_file` 或 `delete` 动作。

## 风险等级

低风险：状态、指标、玩家、有限日志、模组元数据、备份清单、创建备份、发送公告。创建备份虽为低风险，但当前冷备会短暂停服，界面必须提前展示。

中风险：启动、停止、重启、修改允许的 properties、白名单、OP、上传/停用模组、调整定时备份，以及显式启用后的手动通用控制台。需要明确确认和影响摘要。

高风险：恢复备份、覆盖世界、服务端核心或 Minecraft/Java 主版本变更、批量模组变更、系统权限或防火墙变更。需要二次确认、自动恢复点和验证。MVP 不提供删除世界、替换核心或主版本升级工具。

## 知情确认

- 中高风险操作的标题、精确目标、影响、停服状态、恢复方式和预期核对方式均由后端生成 `OperationReview`，传统 UI 与 AI 提案复用同一流程。
- 规范化参数和 review 作为一个冻结对象写入确认记录并整体哈希；review 另含 action + params 哈希。确认 ID 默认 5 分钟过期，参数或 review 变化后拒绝执行。
- 控制台命令保留规范化后的原始字符并逐字展示；玩家、属性、模组和备份均展示后端解析出的具体对象。

## AI 隔离

- 系统提示、工具 schema 和后端权限是不同层；提示文本不是安全边界。
- 日志、模组描述和玩家聊天均视为不可信内容，不得改变工具权限。
- 模型不可看到 API 密钥、SSH 信息、Cookie、Token、完整 IP 或密码。
- 工具显式标记为只读、AI 可提议或仅手动；只有前两类可进入模型 schema。`send_console_command` 为仅手动工具，生产默认关闭。
- 工具调用必须经过服务端 schema、授权、风险、确认和 fingerprint 五重校验。
- 模型声称成功不改变状态；只有后端工具的结构化结果可以完成操作。确认页会说明预期核对方式，但统一的结果保证等级尚未实现，调用方不能把所有 `succeeded` 都解释为已验证外部效果。
- AI 开关关闭或供应商不可用时，传统 API 保持工作。

## 密钥和脱敏

- `/etc/mc-panel/panel.env`：`root:mc-panel`，0640；服务用户只读。
- 前端仅能看到 `configured: true/false` 和掩码后的供应商信息。
- 日志过滤 Authorization、Cookie、API key、密码、Token、URL 凭据、IPv4/IPv6 和常见私钥格式。
- 请求体和环境变量默认不记录。
- 审计日志记录配置字段名和新值摘要，不记录 secret 值。
- 玩家活跃表只保存 Minecraft 玩家名和登录时间；日志中的 IP、端口和原始行不写入 SQLite，API 也不返回它们。

## AI 用量保护

- 每请求输入字符、上下文条数、max tokens 和超时上限。
- 每分钟请求数、每日请求数、每日输入/输出 token 总量。
- 每分钟请求数、每日请求数和每日 token 任一达到上限即拒绝后续调用。
- MVP 使用一个由 root 指定的模型，不自动切换到更昂贵模型。
- 日志先本地筛选、截断和脱敏，禁止整份大日志上传。

## 审计不可抵赖范围

每次操作记录：事件 ID、UTC 时间、用户 ID、请求 ID、动作、风险、确认 ID、参数脱敏摘要和结果摘要。SQLite 审计事件通过 API 只读展示；MVP 尚未实现外部 hash chain、远程不可变日志或来源 IP 字段，因此具备可追踪性，但不宣称达到强不可抵赖。

## 漏洞报告

请使用 GitHub 的 [Private vulnerability reporting](https://github.com/aidong27/ai-mc-panel/security/advisories/new) 私下报告安全问题，不要先公开 Issue、利用代码、生产地址、凭据或日志。

报告应包含受影响版本、复现步骤、预期影响和建议修复方向。维护者会先确认接收，再协调修复、回归测试和披露时间。

| 版本 | 安全更新 |
|---|---|
| `0.4.x` | 支持 |
| `< 0.4.0` | 仅供历史迁移，不再建议公开部署 |
