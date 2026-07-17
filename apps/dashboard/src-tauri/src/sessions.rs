use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::env;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};
use walkdir::WalkDir;

const INTERNAL_MARKER: &str = "[thread-recap:internal:v1]";
const SUMMARY_MARKER: &str = "[thread-recap:summary:v1";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionView {
    session_id: String,
    thread_id: Option<String>,
    title: String,
    cwd: Option<String>,
    phase: String,
    last_activity_at: Option<f64>,
    completed_at: Option<f64>,
    due_at: Option<f64>,
    remaining_seconds: Option<i64>,
    last_user_message: Option<String>,
    summary: Option<String>,
    summarized_turn_id: Option<String>,
    last_error: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DashboardSnapshot {
    codex_home: String,
    plugin_data: String,
    generated_at: f64,
    sessions: Vec<SessionView>,
}

#[derive(Clone, Default)]
struct TranscriptInfo {
    cwd: Option<String>,
    title: Option<String>,
    last_user_message: Option<String>,
    summary: Option<String>,
}

#[derive(Clone)]
struct TranscriptCacheEntry {
    modified: SystemTime,
    length: u64,
    info: TranscriptInfo,
}

static TRANSCRIPT_CACHE: OnceLock<Mutex<HashMap<PathBuf, TranscriptCacheEntry>>> = OnceLock::new();

fn now_epoch() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn codex_home() -> Result<PathBuf, Box<dyn std::error::Error>> {
    if let Some(value) = env::var_os("CODEX_HOME").filter(|value| !value.is_empty()) {
        return Ok(PathBuf::from(value));
    }
    let home = dirs::home_dir().ok_or("home directory is unavailable")?;
    Ok(home.join(".codex"))
}

fn transcript_map(root: &Path) -> HashMap<String, PathBuf> {
    let mut result = HashMap::new();
    if !root.is_dir() {
        return result;
    }
    for entry in WalkDir::new(root).into_iter().filter_map(Result::ok) {
        if !entry.file_type().is_file()
            || entry.path().extension().and_then(|value| value.to_str()) != Some("jsonl")
        {
            continue;
        }
        let stem = entry
            .path()
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        if let Some(session_id) = session_id_from_stem(stem) {
            result.insert(session_id.to_owned(), entry.path().to_path_buf());
        }
    }
    result
}

fn session_id_from_stem(stem: &str) -> Option<&str> {
    if stem.len() < 36 {
        return None;
    }
    let session_id = &stem[stem.len() - 36..];
    (session_id.chars().filter(|value| *value == '-').count() == 4).then_some(session_id)
}

fn collapse(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn is_context_wrapper(value: &str) -> bool {
    let value = value.trim_start();
    value.starts_with("<recommended_plugins>")
        || value.starts_with("<environment_context>")
        || value.starts_with("# AGENTS.md instructions")
        || value.starts_with("<permissions instructions>")
        || value.starts_with("<skills_instructions>")
}

fn ordinary_user_text(payload: &Value) -> Option<String> {
    if payload.get("type").and_then(Value::as_str) == Some("user_message") {
        return payload
            .get("message")
            .and_then(Value::as_str)
            .map(str::to_owned);
    }
    if payload.get("role").and_then(Value::as_str) != Some("user") {
        return None;
    }
    payload
        .get("content")
        .and_then(Value::as_array)
        .and_then(|items| {
            items.iter().find_map(|item| {
                item.get("text")
                    .or_else(|| item.get("input_text"))
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            })
        })
}

fn assistant_text(payload: &Value) -> Option<String> {
    if payload.get("type").and_then(Value::as_str) == Some("agent_message") {
        return payload
            .get("message")
            .and_then(Value::as_str)
            .map(str::to_owned);
    }
    if payload.get("role").and_then(Value::as_str) != Some("assistant") {
        return None;
    }
    payload
        .get("content")
        .and_then(Value::as_array)
        .and_then(|items| {
            items
                .iter()
                .find_map(|item| item.get("text").and_then(Value::as_str).map(str::to_owned))
        })
}

fn parse_transcript(path: &Path) -> TranscriptInfo {
    let mut info = TranscriptInfo::default();
    let Ok(file) = File::open(path) else {
        return info;
    };
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        let Ok(record) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let payload = record.get("payload").unwrap_or(&record);
        if record.get("type").and_then(Value::as_str) == Some("session_meta") {
            info.cwd = payload
                .get("cwd")
                .and_then(Value::as_str)
                .map(str::to_owned);
        }
        if let Some(text) = ordinary_user_text(payload) {
            if text.contains(INTERNAL_MARKER) || is_context_wrapper(&text) {
                continue;
            }
            let compact = collapse(&text);
            if !compact.is_empty() {
                info.title
                    .get_or_insert_with(|| compact.chars().take(72).collect());
                info.last_user_message = Some(compact.chars().take(180).collect());
            }
        }
        if let Some(text) = assistant_text(payload) {
            if text.contains(SUMMARY_MARKER) {
                info.summary = Some(text);
            }
        }
    }
    info
}

fn parse_transcript_cached(path: &Path) -> TranscriptInfo {
    let Ok(metadata) = path.metadata() else {
        return TranscriptInfo::default();
    };
    let modified = metadata.modified().unwrap_or(UNIX_EPOCH);
    let cache = TRANSCRIPT_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(guard) = cache.lock() {
        if let Some(entry) = guard.get(path) {
            if entry.modified == modified && entry.length == metadata.len() {
                return entry.info.clone();
            }
        }
    }

    let info = parse_transcript(path);
    if let Ok(mut guard) = cache.lock() {
        guard.insert(
            path.to_path_buf(),
            TranscriptCacheEntry {
                modified,
                length: metadata.len(),
                info: info.clone(),
            },
        );
        if guard.len() > 200 {
            guard.retain(|cached_path, _| cached_path.exists());
        }
    }
    info
}

pub fn load_snapshot() -> Result<DashboardSnapshot, Box<dyn std::error::Error>> {
    let codex_home = codex_home()?;
    let plugin_data = codex_home
        .join("plugins")
        .join("data")
        .join("thread-recap-thread-recap");
    let database = plugin_data.join("state.db");
    let now = now_epoch();
    let transcripts = transcript_map(&codex_home.join("sessions"));
    let mut sessions = Vec::new();

    if database.is_file() {
        let connection = Connection::open_with_flags(database, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let mut statement = connection.prepare(
            "SELECT session_id, thread_id, phase, last_activity_at, completed_at, due_at, last_summarized_turn_id, last_error \
             FROM sessions ORDER BY COALESCE(last_activity_at, 0) DESC LIMIT 100",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, Option<f64>>(3)?,
                row.get::<_, Option<f64>>(4)?,
                row.get::<_, Option<f64>>(5)?,
                row.get::<_, Option<String>>(6)?,
                row.get::<_, Option<String>>(7)?,
            ))
        })?;

        for row in rows {
            let (
                session_id,
                thread_id,
                phase,
                last_activity_at,
                completed_at,
                due_at,
                summarized_turn_id,
                last_error,
            ) = row?;
            let transcript = transcripts
                .get(&session_id)
                .map(|path| parse_transcript_cached(path))
                .unwrap_or_default();
            let remaining_seconds = due_at.map(|due| (due - now).ceil().max(0.0) as i64);
            sessions.push(SessionView {
                title: transcript.title.unwrap_or_else(|| {
                    format!("Session {}", &session_id[..8.min(session_id.len())])
                }),
                cwd: transcript.cwd,
                last_user_message: transcript.last_user_message,
                summary: transcript.summary,
                session_id,
                thread_id,
                phase,
                last_activity_at,
                completed_at,
                due_at,
                remaining_seconds,
                summarized_turn_id,
                last_error,
            });
        }
    }

    Ok(DashboardSnapshot {
        codex_home: codex_home.to_string_lossy().into_owned(),
        plugin_data: plugin_data.to_string_lossy().into_owned(),
        generated_at: now,
        sessions,
    })
}

