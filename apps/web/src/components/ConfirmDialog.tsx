import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  DatabaseBackup,
  ListChecks,
  ServerOff,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import type { OperationPreview } from "../types";

interface ConfirmDialogProps {
  operation: OperationPreview;
  secondStep: boolean;
  serverName: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (serverName?: string) => Promise<void>;
}

export function ConfirmDialog({
  operation,
  secondStep,
  serverName,
  busy,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const [typedName, setTypedName] = useState("");
  const high = operation.risk === "high";
  const dialogRef = useRef<HTMLElement>(null);
  const busyRef = useRef(busy);
  const cancelRef = useRef(onCancel);

  useEffect(() => {
    busyRef.current = busy;
    cancelRef.current = onCancel;
  }, [busy, onCancel]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busyRef.current) cancelRef.current();
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onCancel();
    }}>
      <section ref={dialogRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title" tabIndex={-1}>
        <header className="modal-header">
          <span className={`risk-icon ${operation.risk}`}>
            {high ? <ShieldAlert size={22} /> : <AlertTriangle size={22} />}
          </span>
          <div>
            <p className="eyebrow">{high ? "高风险操作" : "需要确认"}</p>
            <h2 id="confirm-title">{operation.title}</h2>
          </div>
          <button className="icon-button" onClick={onCancel} disabled={busy} aria-label="关闭" title="关闭">
            <X size={20} />
          </button>
        </header>

        <div className="impact-list">
          <div><AlertTriangle size={18} /><span><strong>为什么：</strong>{operation.reason}</span></div>
          <div><ServerOff size={18} /><span><strong>影响：</strong>{operation.impact}</span></div>
          <div><DatabaseBackup size={18} /><span><strong>恢复：</strong>{operation.rollback}</span></div>
        </div>

        <section className="operation-review" aria-label="本次操作的具体内容">
          <h3><ListChecks size={18} />请确认具体目标</h3>
          <dl>
            {operation.review.review_items.map((item) => (
              <div key={`${item.label}-${item.value}`}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
          <p><ShieldCheck size={17} /><span><strong>系统将如何确认：</strong>{operation.review.assurance_expected}</span></p>
        </section>

        {secondStep && (
          <label className="danger-confirmation">
            为防止误操作，请输入服务器名称 <strong>{serverName}</strong>
            <input value={typedName} onChange={(event) => setTypedName(event.target.value)} autoFocus />
          </label>
        )}

        <footer className="modal-actions">
          <button className="button secondary" onClick={onCancel} disabled={busy}>取消</button>
          <button
            className={high ? "button danger" : "button primary"}
            onClick={() => onConfirm(secondStep ? typedName : undefined)}
            disabled={busy || (secondStep && typedName !== serverName)}
          >
            {busy ? "正在处理…" : secondStep ? "确认恢复" : "确认执行"}
          </button>
        </footer>
      </section>
    </div>
  );
}
