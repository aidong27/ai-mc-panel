# Deployment

本文描述单机 systemd 适配器的安全部署流程。安装脚本默认只建立读取能力，不停止或重启 Minecraft，也不会修改防火墙、SSH、Java、服务端核心、模组或世界。

## 已验证平台

- Ubuntu 24.04 x86_64。
- Python 3.12、systemd、ACL、screen 控制台。
- 已存在且独立工作的 Minecraft systemd service。
- 已存在的一致性备份命令、备份目录和 checksum sidecar。

其他发行版、tmux、RCON、Docker Minecraft 或 ARM 主机尚未完成生产验收。

## 运行配置

安装者必须明确确认以下值：

| 变量 | 示例 | 说明 |
|---|---|---|
| `MC_PANEL_SERVER_ROOT` | `/srv/minecraft/server` | 服务器工作目录 |
| `MC_PANEL_SERVER_STARTUP_PATH` | `/srv/minecraft/server/start.sh` | fingerprint 保护的启动文件 |
| `MC_PANEL_SERVER_SERVICE` | `minecraft.service` | 现有 systemd service |
| `MC_PANEL_SERVER_PORT` | `25565` | 本机就绪探针端口 |
| `MC_PANEL_SERVER_TIMEZONE` | `Asia/Shanghai` | Minecraft 日志时间所用 IANA 时区 |
| `MC_PANEL_BACKUP_ROOT` | `/srv/minecraft/backups` | 已有备份目录 |
| `MC_PANEL_BACKUP_ARCHIVE_GLOB` | `minecraft-*.tar.gz` | 只允许文件名 glob |
| `MC_PANEL_BACKUP_COMMAND` | `/usr/local/bin/minecraft-backup` | 已审计的一致性备份程序 |
| `MC_PANEL_BACKUP_SERVICE` | `minecraft-backup.service` | 备份 service |
| `MC_PANEL_BACKUP_TIMER` | `minecraft-backup.timer` | 备份 timer |
| `MC_PANEL_CONSOLE_USER` | `minecraft` | screen 所属 Unix 用户 |
| `MC_PANEL_CONSOLE_SCREEN` | `minecraft` | screen 会话名 |
| `MC_PANEL_CONSOLE_COMMANDS_ENABLED` | `false` | 是否开放仅手动的通用 Minecraft 控制台；生产默认关闭且不暴露给 AI |

路径、unit、账号、端口和 glob 都会严格校验。浏览器和 AI 不能修改这些值。

## 阶段一：只读检查

在项目根目录执行，命令只读取状态：

```bash
sudo env \
  MC_PANEL_SERVER_ROOT=/srv/minecraft/server \
  MC_PANEL_SERVER_STARTUP_PATH=/srv/minecraft/server/start.sh \
  MC_PANEL_SERVER_SERVICE=minecraft.service \
  MC_PANEL_SERVER_PORT=25565 \
  MC_PANEL_BACKUP_ROOT=/srv/minecraft/backups \
  MC_PANEL_BACKUP_ARCHIVE_GLOB='minecraft-*.tar.gz' \
  MC_PANEL_BACKUP_TIMER=minecraft-backup.timer \
  ./deploy/scripts/preflight-readonly.sh
```

人工核对输出中的 service、启动文件哈希、日志、备份数量、磁盘和监听端口。发现未知结构时停止安装。

## 构建发布包

在受信任开发机执行：

```bash
./deploy/scripts/build-release.sh 0.4.2
cd release
shasum -a 256 -c mc-panel-0.4.2.tar.gz.sha256
cd ..
```

构建会执行后端格式、lint、strict mypy、pytest、前端测试和生产构建，然后生成 Linux x86_64 离线 wheelhouse、文件 SHA256 清单和归档。`.env`、数据库、日志、证书、密钥、本地生产记录和历史发布包会被排除。

## 只读安装

把解压后的 release 放到服务器临时目录，检查 `SHA256SUMS`，然后显式传入运行配置：

