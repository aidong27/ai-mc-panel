# Architecture

## 目标

面板必须让新手只看到“服务器是否正常、谁在线、我想做什么”，同时把 Linux、systemd、Forge、文件路径和权限细节限制在后端适配层。Minecraft 必须能在面板完全停止时独立运行。

## 总体结构

```text
Browser
  -> FastAPI session/API/WebSocket
       -> SQLite (users, sessions, confirmations, audit, AI usage)
       -> read adapters (bounded status, metrics, logs, metadata)
       -> agent service (OpenAI-compatible provider)
            -> typed tool registry
                 -> read adapters
                 -> confirmation gate
                 -> root-owned action helper
                       -> root-owned helper.json
                       -> systemd / screen / configured Minecraft roots

Minecraft service remains independent:
systemd -> screen -> existing start-server.sh -> existing Java/Forge server
```

AI 输出永远不是操作结果。只有 adapter 或 helper 返回的结构化结果可以把操作标记为成功。

## 组件

### Web

首页只显示：总体状态、玩家、通俗资源结论、少量大按钮和“你想做什么？”输入框。控制台、原始日志、JVM 参数、配置文件、API 设置和审计日志均位于“高级工具”。

界面使用简体中文，提供亮色/深色模式和手机布局。每个操作都显示用途、影响、风险、是否停服及恢复方法。专业术语必须附普通中文解释。

### API

FastAPI 负责认证、CSRF、授权、确认状态机、审计、速率限制、WebSocket、AI 编排和传统面板 API。AI 不可用时，除 AI 入口外的所有功能继续工作。

### SQLite

SQLite 保存：用户、密码哈希、会话哈希、确认挑战、审计事件、AI 用量、面板设置、玩家登录时间及覆盖区间和数据库版本。玩家活跃采集每 10 分钟执行一次，以事件哈希去重，仅保存玩家名和登录时间，最多保留 400 天。API 密钥、SSH 密码、Cookie 原文、IP 地址、Minecraft 世界内容均不进入数据库。

数据库启用 WAL、外键、busy timeout 和事务。迁移只向前执行；更新前复制数据库并记录校验值。

### Read adapters

只读适配器提供有上限的结构化信息：

- systemd 服务状态和进程指标。
- Minecraft 状态、玩家与 TPS。
- 最新日志尾部及崩溃报告摘要。
- 固定目录内的模组元数据和配置项。
- 备份清单、大小、时间和校验状态。
- 已配置备份 service/timer 的上次执行结果和下次计划时间。

适配器不得接受调用方传入的任意路径。日志读取使用固定文件、游标和最大字节数；模组读取只接受后端生成的对象 ID。

### Action helper

特权 helper 安装到 `/usr/local/libexec/mc-panel-action`，归 `root:root` 所有，普通用户不可写。sudoers 只允许 `mc-panel` 无参数执行该文件；请求通过标准输入传入 JSON。

helper 只接受枚举动作，使用结构化解析和 argv 数组调用系统程序。运行路径、unit、端口和 screen 会话来自 `/etc/mc-panel/helper.json`；该文件必须为 `root:root`、不可被组或其他用户写入，并经过字段、路径、unit、端口和 glob 校验。禁止 `shell=True`、`eval`、`exec`、Shell 插值、调用方路径和调用方环境变量。每个动作有超时、全局非阻塞文件锁和结构化结果；MVP 不宣称通用幂等键。

### Agent service

Agent 接收经过脱敏和压缩的上下文。工具 schema 与后端 Pydantic 模型共用语义；模型只提出工具调用，后端独立校验风险、权限、确认状态和服务器检测结果。

## Systemd 运行配置

`SystemdMinecraftAdapter` 管理一台本机 Minecraft 服务。以下值全部由 root 在安装时确认，不接受浏览器或 AI 输入：

- Minecraft 根目录、启动文件、systemd service 和游戏端口。
- 日志时区、控制台运行用户和 screen 会话名。
- 备份目录、归档文件 glob、备份命令、service 和 timer。
- 恢复目录、systemd drop-in 路径和可选版本提示。

适配器优先从受限日志和文件结构识别 Minecraft、Forge、NeoForge、Fabric、Quilt、Vanilla 与 Java；无法确认时返回 `unknown`，不会用默认版本伪装成功。配置提示会标注来源为 `configuration`。

每次生产写操作前重新探测并与 root 配置的批准 fingerprint 对比。service unit、根目录或启动文件变化时写操作会被拒绝；重新审计并显式更新批准值之前只能使用读取功能。helper 使用独立 root 配置，不信任 API 传入运行路径。

## 操作状态机

```text
requested -> validated -> low-risk executing -> succeeded | failed
                    \-> confirmation_required
medium: pending -> confirmed -> executing -> succeeded | failed
high:   pending -> first_confirmed -> second_confirmed -> executing -> succeeded | failed
```

低风险动作可跳过确认，但仍写审计。中风险需要一次确认。高风险需要二次确认、输入服务器名称和 5 分钟有效的确认记录。具体 helper 会在配置、模组、定时任务或恢复动作内部创建相应恢复点；白名单和 OP 等可逆控制台操作不伪造文件恢复点。

## 并发与锁

生产写操作由 root helper 的非阻塞文件锁串行化；SQLite 记录操作和确认状态，但不宣称数据库分布式锁。状态读取可以并发；第二个同时到达的写操作会收到 `operation_in_progress`，不会排队或并行修改服务器。

## 技术选择理由

- systemd：当前生产架构原生使用，新增服务不改变 Docker 或网络栈。
- FastAPI/Pydantic：适合严格请求和工具参数模型，OpenAPI 自动可审计。
- React/TypeScript：适合状态密集、移动端和确认流程，编译后无 Node 运行依赖。
- SQLite：单机 MVP 足够，备份和恢复简单，不新增数据库服务。
- opaque session：可撤销、可服务端失效，比把长期权限放入 JWT 更适合单机管理面板。

## 不采用的方案

- 不把 AI 文本传给 Shell。
- 不让 Web 服务以 root 运行。
- 不将面板并入 Minecraft systemd unit。
- 不启用 RCON 作为默认控制通道。
- 不安装 Docker 到当前生产机。
- 不直接公开面板端口。
- 不自动修复、更新、删除或重排模组。
