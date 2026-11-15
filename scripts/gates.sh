#!/bin/sh
set -eu

# Each nonempty entry is an explicit repository-root command owned by this task.
manifest=${1:-docs/build/E01/E01.gates}
if [ ! -r "$manifest" ]; then
    echo "Missing gate manifest: $manifest" >&2
    exit 1
fi
count=0
failed=0
while IFS= read -r command || [ -n "$command" ]; do
    case "$command" in
        ''|'#'*) continue ;;
    esac
    if [ "$count" -ge 1024 ]; then
        echo "Gate manifest exceeds the checker command ceiling" >&2
        exit 1
    fi
    count=$((count + 1))
    echo "Running gate $count: $command"
    # A gate must never consume later commands from the manifest on standard input.
    if sh -eu -c "$command" < /dev/null; then
        status=0
    else
        status=$?
        failed=$((failed + 1))
    fi
    echo "Gate $count exit status: $status"
done < "$manifest"
if [ "$count" -eq 0 ]; then
    echo "Empty gate manifest: $manifest" >&2
    exit 1
fi
echo "Gate summary: $count commands, $failed failed"
if [ "$failed" -ne 0 ]; then
    exit 1
fi
