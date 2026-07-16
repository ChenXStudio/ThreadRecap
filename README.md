# ThreadRecap

ThreadRecap is a local Codex plugin that adds a structured checkpoint to the
bottom of a task after the task has been inactive for five minutes.

The timer starts only after Codex reports that the current turn is complete.
While Codex is replying or using tools, the task is not considered idle. A new
ordinary user message invalidates the old timer. Each task has isolated state,
and ThreadRecap's own generated turn cannot schedule another recap.

## Requirements

- Codex with plugin hooks and app-server support
- Python 3.10 or newer available as `py -3`, `python3`, or `python`
- Windows, macOS, or Linux

ThreadRecap uses the user's existing Codex login. It does not require a separate
OpenAI API key or send transcripts to a third-party summarization service.

## Install from GitHub

```console
codex plugin marketplace add ChenXStudio/ThreadRecap
codex plugin add thread-recap@thread-recap
```

Review and trust the plugin hook when Codex asks. Start a new task after
installation so the plugin components are loaded.

For local development, clone the repository anywhere and add that directory;
no fixed checkout path is required:

```console
git clone https://github.com/ChenXStudio/ThreadRecap.git
cd ThreadRecap
codex plugin marketplace add .
codex plugin add thread-recap@thread-recap
```

After changing the plugin, reinstall it and test in a new task. To remove it:

```console
codex plugin remove thread-recap@thread-recap
codex plugin marketplace remove thread-recap
```

## What the recap contains

The `$thread-recap` skill produces these sections:

1. 阶段目标
2. 已完成
3. 关键决定
4. 当前状态
5. 待办与下一步
6. 风险或阻塞

The first recap covers the available task. Later recaps emphasize ordinary
turns after the previous recap while refreshing the overall state. Tool logs
are not copied line by line.

## How it works

The plugin's `UserPromptSubmit`, `Stop`, and `SessionStart` hooks write only
coordination metadata to `PLUGIN_DATA/state.db`. A detached Python worker uses
the stable Codex app-server thread and turn APIs to confirm `completed` status,
wait 300 seconds, re-check that no newer ordinary turn exists, and append the
recap to the same task.

Source paths come from `PLUGIN_ROOT`; writable state and `worker.log` go only to
`PLUGIN_DATA`. The repository contains no machine-specific installation path.
Transcript bodies are not copied into the SQLite database.

## Troubleshooting

- **Hook is skipped:** open `/hooks`, review the ThreadRecap hook, and trust it.
- **Python error:** install Python 3.10+ and ensure one supported command is on
  `PATH`.
- **No recap after five minutes:** confirm the turn has actually completed and
  inspect `worker.log` in the plugin data directory for a sanitized error code.
- **Codex executable is not discoverable:** make `codex` available on `PATH`.
  Advanced launch environments can set `THREAD_RECAP_CODEX` to an explicit
  executable without changing repository files.
- **Plugin was updated but behaves like the old build:** reinstall it and start
  a new task.

## Current MVP limits

- The cooldown is fixed at 300 seconds.
- There is no settings UI.
- Triggering a recap creates a visible internal user turn before the generated
  summary because same-task app-server writeback is used.
