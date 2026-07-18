export type Role = "owner" | "viewer";
export type ViewId = "home" | "players" | "server" | "mods" | "backups" | "settings" | "audit";

export interface ApiEnvelope<T> {
  ok: boolean;
  data: T;
  error?: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
  request_id: string;
}

export interface User {
  username: string;
  role: Role;
  csrf_token: string;
}

export interface ServerStatus {
  state: string;
  healthy: boolean;
  friendly_status: string;
  service: string;
  minecraft_version: string;
  loader: string;
  loader_version: string;
  java_version: string;
  identity_sources?: Record<string, string>;
  uptime_seconds: number;
  fingerprint: string;
  adapter: string;
}

export interface Metrics {
  cpu_percent: number;
  memory_used_bytes: number;
  memory_total_bytes: number;
  disk_used_bytes: number;
  disk_total_bytes: number;
  tps: number | null;
  summary: {
    cpu: string;
    memory: string;
    disk: string;
    performance: string;
  };
}

export interface Players {
  online: number;
  maximum: number;
  players: string[];
  stale?: boolean;
  source?: string;
}

export interface AiStatus {
  enabled: boolean;
  configured: boolean;
  model: string | null;
  requests_per_minute: number;
  requests_per_day: number;
  tokens_per_day: number;
}

export type DiagnosticTone = "good" | "neutral" | "warning" | "critical";

export interface DiagnosticCheck {
  id: string;
  tone: DiagnosticTone;
  title: string;
  detail: string;
  target: ViewId;
}

export interface Diagnostics {
  level: "good" | "attention" | "critical";
  headline: string;
  updated_at: string;
  checks: DiagnosticCheck[];
  signals: Array<{
    id: string;
    tone: DiagnosticTone;
    title: string;
    target: ViewId;
  }>;
  backup: {
    latest: BackupInfo | null;
    age_hours: number | null;
    schedule: { hour: number; minute: number; keep: number; kind?: string };
    count: number;
    status?: BackupRuntimeStatus | null;
  };
  local_time: string;
}

export interface DashboardData {
  server: ServerStatus;
  server_name: string;
  connection_address: string | null;
  metrics: Metrics;
  players: Players;
  ai: AiStatus;
  diagnostics: Diagnostics;
  quick_actions: string[];
}

export interface PlayerActivityDay {
  date: string;
  sessions: number;
  unique_players: number;
  players: string[];
}

export interface PlayerActivity {
  days: number;
  requested_start: string;
  generated_at: string;
  coverage_start: string | null;
  coverage_end: string | null;
  coverage_complete: boolean;
  truncated: boolean;
  sessions: number;
  unique_players: number;
  daily: PlayerActivityDay[];
  recent_players: Array<{
    name: string;
    sessions: number;
    last_joined_at: string;
  }>;
  source: string;
  history: {
    persisted: boolean;
    last_imported_at: string | null;
    collector_healthy: boolean;
  };
}

export interface AiUsage {
  day: string;
  request_count: number;
  input_tokens: number;
  output_tokens: number;
}

export interface OperationPreview {
  action: string;
  params: Record<string, unknown>;
  title: string;
  reason: string;
  impact: string;
  risk: "low" | "medium" | "high";
  requires_confirmation: boolean;
  requires_second_confirmation: boolean;
  stops_server: boolean;
  creates_recovery_point: boolean;
  rollback: string;
  status?: string;
  confirmation_id?: string;
}

export interface OperationResult {
  status: string;
  operation_id?: string;
  result?: Record<string, unknown>;
  confirmation_id?: string;
  prompt?: string;
}

export type OperationRunner = (
  action: string,
  params?: Record<string, unknown>,
) => Promise<boolean>;

export interface ModInfo {
  id: string;
  name: string;
  version: string;
  loader: string;
  filename: string;
  enabled: boolean;
  duplicate: boolean;
  size_bytes?: number;
}

export interface BackupInfo {
  id: string;
  created_at: string | number;
  size_bytes: number;
  verified: boolean;
  verification_status?: "verified" | "checksum_present" | "invalid" | "missing";
  kind: string;
  filename?: string;
}

export interface BackupRuntimeStatus {
  timer_active: boolean | null;
  timer_enabled: boolean | null;
  last_result: "success" | "failed" | "running" | "never" | "unknown";
  last_run_at: string | null;
  last_finished_at: string | null;
  last_exit_code: number | null;
  next_run_at: string | null;
}

export interface AuditEvent {
  id: string;
  created_at: string;
  action: string;
  risk: string;
  outcome: string;
  request_id: string;
  confirmation_id: string | null;
  params_summary: Record<string, unknown>;
  result_summary: Record<string, unknown>;
}

export interface AiReply {
  answer: string;
  evidence: Array<{ tool: string; result: unknown }>;
  proposed_actions: OperationPreview[];
  limits: Record<string, unknown>;
}
