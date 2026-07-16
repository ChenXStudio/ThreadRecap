#!/bin/sh

log_file=

report_error() {
    message=$1
    printf '%s\n' "$message" >&2
    if [ -n "$log_file" ]; then
        printf '%s\n' "$message" >>"$log_file"
    fi
}

if [ -z "${PLUGIN_ROOT:-}" ]; then
    report_error "ThreadRecap cannot start: PLUGIN_ROOT is missing or empty. Run this hook through Codex plugin loading."
    exit 2
fi

if [ -z "${PLUGIN_DATA:-}" ]; then
    report_error "ThreadRecap cannot start: PLUGIN_DATA is missing or empty. Run this hook through Codex plugin loading."
    exit 2
fi

if ! mkdir -p "$PLUGIN_DATA" 2>/dev/null; then
    report_error "ThreadRecap cannot start: PLUGIN_DATA cannot be created or is not writable: $PLUGIN_DATA"
    exit 3
fi

log_file="$PLUGIN_DATA/worker.log"
if ! (umask 077; : >>"$log_file") 2>/dev/null; then
    log_file=
    report_error "ThreadRecap cannot start: PLUGIN_DATA cannot be created or is not writable: $PLUGIN_DATA"
    exit 3
fi

if [ ! -d "$PLUGIN_ROOT" ]; then
    report_error "ThreadRecap cannot start: PLUGIN_ROOT is not a directory: $PLUGIN_ROOT"
    exit 4
fi

entry_point="$PLUGIN_ROOT/scripts/hook_entry.py"
if [ ! -f "$entry_point" ]; then
    report_error "ThreadRecap cannot start: hook entry point is missing: $entry_point"
    exit 4
fi

python_command=
if command -v python3 >/dev/null 2>&1 &&
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    python_command=python3
elif command -v python >/dev/null 2>&1 &&
    python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    python_command=python
fi

if [ -z "$python_command" ]; then
    report_error "ThreadRecap requires Python 3.10 or newer. Install it and ensure python3 or python is on PATH."
    exit 5
fi

"$python_command" "$entry_point"
status=$?
if [ "$status" -ne 0 ]; then
    report_error "ThreadRecap hook_entry.py exited with status $status."
fi
exit "$status"
