# ThreadRecap acceptance

## Automated contract

- The idle clock starts from app-server turn completion, never prompt submit.
- A new ordinary prompt increments the task generation and invalidates old work.
- The due-time check re-reads the task and requires the covered turn to remain
  the latest ordinary completed turn.
- Summary success advances the cursor only through a generation compare-and-swap.
- Internal ThreadRecap turns are marked and ignored.
- SQLite and logs are rooted only in `PLUGIN_DATA`; plugin code is rooted only
  in `PLUGIN_ROOT`.
- A global renewable worker lease prevents duplicate long-running workers and
  permits recovery after expiry.

## Local verification

The repository test suite, bytecode compilation, plugin validation, and
whitespace/path checks must pass before release. A real installed-plugin smoke
test should record the Codex version and verify same-task writeback after the
full 300-second interval.

## Windows real-plugin smoke test

- Date: 2026-07-16
- Codex CLI: `0.144.5`
- Platform: Windows

The repository marketplace was installed through the normal `codex plugin`
flow. A non-ephemeral `codex exec` task ran with hook trust bypassed only for
that isolated test invocation and a read-only sandbox.

Observed sanitized results:

- app-server mapped the hook session to exactly the same thread id;
- the ordinary turn reached `completed` at epoch `1784195410`;
- SQLite set `due_at` to `1784195710`, exactly 300 seconds later;
- no recap was created before the due time;
- after the due-time recheck, state moved from `cooling` to `generating` and
  then `idle`;
- the recap cursor advanced to the original ordinary turn with zero retries and
  no recorded error;
- a final `thread/read` found one completed internal recap turn in the same
  thread, with the internal client id, agent text, all six required sections,
  and the `[thread-recap:summary:v1 ...]` marker.

The local offline suite passed with 47 tests. macOS and Linux behavior is
covered by the CI matrix and portable launcher tests; real installed-plugin
smoke runs on those platforms remain release follow-up work.
