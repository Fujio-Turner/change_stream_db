#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# csdb_collect.sh — Plan B: Offline Diagnostics Collector
# ═══════════════════════════════════════════════════════════════════════
#
# Use this script when the webserver has crashed and you can't hit
# POST /_collect.  Run it from the host or exec into the container.
#
# Usage:
#   # From the Docker host (recommended):
#   ./csdb_collect.sh
#
#   # Or exec into the container:
#   docker exec -it <container> bash /app/csdb_collect.sh
#
#   # Specify custom container name and output dir:
#   CONTAINER=my-worker OUTPUT_DIR=/tmp/diag ./csdb_collect.sh
#
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# --- Configuration ---
CONTAINER="${CONTAINER:-changes-worker}"              # docker-compose service name
COMPOSE_SERVICE="${COMPOSE_SERVICE:-changes-worker}"
OUTPUT_DIR="${OUTPUT_DIR:-.}"                          # where to write the zip
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
HOSTNAME_TAG=$(hostname 2>/dev/null || echo "unknown")
COLLECT_NAME="csdb_collect_${HOSTNAME_TAG}_${TIMESTAMP}"
STAGING_DIR=$(mktemp -d "/tmp/${COLLECT_NAME}.XXXXXX")
MAX_LOG_SIZE_MB="${MAX_LOG_SIZE_MB:-200}"

cleanup() {
    rm -rf "$STAGING_DIR" 2>/dev/null || true
}
trap cleanup EXIT

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  CSDB Offline Diagnostics Collector (Plan B)               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Staging: $STAGING_DIR"
echo ""

# --- Detect if we're inside the container or on the host ---
IN_CONTAINER=false
if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    IN_CONTAINER=true
fi

# Helper: run a command inside the container (or locally if already inside)
run_in_container() {
    if [ "$IN_CONTAINER" = true ]; then
        "$@" 2>/dev/null || true
    else
        docker exec "$CONTAINER" "$@" 2>/dev/null || true
    fi
}

# Helper: copy from container to staging
copy_from_container() {
    local src="$1"
    local dest="$2"
    mkdir -p "$(dirname "$dest")"
    if [ "$IN_CONTAINER" = true ]; then
        cp -r "$src" "$dest" 2>/dev/null || true
    else
        docker cp "${CONTAINER}:${src}" "$dest" 2>/dev/null || true
    fi
}

# --- 1. Container state ---
echo "  [1/8] Container state..."
mkdir -p "$STAGING_DIR/container"
if [ "$IN_CONTAINER" = false ]; then
    docker inspect "$CONTAINER" > "$STAGING_DIR/container/inspect.json" 2>/dev/null || true
    docker logs --tail 5000 "$CONTAINER" > "$STAGING_DIR/container/docker_logs.txt" 2>/dev/null || true
    docker stats --no-stream "$CONTAINER" > "$STAGING_DIR/container/stats.txt" 2>/dev/null || true
    docker top "$CONTAINER" > "$STAGING_DIR/container/top.txt" 2>/dev/null || true
fi

# --- 2. Project logs ---
echo "  [2/8] Project logs..."
mkdir -p "$STAGING_DIR/project_logs"
if [ "$IN_CONTAINER" = false ]; then
    # logs/ is typically bind-mounted, so copy from host
    if [ -d "./logs" ]; then
        # Cap total size
        total=0
        for f in $(ls -t ./logs/changes_worker.log* 2>/dev/null); do
            size=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
            if [ $((total + size)) -gt $((MAX_LOG_SIZE_MB * 1024 * 1024)) ]; then
                echo "    (log size cap reached at ${MAX_LOG_SIZE_MB}MB)"
                break
            fi
            cp "$f" "$STAGING_DIR/project_logs/" 2>/dev/null || true
            total=$((total + size))
        done
    else
        copy_from_container /app/logs "$STAGING_DIR/project_logs"
    fi
else
    for f in $(ls -t /app/logs/changes_worker.log* 2>/dev/null); do
        cp "$f" "$STAGING_DIR/project_logs/" 2>/dev/null || true
    done
fi

# --- 3. CBL logs ---
echo "  [3/8] Couchbase Lite logs..."
mkdir -p "$STAGING_DIR/cbl_logs"
copy_from_container /app/data "$STAGING_DIR/cbl_data_raw"
# Only keep .cbllog files
if [ -d "$STAGING_DIR/cbl_data_raw" ]; then
    find "$STAGING_DIR/cbl_data_raw" -name "*.cbllog*" -exec mv {} "$STAGING_DIR/cbl_logs/" \; 2>/dev/null || true
    rm -rf "$STAGING_DIR/cbl_data_raw"
fi

