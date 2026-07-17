export type SessionPhase =
  | "working"
  | "waiting_completion"
  | "cooling"
  | "generating"
  | "idle"
  | "superseded";

export interface SessionView {
  sessionId: string;
  threadId: string | null;
  title: string;
  cwd: string | null;
  phase: SessionPhase;
  lastActivityAt: number | null;
  completedAt: number | null;
  dueAt: number | null;
  remainingSeconds: number | null;
  lastUserMessage: string | null;
  summary: string | null;
  summarizedTurnId: string | null;
  lastError: string | null;
}

export interface DashboardSnapshot {
  codexHome: string;
  pluginData: string;
  generatedAt: number;
  sessions: SessionView[];
}
