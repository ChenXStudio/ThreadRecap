# ThreadRecap

[Chinese](README.zh-CN.md)

ThreadRecap is a local Codex plugin that automatically adds a structured recap
to the bottom of a task after five minutes of inactivity.

The idle timer starts only after Codex reports that the current turn is
complete. Time spent replying or using tools does not count as idle time. A new
user message invalidates the previous timer, each task keeps independent state,
and ThreadRecap's own recap turn cannot trigger another recap.

## Features

- Starts the five-minute timer after the active Codex turn completes
- Resets the timer when a new user message arrives
- Writes the recap back to the original task
- Keeps timers and recap cursors isolated between tasks
- Prevents recursive recap generation
- Recovers from restarts with SQLite-backed state
- Runs locally without a separate OpenAI API key
- Supports Windows, macOS, and Linux

## Requirements

- Codex with plugin hooks and app-server support
- Python 3.10 or newer, available as `py -3`, `python3`, or `python`

## Installation

Add the GitHub repository as a Codex plugin marketplace, then install
ThreadRecap:

```console
codex plugin marketplace add ChenXStudio/ThreadRecap
codex plugin add thread-recap@thread-recap
```

Start a new Codex task after installation. When Codex asks you to review the
plugin hook, trust and enable the ThreadRecap hook. You can also open `/hooks`
and approve the hook there.

To install from a local clone instead, the repository can live at any path:

```console
git clone https://github.com/ChenXStudio/ThreadRecap.git
cd ThreadRecap
codex plugin marketplace add .
codex plugin add thread-recap@thread-recap
```

## Usage

No command is required during normal use:

1. Work in a Codex task as usual.
2. Wait until the current Codex turn is complete.
3. Leave the task without new messages for five minutes.
4. ThreadRecap verifies that the task is still idle and appends a recap.

The five-minute period is the trigger threshold. Generating the recap may take
additional time.

You can also request a recap manually with `$thread-recap`.

## Recap structure

Each recap contains six sections:

1. Phase goal
2. Completed work
3. Key decisions
4. Current state
5. TODOs and next steps
6. Risks or blockers

The first recap covers the available task history. Later recaps focus on normal
turns added after the previous recap while refreshing the overall state. Tool
logs are not copied line by line.

## Privacy and storage

ThreadRecap uses the existing Codex login and the local Codex app-server. It
does not require a separate API key or send transcripts to a third-party
summarization service.

Coordination state is stored in `PLUGIN_DATA/state.db`, and diagnostic messages
are written to `PLUGIN_DATA/worker.log`. Transcript bodies are not copied into
the SQLite database. Plugin files are resolved through `PLUGIN_ROOT`, so no
machine-specific installation path is required.

## Troubleshooting

- **The hook is skipped:** open `/hooks`, review the ThreadRecap hook, and trust
  it.
- **The plugin still behaves like an older build:** reinstall the plugin and
  start a new task.
- **No recap appears:** confirm the Codex turn has completed, wait at least five
  minutes, and inspect `worker.log` for a sanitized error code.
- **Python cannot be found:** install Python 3.10 or newer and ensure a supported
  Python command is available on `PATH`.
- **Codex cannot be found:** make the `codex` executable available on `PATH`.
  Advanced environments can set `THREAD_RECAP_CODEX` to an explicit executable
  path without changing the repository.

## Uninstallation

```console
codex plugin remove thread-recap@thread-recap
codex plugin marketplace remove thread-recap
```

## Current limitations

- The idle timeout is fixed at 300 seconds.
- There is no settings interface.
- Same-task writeback creates a visible internal user turn before the generated
  recap.