# --- 4. Config (redacted) ---
echo "  [4/8] Configuration (redacted)..."
mkdir -p "$STAGING_DIR/config"
copy_from_container /app/config.json "$STAGING_DIR/config/config_raw.json"
# Basic redaction: mask password/token/secret/key values
if [ -f "$STAGING_DIR/config/config_raw.json" ]; then
    sed -E 's/("(password|token|secret|bearer_token|session_cookie|access_key_id|secret_access_key|api_key)"[[:space:]]*:[[:space:]]*")[^"]+"/\1***REDACTED***"/gi' \
        "$STAGING_DIR/config/config_raw.json" > "$STAGING_DIR/config/config_redacted.json"
    rm "$STAGING_DIR/config/config_raw.json"
fi

# --- 5. System info ---
echo "  [5/8] System diagnostics..."
mkdir -p "$STAGING_DIR/system"

collect_cmd() {
    local name="$1"
    shift
    run_in_container "$@" > "$STAGING_DIR/system/${name}.txt" 2>&1 || true
}

collect_cmd "uname"    uname -a
collect_cmd "ps_aux"   ps aux
collect_cmd "df"       df -h
collect_cmd "ulimit"   sh -c "ulimit -a"

# Linux-specific (Docker containers are Linux)
collect_cmd "top"      top -bn1
collect_cmd "free"     free -m
collect_cmd "netstat"  ss -an
collect_cmd "ip_addr"  ip addr

# Write safe env vars
run_in_container env | grep -E '^(PATH|LANG|LC_ALL|TZ|HOSTNAME|PYTHONPATH|HOME|USER|PWD|SHELL|TERM|VIRTUAL_ENV)=' \
    > "$STAGING_DIR/system/env.txt" 2>/dev/null || true

# --- 6. Process info (if PID is findable) ---
echo "  [6/8] Process info..."
mkdir -p "$STAGING_DIR/profiling"
PID=$(run_in_container pgrep -f "python.*main.py" | head -1)
if [ -n "$PID" ]; then
    echo "    Found PID: $PID"
    # /proc info (Linux containers)
    run_in_container cat /proc/$PID/status > "$STAGING_DIR/profiling/proc_status.txt" 2>/dev/null || true
    run_in_container cat /proc/$PID/limits > "$STAGING_DIR/profiling/proc_limits.txt" 2>/dev/null || true
    run_in_container cat /proc/$PID/io     > "$STAGING_DIR/profiling/proc_io.txt" 2>/dev/null || true
    run_in_container ls -la /proc/$PID/fd  > "$STAGING_DIR/profiling/proc_fds.txt" 2>/dev/null || true
    run_in_container cat /proc/$PID/maps | head -200 > "$STAGING_DIR/profiling/proc_maps.txt" 2>/dev/null || true

    # Thread stacks via py-spy (if installed)
    run_in_container py-spy dump --pid $PID > "$STAGING_DIR/profiling/pyspy_threads.txt" 2>/dev/null || true
else
    echo "    Python process not found (may have crashed)"
    echo "Process not running" > "$STAGING_DIR/profiling/proc_status.txt"
fi

# --- 7. Metrics snapshot (try HTTP even if app is semi-alive) ---
echo "  [7/8] Metrics snapshot..."
run_in_container curl -s --max-time 5 http://localhost:9090/_metrics \
    > "$STAGING_DIR/metrics_snapshot.txt" 2>/dev/null || \
    echo "Could not reach /_metrics endpoint" > "$STAGING_DIR/metrics_snapshot.txt"

# --- 8. Metadata ---
echo "  [8/8] Metadata..."
cat > "$STAGING_DIR/collect_info.json" <<EOF
{
  "collector": "csdb_collect.sh (Plan B offline)",
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "hostname": "$HOSTNAME_TAG",
  "in_container": $IN_CONTAINER,
  "container_name": "$CONTAINER",
  "python_pid": "${PID:-not_found}"
}
EOF

# --- Create zip ---
echo ""
echo "Creating zip..."
ZIP_FILE="${OUTPUT_DIR}/${COLLECT_NAME}.zip"
(cd "$(dirname "$STAGING_DIR")" && zip -r "$ZIP_FILE" "$(basename "$STAGING_DIR")" -x '*.DS_Store') > /dev/null 2>&1 || \
    (cd "$(dirname "$STAGING_DIR")" && tar czf "${ZIP_FILE%.zip}.tar.gz" "$(basename "$STAGING_DIR")")

if [ -f "$ZIP_FILE" ]; then
    SIZE=$(du -h "$ZIP_FILE" | cut -f1)
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  ✅ Collection complete                                     ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  File: $ZIP_FILE"
    echo "  Size: $SIZE"
    echo ""
    echo "  Extract with: unzip $ZIP_FILE"
elif [ -f "${ZIP_FILE%.zip}.tar.gz" ]; then
    SIZE=$(du -h "${ZIP_FILE%.zip}.tar.gz" | cut -f1)
    echo ""
    echo "  File: ${ZIP_FILE%.zip}.tar.gz (zip not available, used tar)"
    echo "  Size: $SIZE"
else
    echo "  ❌ Failed to create archive"
    exit 1
fi
