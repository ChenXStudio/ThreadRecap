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

const summaryKey = (session: SessionView) =>
  `${session.sessionId}:${session.summarizedTurnId ?? "none"}`;

function StatusPill({ session }: { session: SessionView }) {
  return (
    <span className={`status status--${session.phase}`}>
      <span className="status__dot" />
      {PHASE_LABELS[session.phase] ?? session.phase}
      {session.phase === "cooling" && session.remainingSeconds !== null
        ? ` · ${countdown(session.remainingSeconds)}`
        : ""}
    </span>
  );
}

export default function App() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const next = await invoke<DashboardSnapshot>("dashboard_snapshot");
      setSnapshot(next);
      setError(null);
      setSelectedId((current) => current ?? next.sessions[0]?.sessionId ?? null);

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
              body: session.summary.replace(/[#*`]/g, "").slice(0, 180),
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
  const selected = sessions.find((session) => session.sessionId === selectedId) ?? sessions[0] ?? null;
  const metrics = useMemo(
    () => ({
      total: sessions.length,
      active: sessions.filter((session) => ["working", "waiting_completion", "generating"].includes(session.phase)).length,
      cooling: sessions.filter((session) => session.phase === "cooling").length,
      ready: sessions.filter((session) => Boolean(session.summary)).length,
    }),
    [sessions, tick],
  );

  const copySummary = async () => {
    if (selected?.summary) await navigator.clipboard.writeText(selected.summary);
  };

  const openInCodex = async () => {
    if (!selected) return;
    await invoke("open_in_codex", { sessionId: selected.sessionId, cwd: selected.cwd });
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

      <section className="workspace">
        <aside className="session-panel">
          <div className="panel-heading">
            <span>Sessions</span>
            <small>{sessions.length}</small>
          </div>
          <div className="session-list">
            {sessions.map((session) => (
              <button
                key={session.sessionId}
                className={`session-card ${selected?.sessionId === session.sessionId ? "session-card--selected" : ""}`}
                onClick={() => setSelectedId(session.sessionId)}
              >
                <div className="session-card__title">{session.title}</div>
                <StatusPill session={session} />
                <p>{session.lastUserMessage ?? "Waiting for the first prompt"}</p>
                <div className="session-card__meta">
                  <span>{relativeTime(session.lastActivityAt)}</span>
                  <code>{session.sessionId.slice(0, 8)}</code>
                </div>
              </button>
            ))}
            {!sessions.length && (
              <div className="empty-state">No ThreadRecap sessions yet.<br />Start a Codex task to begin tracking.</div>
            )}
          </div>
        </aside>

        <section className="detail-panel">
          {selected ? (
            <>
              <div className="detail-header">
                <div>
                  <StatusPill session={selected} />
                  <h2>{selected.title}</h2>
                  <p>{selected.cwd ?? "Workspace unavailable"}</p>
                </div>
                <div className="actions">
                  <button onClick={() => void copySummary()} disabled={!selected.summary}>Copy recap</button>
                  <button className="primary" onClick={() => void openInCodex()}>Open in Codex</button>
                </div>
              </div>

              {selected.phase === "cooling" && (
                <div className="cooldown-card">
                  <div>
                    <span>Recap scheduled</span>
                    <strong>{countdown(selected.remainingSeconds)}</strong>
                  </div>
                  <div className="progress-track"><div style={{ width: `${100 - Math.min(100, ((selected.remainingSeconds ?? 0) / 300) * 100)}%` }} /></div>
                </div>
              )}

              <article className="recap-card">
                <div className="recap-card__heading">
                  <span>Latest recap</span>
                  {selected.summarizedTurnId && <code>{selected.summarizedTurnId.slice(0, 12)}</code>}
                </div>
                {selected.summary ? (
                  <div className="recap-content"><ReactMarkdown>{selected.summary}</ReactMarkdown></div>
                ) : (
                  <div className="recap-placeholder">
                    <div className="orb" />
                    <h3>No recap yet</h3>
                    <p>The recap appears here automatically after the task stays idle for five minutes.</p>
                  </div>
                )}
              </article>

              {selected.lastError && <div className="error-card"><strong>Last error</strong>{selected.lastError}</div>}
            </>
          ) : (
            <div className="recap-placeholder full"><div className="orb" /><h3>Select a session</h3></div>
          )}
        </section>
      </section>

      <footer>
        <span>Data: {snapshot?.pluginData ?? "Discovering…"}</span>
        <span>Updated {snapshot ? new Date(snapshot.generatedAt * 1000).toLocaleTimeString() : "—"}</span>
      </footer>
    </main>
  );
}
