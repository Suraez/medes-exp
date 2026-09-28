#!/usr/bin/env bash
set -uo pipefail

# ============================================================
# Measure warm-pool replacement overhead with STAGGERED restore
#
# Usage:
#   sudo ./measure_replacement_staggered.sh <benchmark> <stagger_seconds>
#
# Examples:
#   sudo ./measure_replacement_staggered.sh modeltrain 0
#   sudo ./measure_replacement_staggered.sh modeltrain 0.25
#   sudo ./measure_replacement_staggered.sh modeltrain 0.5
#   sudo ./measure_replacement_staggered.sh modeltrain 1
#
# The script:
#   1. Counts existing benchmark containers (N)
#   2. Destroys all N
#   3. Creates N replacement containers
#   4. Copies the same warm-parent checkpoint to each
#   5. Launches restores with a configurable stagger
#   6. Waits for all restore attempts
#   7. Reports successes/failures and timing
# ============================================================

if [[ $# -ne 2 ]]; then
    echo "Usage:"
    echo "  sudo $0 <benchmark> <stagger_seconds>"
    exit 1
fi

BENCHMARK=$(echo "$1" | tr '[:upper:]' '[:lower:]')
STAGGER="$2"

IMAGE="imsuraj/${BENCHMARK}-checkpoint:latest"
CKPT="/home/suraj/checkpoints/${BENCHMARK}/warm-parent"

# ------------------------------------------------------------
# Helper: nanoseconds -> milliseconds
# ------------------------------------------------------------

ns_to_ms() {
    awk -v ns="$1" 'BEGIN { printf "%.3f", ns / 1000000 }'
}

# ------------------------------------------------------------
# Validate stagger
# ------------------------------------------------------------

if ! awk -v x="$STAGGER" \
    'BEGIN { exit !(x ~ /^[0-9]+([.][0-9]+)?$/) }'; then
    echo "ERROR: stagger must be a non-negative number."
    echo "Examples: 0, 0.1, 0.25, 0.5, 1"
    exit 1
fi

# ------------------------------------------------------------
# Validate image/checkpoint
# ------------------------------------------------------------

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: Docker image not found:"
    echo "  $IMAGE"
    exit 1
fi

if [[ ! -d "$CKPT" ]]; then
    echo "ERROR: Warm-parent checkpoint directory not found:"
    echo "  $CKPT"
    exit 1
fi

if [[ -z "$(find "$CKPT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "ERROR: Warm-parent checkpoint directory is empty:"
    echo "  $CKPT"
    exit 1
fi

# ------------------------------------------------------------
# Find existing benchmark containers
# ------------------------------------------------------------

mapfile -t OLD_CONTAINERS < <(
    docker ps -a \
        --filter "name=^/${BENCHMARK}-" \
        --format '{{.Names}}'
)

NUM_CONTAINERS=${#OLD_CONTAINERS[@]}

if [[ "$NUM_CONTAINERS" -eq 0 ]]; then
    echo "ERROR: No existing ${BENCHMARK}-* containers found."
    echo
    echo "Create the independent containers first."
    exit 1
fi

echo
echo "========================================"
echo "Warm-Pool Replacement Overhead"
echo "STAGGERED CRIU RESTORE"
echo "========================================"
echo "Benchmark:  $BENCHMARK"
echo "Image:      $IMAGE"
echo "Checkpoint: $CKPT"
echo "Stagger:    $STAGGER seconds"
echo "========================================"
echo
echo "Existing containers:"

for container in "${OLD_CONTAINERS[@]}"; do
    echo "$container"
done

echo
echo "Number of containers to replace: $NUM_CONTAINERS"
echo

# ============================================================
# TOTAL TIMER
# ============================================================

TOTAL_START=$(date +%s%N)

# ============================================================
# 1. Destroy existing containers
# ============================================================

DESTROY_START=$(date +%s%N)

docker rm -f "${OLD_CONTAINERS[@]}" >/dev/null

DESTROY_END=$(date +%s%N)
DESTROY_NS=$((DESTROY_END - DESTROY_START))

# ============================================================
# 2. Create replacements
# ============================================================

CREATE_START=$(date +%s%N)

NEW_CONTAINERS=()

for i in $(seq 1 "$NUM_CONTAINERS"); do

    NAME="${BENCHMARK}-clone${i}"

    docker create \
        --name "$NAME" \
        --network=host \
        --security-opt seccomp=unconfined \
        "$IMAGE" >/dev/null

    NEW_CONTAINERS+=("$NAME")

done

CREATE_END=$(date +%s%N)
CREATE_NS=$((CREATE_END - CREATE_START))

# ============================================================
# 3. Copy checkpoint to every replacement
# ============================================================

COPY_START=$(date +%s%N)

for NAME in "${NEW_CONTAINERS[@]}"; do

    CID=$(docker inspect -f '{{.Id}}' "$NAME")

    DEST="/var/lib/docker/containers/${CID}/checkpoints/warm"

    mkdir -p "$DEST"

    cp -a "$CKPT/." "$DEST/"

done

COPY_END=$(date +%s%N)
COPY_NS=$((COPY_END - COPY_START))

# ============================================================
# 4. STAGGERED restore
# ============================================================

echo
echo "Launching staggered restores..."
echo

RESTORE_START=$(date +%s%N)

PIDS=()
STATUS_FILES=()
TIME_FILES=()

TMPDIR_RUN=$(mktemp -d /tmp/replacement-staggered.XXXXXX)

for i in "${!NEW_CONTAINERS[@]}"; do

    NAME="${NEW_CONTAINERS[$i]}"

    STATUS_FILE="${TMPDIR_RUN}/${NAME}.status"
    TIME_FILE="${TMPDIR_RUN}/${NAME}.time"

    STATUS_FILES+=("$STATUS_FILE")
    TIME_FILES+=("$TIME_FILE")

    echo "Launching $NAME"

    (
        START_NS=$(date +%s%N)

        if docker start --checkpoint warm "$NAME" >/dev/null 2>&1; then
            STATUS=0
        else
            STATUS=$?
        fi

        END_NS=$(date +%s%N)

        DURATION_NS=$((END_NS - START_NS))

        echo "$STATUS" > "$STATUS_FILE"
        echo "$DURATION_NS" > "$TIME_FILE"

        exit "$STATUS"
    ) &

    PIDS+=("$!")

    # Do not sleep after the final launch.
    if [[ "$i" -lt $((NUM_CONTAINERS - 1)) ]]; then
        sleep "$STAGGER"
    fi

done

# ------------------------------------------------------------
# Wait for all restores
# ------------------------------------------------------------

for pid in "${PIDS[@]}"; do
    wait "$pid" || true
done

RESTORE_END=$(date +%s%N)
RESTORE_NS=$((RESTORE_END - RESTORE_START))

TOTAL_END=$(date +%s%N)
TOTAL_NS=$((TOTAL_END - TOTAL_START))

# ============================================================
# Analyze individual restore results
# ============================================================

SUCCESS_COUNT=0
FAIL_COUNT=0

declare -a RESTORE_STATUS
declare -a RESTORE_TIME_MS

for i in "${!NEW_CONTAINERS[@]}"; do

    STATUS_FILE="${STATUS_FILES[$i]}"
    TIME_FILE="${TIME_FILES[$i]}"

    if [[ -f "$STATUS_FILE" ]]; then
        STATUS=$(cat "$STATUS_FILE")
    else
        STATUS=999
    fi

    if [[ -f "$TIME_FILE" ]]; then
        DURATION_NS=$(cat "$TIME_FILE")
        DURATION_MS=$(ns_to_ms "$DURATION_NS")
    else
        DURATION_MS="N/A"
    fi

    RESTORE_STATUS+=("$STATUS")
    RESTORE_TIME_MS+=("$DURATION_MS")

    if [[ "$STATUS" -eq 0 ]]; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

done

# ============================================================
# Convert overall timing
# ============================================================

DESTROY_MS=$(ns_to_ms "$DESTROY_NS")
CREATE_MS=$(ns_to_ms "$CREATE_NS")
COPY_MS=$(ns_to_ms "$COPY_NS")
RESTORE_MS=$(ns_to_ms "$RESTORE_NS")
TOTAL_MS=$(ns_to_ms "$TOTAL_NS")

AVERAGE_MS=$(awk \
    -v total="$TOTAL_MS" \
    -v n="$NUM_CONTAINERS" \
    'BEGIN { printf "%.3f", total / n }')

# ============================================================
# Results
# ============================================================

echo
echo "========================================"
echo "Warm-Pool Replacement Overhead"
echo "STAGGERED CRIU RESTORE"
echo "========================================"
echo "Containers attempted: $NUM_CONTAINERS"
echo "Successful restores:  $SUCCESS_COUNT"
echo "Failed restores:      $FAIL_COUNT"
echo "Stagger:              $STAGGER seconds"
echo
printf "Destroy all:          %s ms\n" "$DESTROY_MS"
printf "Create all:           %s ms\n" "$CREATE_MS"
printf "Checkpoint copy all:  %s ms\n" "$COPY_MS"
printf "CRIU restore phase:   %s ms\n" "$RESTORE_MS"
echo "----------------------------------------"
printf "Total:                %s ms\n" "$TOTAL_MS"
printf "Amortized/container:  %s ms\n" "$AVERAGE_MS"
echo "========================================"

echo
echo "Individual Restore Results"
echo "========================================"

for i in "${!NEW_CONTAINERS[@]}"; do

    NAME="${NEW_CONTAINERS[$i]}"
    STATUS="${RESTORE_STATUS[$i]}"
    TIME="${RESTORE_TIME_MS[$i]}"

    if [[ "$STATUS" -eq 0 ]]; then
        RESULT="SUCCESS"
    else
        RESULT="FAILED"
    fi

    printf "%-25s %-8s %s ms\n" \
        "$NAME" \
        "$RESULT" \
        "$TIME"

done

echo "========================================"

echo
echo "Container states:"
echo

docker ps -a \
    --filter "name=^/${BENCHMARK}-clone" \
    --format "table {{.Names}}\t{{.Status}}"

echo

# ------------------------------------------------------------
# Cleanup temporary measurement files
# ------------------------------------------------------------

rm -rf "$TMPDIR_RUN"

# ------------------------------------------------------------
# Final result
# ------------------------------------------------------------

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "RESULT: FAILED"
    echo "$FAIL_COUNT of $NUM_CONTAINERS restores failed."
    echo
    exit 1
fi

echo "RESULT: SUCCESS"
echo "All $NUM_CONTAINERS containers restored successfully."
echo
