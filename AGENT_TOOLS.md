# Agent Tools

AI 只看到这里定义的工具，不拥有 Shell、文件系统、sudo、数据库或网络下载能力。后端拥有最终决定权。

## 工具清单

| 工具 | 风险 | 默认确认 | 说明 |
|---|---:|---:|---|
| `get_server_status` | 低 | 否 | 服务、版本、启动阶段和 fingerprint |
| `get_system_metrics` | 低 | 否 | CPU、内存、磁盘和通俗结论 |
| `read_recent_logs` | 低 | 否 | 固定为最近日志，限制行数和总字符数 |
| `read_crash_report` | 低 | 否 | 可接受后端 report ID；不传时读取最新一份 |
| `list_players` | 低 | 否 | 当前在线玩家 |
| `get_player_activity` | 低 | 否 | 1 到 30 天的持久化登录活跃摘要和覆盖状态，不包含 IP |
| `list_mods` | 低 | 否 | 压缩后的文件名、元数据和状态摘要 |
| `get_server_properties` | 低 | 否 | 只返回允许展示的键 |
| `list_backups` | 低 | 否 | 时间、大小、校验状态、上次结果和下次计划 |
| `check_disk_space` | 低 | 否 | 当前使用量、剩余空间和通俗结论 |
| `read_recent_operations` | 低 | 否 | 最近 1 到 20 条受控操作摘要 |
| `send_announcement` | 低 | 否 | 纯文本游戏公告 |
| `create_backup` | 低 | 否 | 当前服会短暂停机，执行前必须展示影响 |
| `start_server` | 中 | 是 | 验证服务当前为 stopped |
| `stop_server` | 中 | 是 | 展示在线玩家和停服影响 |
| `restart_server` | 中 | 是 | 保存世界、广播、重启并验证 |
| `edit_server_property` | 中 | 是 | 仅 schema 白名单键 |
| `add_whitelist_player` | 中 | 是 | 校验玩家名并执行 whitelist add |
| `remove_whitelist_player` | 中 | 是 | 校验玩家名并执行 whitelist remove |
| `add_operator` | 中 | 是 | 显示离线模式身份风险 |
| `remove_operator` | 中 | 是 | 校验玩家名并执行 deop |
| `install_mod` | 中 | 是 | 仅已上传、已扫描的隔离区对象 |
| `disable_mod` | 中 | 是 | 移到保留目录，不删除 |
| `set_backup_schedule` | 中 | 是 | 只允许安全时间表达和保留范围 |
| `check_mod_compatibility` | 低 | 否 | 元数据与日志分析，只给建议 |
| `restore_backup` | 高 | 二次 | 自动当前备份、staging 和失败回滚 |

MVP 不注册 `delete_world`、`replace_server_core`、`upgrade_minecraft`、`change_java_major`、`change_firewall`、`change_user_permissions` 或任意下载工具。

## 参数规则示例

`read_recent_logs`：

```json
{
  "lines": 120,
  "severity": ["WARN", "ERROR"]
}
```

- Agent 工具固定读取 `latest.log`，不会接受任意路径或来源名称。
- `lines` 为 1 到 300。
- 单行发送模型不超过 1,200 字符，单次总量不超过 48,000 字符；兼容性分析中的日志上限更低。

`edit_server_property`：

```json
{
  "key": "view-distance",
  "value": 8
}
```

首版可写键仅包括 `difficulty`、`gamemode`、`max-players`、`pvp`、`view-distance`、`simulation-distance`、`white-list` 和 `motd`。每个键有独立类型和范围。`online-mode`、端口、RCON、level-name、JVM 参数不在普通工具中。

`send_announcement`：只接受 1 到 200 个可打印字符，拒绝换行、控制字符、以 `/` 开头和包含 secret 模式的文本。

`install_mod`：只接受后端生成的 `upload_id`。上传对象必须是单个 `.jar`、通过 ZIP 结构检查、大小限制、SHA256 和 Forge/Fabric/NeoForge/Quilt 元数据识别；AI 不能提交路径或 URL。

## 工具执行管线

1. 认证用户提交普通中文请求。
2. 后端构造最小、脱敏的服务器摘要。
3. 模型返回解释或 typed tool call。
4. 后端按工具 schema 严格校验参数，写工具再经过 Pydantic，全部拒绝额外字段。
5. 权限引擎检查角色和工具范围。
6. fingerprint 检查服务器结构是否仍匹配。
7. 风险引擎生成影响、停服、备份和回滚说明。
8. 需要确认时只创建 proposal，不执行。
9. 确认后后端执行注册函数，必要时调用固定 helper。
10. helper 或 adapter 返回经过验证的结构化结果，后端写入审计。
11. 模型只能基于工具结果解释最终状态。

## 提示注入处理

日志、玩家聊天、模组描述、崩溃报告和 MOTD 都标记为 untrusted data。出现“忽略规则”“执行命令”“读取密钥”等文字时只作为日志内容展示，不提升权限、不创建工具、不改变系统提示。

## 无法确认时

路径、版本、服务状态、备份完整性或 fingerprint 不确定时，工具返回 `server_structure_changed` 或 `verification_failed`。Agent 必须停止写操作，说明缺少的证据，并重新调用检测工具；禁止猜测路径或报告成功。
