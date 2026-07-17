import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ReactMarkdown from "react-markdown";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import type { DashboardSnapshot, SessionPhase, SessionView } from "./types";

const PHASE_LABELS: Record<SessionPhase, string> = {
  working: "Working",
  waiting_completion: "Finishing turn",
  cooling: "Cooling",
  generating: "Generating recap",
  idle: "Recap ready",
  superseded: "Superseded",
};

const relativeTime = (epoch: number | null) => {
  if (!epoch) return "No activity";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(epoch * 1000).toLocaleDateString();
};

const countdown = (seconds: number | null) => {
  if (seconds === null) return "";
  const safe = Math.max(0, seconds);
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
};

const remainingFor = (session: SessionView) => {
  if (!session.dueAt) return session.remainingSeconds;
  return Math.max(0, Math.ceil(session.dueAt - Date.now() / 1000));
};

const summaryKey = (session: SessionView) =>
  `${session.sessionId}:${session.summarizedTurnId ?? "none"}`;

const summaryPreview = (summary: string) =>
  summary
    .replace(/\[thread-recap:summary:[^\]]+\]/g, "")
    .replace(/[#>*_`~-]/g, "")
    .replace(/\s+/g, " ")
    .trim();

function StatusPill({ session }: { session: SessionView }) {
  const remaining = remainingFor(session);
  return (
    <span className={`status status--${session.phase}`}>
      <span className="status__dot" />
      {PHASE_LABELS[session.phase] ?? session.phase}
      {session.phase === "cooling" && remaining !== null
        ? ` · ${countdown(remaining)}`
        : ""}
    </span>
  );
}

function SessionCard({
  session,
  expanded,
  onToggle,
}: {
  session: SessionView;
  expanded: boolean;
  onToggle: () => void;
}) {
  const remaining = remainingFor(session);
  const openInCodex = async () => {
    await invoke("open_in_codex", {
      sessionId: session.sessionId,
      cwd: session.cwd,
    });
  };
  const copySummary = async () => {
    if (session.summary) await navigator.clipboard.writeText(session.summary);
  };

  return (
    <article className={`session-tile ${expanded ? "session-tile--expanded" : ""}`}>
      <div className="session-tile__topline">
        <StatusPill session={session} />
        <span className="session-tile__time">{relativeTime(session.lastActivityAt)}</span>
      </div>

      <h2 title={session.title}>{session.title}</h2>
      <p className="session-tile__message">
        {session.lastUserMessage ?? "Waiting for the first prompt"}
      </p>

      {session.phase === "cooling" && remaining !== null && (
        <div className="cooldown-inline">
          <div className="cooldown-inline__label">
            <span>Recap in</span>
            <strong>{countdown(remaining)}</strong>
          </div>
          <div className="progress-track">
            <div
              style={{
                width: `${100 - Math.min(100, (remaining / 300) * 100)}%`,
              }}
            />
          </div>
        </div>
      )}

      <div className={`tile-recap ${session.summary ? "tile-recap--ready" : ""}`}>
        <div className="tile-recap__label">
          <span>{session.summary ? "Latest recap" : "Recap"}</span>
          {session.summarizedTurnId && (
            <code>{session.summarizedTurnId.slice(0, 8)}</code>
          )}
        </div>
        {session.summary ? (
          expanded ? (
            <div className="recap-content">
              <ReactMarkdown>{session.summary}</ReactMarkdown>
            </div>
          ) : (
            <p className="tile-recap__preview">{summaryPreview(session.summary)}</p>
          )
        ) : (
          <p className="tile-recap__empty">
            A recap will appear after five minutes of inactivity.
          </p>
        )}
      </div>

      {session.lastError && (
        <div className="error-card"><strong>Last error</strong>{session.lastError}</div>
      )}

      <div className="session-tile__footer">
        <div className="session-tile__identity">
          <span title={session.cwd ?? undefined}>{session.cwd ?? "No workspace"}</span>
          <code>{session.sessionId.slice(0, 8)}</code>
        </div>
        <div className="card-actions">
          {session.summary && (
            <>
              <button onClick={() => void copySummary()} aria-label="Copy recap">Copy</button>
              <button onClick={onToggle}>{expanded ? "Collapse" : "Read recap"}</button>
            </>
          )}
          <button className="primary" onClick={() => void openInCodex()}>Open</button>
        </div>
      </div>
    </article>
  );
}

export default function App() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const next = await invoke<DashboardSnapshot>("dashboard_snapshot");
      setSnapshot(next);
      setError(null);

      const seen = new Set<string>(JSON.parse(localStorage.getItem("recaps-seen") ?? "[]"));
      let changed = false;
      for (const session of next.sessions) {
        if (!session.summary || !session.summarizedTurnId) continue;
        const key = summaryKey(session);
        if (seen.has(key)) continue;
        seen.add(key);
        changed = true;
        if (Date.now() / 1000 - (session.dueAt ?? 0) < 900) {
          let granted = await isPermissionGranted();
          if (!granted) granted = (await requestPermission()) === "granted";
          if (granted) {
            sendNotification({
              title: `ThreadRecap · ${session.title}`,
              body: summaryPreview(session.summary).slice(0, 180),
            });
          }
        }
      }
      if (changed) localStorage.setItem("recaps-seen", JSON.stringify([...seen].slice(-200)));
    } catch (reason) {
      setError(String(reason));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const refreshTimer = window.setInterval(() => void refresh(), 2000);
    const clockTimer = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => {
      window.clearInterval(refreshTimer);
      window.clearInterval(clockTimer);
    };
  }, [refresh]);

  const sessions = snapshot?.sessions ?? [];
  const metrics = useMemo(
    () => ({
      total: sessions.length,
      active: sessions.filter((session) =>
        ["working", "waiting_completion", "generating"].includes(session.phase),
      ).length,
      cooling: sessions.filter((session) => session.phase === "cooling").length,
      ready: sessions.filter((session) => Boolean(session.summary)).length,
    }),
    [sessions],
  );

  const toggleExpanded = (sessionId: string) => {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  };

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">CODEX SESSION COMPANION</div>
          <h1>ThreadRecap</h1>
        </div>
        <div className="topbar__right">
          <span className="live"><span />Live</span>
          <button className="icon-button" onClick={() => void refresh()} aria-label="Refresh">↻</button>
        </div>
      </header>

      <section className="metrics">
        <article><span>Tracked</span><strong>{metrics.total}</strong></article>
        <article><span>Active</span><strong className="accent-blue">{metrics.active}</strong></article>
        <article><span>Cooling</span><strong className="accent-amber">{metrics.cooling}</strong></article>
        <article><span>Recaps</span><strong className="accent-green">{metrics.ready}</strong></article>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <section className="sessions-area">
        <div className="section-heading">
          <div><span>Sessions</span><small>Each session is shown as an independent card</small></div>
          <strong>{sessions.length}</strong>
        </div>

        {sessions.length ? (
          <div className="session-grid">
            {sessions.map((session) => (
              <SessionCard
                key={session.sessionId}
                session={session}
                expanded={expandedIds.has(session.sessionId)}
                onToggle={() => toggleExpanded(session.sessionId)}
              />
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <div className="orb" />
            <h3>No ThreadRecap sessions yet</h3>
            <p>Start a Codex task to begin tracking.</p>
          </div>
        )}
      </section>

      <footer>
        <span>Data: {snapshot?.pluginData ?? "Discovering…"}</span>
        <span>Updated {snapshot ? new Date(snapshot.generatedAt * 1000).toLocaleTimeString() : "—"}</span>
      </footer>
    </main>
  );
}
