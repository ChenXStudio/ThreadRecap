# ThreadRecap plugin development

Run the offline test suite from the repository root:

```console
python -m pytest plugins/thread-recap/tests -q
python -m compileall -q plugins/thread-recap/scripts
git diff --check
```

The runtime uses only the Python standard library. SQLite state is stored at
`PLUGIN_DATA/state.db`; logs are stored at `PLUGIN_DATA/worker.log`. The hook
entry point must finish quickly and only wakes the detached worker.

The worker treats Codex app-server `Turn.status == "completed"` as the start of
the five-minute idle period. `Turn.completedAt` is preferred; observation time
is a conservative fallback when an older compatible server omits it.
