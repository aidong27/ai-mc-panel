import { useEffect, useState, type FormEvent } from "react";
import {
  Archive,
  CheckCircle2,
  CircleAlert,
  Clock,
  DatabaseBackup,
  HardDrive,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { api } from "../api";
import { formatTimestamp } from "../format";
import type { BackupInfo, BackupRuntimeStatus, OperationRunner } from "../types";

interface BackupsViewProps {
  refreshKey: number;
  operationBusy: boolean;
  onOperation: OperationRunner;
}

function size(value: number): string {
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function verification(backup: BackupInfo): { label: string; className: string; title: string } {
  if (backup.verified || backup.verification_status === "verified") {
    return { label: "已完整校验", className: "small-badge success", title: "备份内容已读取并通过校验" };
  }
  if (backup.verification_status === "checksum_present") {
    return { label: "校验文件就绪", className: "small-badge neutral", title: "恢复前会再计算完整哈希并检查压缩包" };
  }
  return { label: "缺少校验文件", className: "small-badge warning", title: "不建议使用该备份恢复" };
}

function nextBackupText(hour: number, minute: number): string {
  return `每天 ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")} 服务器时间`;
}

function runResult(status: BackupRuntimeStatus | null): string {
  if (!status) return "正在读取…";
  return {
    success: "成功",
    failed: "失败",
    running: "正在进行",
    never: "尚未执行",
    unknown: "暂时无法确认",
  }[status.last_result];
}

function canRestore(backup: BackupInfo): boolean {
  return backup.verified
    || backup.verification_status === "verified"
    || backup.verification_status === "checksum_present";
}

export function BackupsView({ refreshKey, operationBusy, onOperation }: BackupsViewProps) {
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [schedule, setSchedule] = useState({ hour: 3, minute: 30, keep: 7 });
  const [runtime, setRuntime] = useState<BackupRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setLoadError("");
    void Promise.all([
      api.get<BackupInfo[]>("/backups"),
      api.get<{ hour: number; minute: number; keep: number }>("/backup-schedule"),
      api.get<BackupRuntimeStatus>("/backup-status"),
    ]).then(([items, nextSchedule, nextRuntime]) => {
      if (disposed) return;
      setBackups(items);
      setSchedule(nextSchedule);
      setRuntime(nextRuntime);
    }, (error: unknown) => {
      if (!disposed) setLoadError(error instanceof Error ? error.message : "备份信息读取失败");
    }).finally(() => {
      if (!disposed) setLoading(false);
    });
    return () => { disposed = true; };
  }, [refreshKey, reloadKey]);

  async function updateSchedule(event: FormEvent) {
    event.preventDefault();
    await onOperation("set_backup_schedule", schedule);
  }

  const latest = backups[0];

  return (
    <div className="view">
      <header className="page-heading">
        <div><p className="eyebrow">世界存档点</p><h1>备份与恢复</h1></div>
        <button className="button primary" disabled={operationBusy} onClick={() => void onOperation("create_backup")}><DatabaseBackup size={18} />{operationBusy ? "正在处理…" : "创建备份"}</button>
      </header>

      {loadError && (
        <div className="load-error" role="alert"><span>{loadError}</span><button className="button secondary compact-button" onClick={() => setReloadKey((value) => value + 1)}><RefreshCw size={15} />重试</button></div>
      )}

      <section className="backup-summary-band">
        <div><span className="summary-icon"><ShieldCheck size={21} /></span><span><small>最近保护</small><strong>{latest ? formatTimestamp(latest.created_at) : loading ? "正在读取…" : "还没有备份"}</strong></span></div>
        <div><span className="summary-icon"><Archive size={21} /></span><span><small>上次自动备份</small><strong>{runResult(runtime)}{runtime?.last_finished_at ? ` · ${formatTimestamp(runtime.last_finished_at)}` : ""}</strong></span></div>
        <div><span className="summary-icon"><Clock size={21} /></span><span><small>下次自动备份</small><strong>{runtime?.next_run_at ? formatTimestamp(runtime.next_run_at) : nextBackupText(schedule.hour, schedule.minute)}</strong></span></div>
      </section>

      {runtime?.last_result === "failed" && (
        <p className="notice critical"><CircleAlert size={17} />上次自动备份失败{runtime.last_exit_code !== null ? `（退出码 ${runtime.last_exit_code}）` : ""}，现有备份不会被删除。</p>
      )}
      {(runtime?.timer_active === false || runtime?.timer_enabled === false) && (
        <p className="notice warning"><Clock size={17} />自动备份计划当前未运行，下一次不会自动执行。</p>
      )}
      <p className="notice neutral"><Clock size={17} />当前采用冷备份：短暂停服保存一致存档，通常 1 到 2 分钟，完成后自动恢复。</p>

      <div className="backups-layout">
        <section className="panel-section backup-list-section">
          <div className="section-title-row compact"><div><p className="eyebrow">最近存档点</p><h2>备份列表</h2></div><span className="small-badge neutral">{backups.length} / {schedule.keep} 份</span></div>
          {loading && !backups.length ? <p className="empty-copy">正在读取备份目录…</p> : (
            <div className="backup-list">
              {backups.map((backup) => {
                const state = verification(backup);
                return (
                  <div key={backup.id}>
                    <span className="backup-status">{backup.verified ? <CheckCircle2 size={19} /> : <Archive size={19} />}</span>
                    <span><strong>{formatTimestamp(backup.created_at)}</strong><small>{size(backup.size_bytes)} · {backup.kind === "cold" ? "一致性冷备" : backup.kind}</small></span>
                    <span className={state.className} title={state.title}>{state.label}</span>
                    <button className="button secondary compact-button" disabled={operationBusy || !canRestore(backup)} onClick={() => void onOperation("restore_backup", { backup_id: backup.id })}><RotateCcw size={16} />恢复</button>
                  </div>
                );
              })}
              {!backups.length && !loading && <p className="empty-copy"><HardDrive size={17} />还没有找到可用备份。修改模组或配置前，建议先创建一份。</p>}
            </div>
          )}
        </section>

        <section className="panel-section schedule-section">
          <div className="section-title-row compact"><div><p className="eyebrow">自动保护</p><h2>每日备份</h2></div><Clock size={22} /></div>
          <p className="help-copy">建议选择朋友通常不在线的时间。修改后不会立即执行备份。</p>
          <form className="form-stack" onSubmit={updateSchedule}>
            <div className="form-row">
              <label>小时<input type="number" min={0} max={23} value={schedule.hour} onChange={(event) => setSchedule({ ...schedule, hour: Number(event.target.value) })} /></label>
              <label>分钟<input type="number" min={0} max={59} value={schedule.minute} onChange={(event) => setSchedule({ ...schedule, minute: Number(event.target.value) })} /></label>
            </div>
            <label>保留份数<input type="number" min={3} max={30} value={schedule.keep} onChange={(event) => setSchedule({ ...schedule, keep: Number(event.target.value) })} /></label>
            <button className="button secondary" type="submit" disabled={operationBusy}>保存备份计划</button>
          </form>
        </section>
      </div>
    </div>
  );
}
