#!/bin/sh
set -eu

# Each nonempty entry is an explicit repository-root command owned by this task.
manifest=${1:-docs/build/E01/T2.gates}
if [ ! -r "$manifest" ]; then
    echo "Missing gate manifest: $manifest" >&2
    exit 1
fi
count=0
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
    sh -eu -c "$command"
done < "$manifest"
if [ "$count" -eq 0 ]; then
    echo "Empty gate manifest: $manifest" >&2
    exit 1
fi