```bash
sudo env \
  MC_PANEL_APPROVE_INSTALL=YES \
  MC_PANEL_SERVER_NAME='我的 Minecraft 服务器' \
  MC_PANEL_SERVER_ROOT=/srv/minecraft/server \
  MC_PANEL_SERVER_STARTUP_PATH=/srv/minecraft/server/start.sh \
  MC_PANEL_SERVER_SERVICE=minecraft.service \
  MC_PANEL_SERVER_PORT=25565 \
  MC_PANEL_SERVER_TIMEZONE=Asia/Shanghai \
  MC_PANEL_BACKUP_ROOT=/srv/minecraft/backups \
  MC_PANEL_BACKUP_ARCHIVE_GLOB='minecraft-*.tar.gz' \
  MC_PANEL_BACKUP_COMMAND=/usr/local/bin/minecraft-backup \
  MC_PANEL_BACKUP_SERVICE=minecraft-backup.service \
  MC_PANEL_BACKUP_TIMER=minecraft-backup.timer \
  MC_PANEL_CONSOLE_USER=minecraft \
  MC_PANEL_CONSOLE_SCREEN=minecraft \
  MC_PANEL_CONSOLE_COMMANDS_ENABLED=false \
  ./deploy/scripts/install-readonly.sh /tmp/mc-panel-release/0.4.2 0.4.2
```

安装会创建：

- `mc-panel:mc-panel` nologin 系统用户和组。
- `/opt/mc-panel/releases/<version>` 与原子 `current` 链接。
- `/var/lib/mc-panel/panel.db`。
- `/etc/mc-panel/panel.env`，`root:mc-panel`、0640。
- `/etc/mc-panel/helper.json`，`root:root`、0600。
- `/usr/local/libexec/mc-panel-action`，`root:root`、0755。
- `mc-panel.service` 和可回滚的最小只读 ACL。

此阶段不安装 sudoers，不给 Minecraft 路径写入能力。

## 创建管理员与自检

```bash
sudo /opt/mc-panel/current/deploy/scripts/create-admin.sh admin
sudo bash -c 'set -a; source /etc/mc-panel/panel.env; set +a; \
  /opt/mc-panel/current/.venv/bin/mc-panel-admin doctor'
sudo /opt/mc-panel/current/deploy/scripts/verify.sh
```

CLI 不提供默认密码。`doctor` 不显示密钥，会验证运行目录、服务、备份、启动文件和版本识别来源。

## 启用受控写入

只有在最近一致性备份已经完成并校验、fingerprint 已人工核对后执行：

```bash
fingerprint=$(sudo /opt/mc-panel/current/deploy/scripts/fingerprint.sh)
sudo env MC_PANEL_APPROVE_WRITES=YES \
  /opt/mc-panel/current/deploy/scripts/enable-writes.sh "$fingerprint"
```

脚本会从 root 所有的 `helper.json` 生成精确 `ReadWritePaths` drop-in，安装只允许无参数 helper 的 sudoers，并重启面板。Minecraft 不会因此重启。

通用控制台不是启用受控写入的必需能力。确有需要时，由 root 在 `/etc/mc-panel/panel.env` 中显式设置 `MC_PANEL_CONSOLE_COMMANDS_ENABLED=true`，检查文件仍为 `root:mc-panel`、0640 后只重启 `mc-panel.service`。该开关不会把控制台工具暴露给 AI。

紧急关闭写入：

```bash
sudo /opt/mc-panel/current/deploy/scripts/disable-writes.sh
```

## HTTPS

API 默认只监听 `127.0.0.1:18080`。`deploy/caddy/` 提供使用 `MC_PANEL_HOST` 和 `MC_PANEL_PORT` 的模板，必须先复制到独立测试路径并运行 `caddy validate`。不要让安装器自动开放公网端口。

公网部署必须设置：

```dotenv
MC_PANEL_SESSION_SECURE=true
MC_PANEL_PUBLIC_ORIGIN=https://panel.example.com
```

## 更新与回滚

```bash
sudo env MC_PANEL_APPROVE_UPDATE=YES \
  /opt/mc-panel/current/deploy/scripts/update.sh /tmp/mc-panel-release/0.4.2 0.4.2

sudo env MC_PANEL_APPROVE_ROLLBACK=YES \
  /opt/mc-panel/current/deploy/scripts/rollback.sh 0.4.2
```

更新前使用 SQLite backup API 创建一致副本和 SHA256，保存旧 helper、systemd unit、运行路径 drop-in 与 release。健康检查失败会自动恢复旧 release 和数据库。更新只重启面板。

卸载会保留数据库和配置到 root 专用归档：

```bash
sudo env MC_PANEL_APPROVE_UNINSTALL=YES \
  /opt/mc-panel/current/deploy/scripts/uninstall-preserve-data.sh
```