#[cfg(test)]
mod tests {
    use super::{parse_transcript, session_id_from_stem};
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn extracts_session_id_from_rollout_name() {
        let stem = "rollout-2026-07-17T09-00-00-019f6b19-9d55-7cd3-a195-cf0cbd9ca6da";
        assert_eq!(
            session_id_from_stem(stem),
            Some("019f6b19-9d55-7cd3-a195-cf0cbd9ca6da")
        );
        assert_eq!(session_id_from_stem("not-a-session"), None);
    }

    #[test]
    fn parses_visible_prompts_and_latest_recap() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("thread-recap-transcript-{unique}.jsonl"));
        let content = concat!(
            "{\"type\":\"session_meta\",\"payload\":{\"cwd\":\"C:\\\\work\"}}\n",
            "{\"type\":\"event_msg\",\"payload\":{\"type\":\"user_message\",\"message\":\"Build a small dashboard\"}}\n",
            "{\"type\":\"event_msg\",\"payload\":{\"type\":\"user_message\",\"message\":\"[thread-recap:internal:v1] hidden\"}}\n",
            "{\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"message\":\"Current state\\n[thread-recap:summary:v1 covered-through=turn-1]\"}}\n"
        );
        fs::write(&path, content).unwrap();
        let parsed = parse_transcript(&path);
        let _ = fs::remove_file(path);

        assert_eq!(parsed.cwd.as_deref(), Some("C:\\work"));
        assert_eq!(parsed.title.as_deref(), Some("Build a small dashboard"));
        assert_eq!(
            parsed.last_user_message.as_deref(),
            Some("Build a small dashboard")
        );
        assert!(parsed.summary.unwrap().contains("Current state"));
    }
}
