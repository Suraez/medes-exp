#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Clone Sharing Before/After Execution
# ============================================================
#
# Usage:
#
#   sudo ./measure_clone_sharing_after_execution.sh \
#       <image> \
#       <payload.json>
#
# Example:
#
#   sudo ./measure_clone_sharing_after_execution.sh \
#       imsuraj/modeltrain-checkpoint:latest \
#       modeltrain_host.json
#
#
# Experiment:
#
#   1. Restore clone1 from common warm checkpoint
#      -> measure M1_before
#
#   2. Restore clone2 from same checkpoint
#      -> measure M2_before
#
#   3. Calculate sharing_before
#
#   4. Pause clone2
#      Invoke exactly one request on clone1
#      Measure Delta_C1 from parent cgroup
#
#   5. Unpause clone2
#      Pause clone1
#      Invoke exactly one request on clone2
#      Measure Delta_C2 from parent cgroup
#
#   6. Estimate:
#
#      M1_after = M1_before + Delta_C1
#
#      M2_after = M2_before + Delta_C1 + Delta_C2
#
#   7. Calculate sharing_after using the same marginal-memory
#      sharing formula.
#
# Both clones remain alive throughout the experiment.
#
# ============================================================


# ============================================================
# Arguments
# ============================================================

if [[ $# -ne 2 ]]; then
    echo "Usage:"
    echo "  sudo $0 <image> <payload.json>"
    echo
    echo "Example:"
    echo "  sudo $0 imsuraj/modeltrain-checkpoint:latest modeltrain_host.json"
    exit 1
fi

IMAGE="$1"
PAYLOAD="$2"


# ============================================================
# Derive benchmark name from image
#
# Example:
#   imsuraj/modeltrain-checkpoint:latest
#                  ↓
#              modeltrain
# ============================================================

IMAGE_BASE=$(basename "$IMAGE")
BENCHMARK="${IMAGE_BASE%%-checkpoint*}"
BENCHMARK="${BENCHMARK%%:*}"


# ============================================================
# Configuration
# ============================================================

CHECKPOINT_SRC="/home/suraj/checkpoints/${BENCHMARK}/warm-parent"

CLONE1="${BENCHMARK}-clone1"
CLONE2="${BENCHMARK}-clone2"

PARENT_CGROUP="/sys/fs/cgroup/docker_limit.slice"

URL="http://127.0.0.1:8080/run"

WAIT_SECONDS=1


# ============================================================
# Helper functions
# ============================================================

memory_current() {
    cat "${PARENT_CGROUP}/memory.current"
}


get_pid() {
    docker inspect -f '{{.State.Pid}}' "$1"
}


copy_checkpoint() {

    local container="$1"
    local id
    local dest

    id=$(docker inspect -f '{{.Id}}' "$container")

    dest="/var/lib/docker/containers/${id}/checkpoints/warm"

    mkdir -p "$dest"

    cp -a "${CHECKPOINT_SRC}/." "$dest/"
}


invoke() {

    local label="$1"

    local response_file
    response_file="/tmp/${BENCHMARK}_${label}_response.json"

    local http_code

    http_code=$(
        curl -sS \
            -o "$response_file" \
            -w "%{http_code}" \
            -X POST \
            -H "Content-Type: application/json" \
            --data @"${PAYLOAD}" \
            "$URL"
    )

    echo "HTTP status: $http_code"

    if [[ "$http_code" != "200" ]]; then

        echo
        echo "ERROR: invocation failed."
        echo "Response:"
        cat "$response_file" || true
        echo

        exit 1
    fi
}


show_smaps() {

    local container="$1"
    local pid

    pid=$(get_pid "$container")

    echo "----------------------------------------"
    echo "$container"
    echo "PID: $pid"
    echo "----------------------------------------"

    grep -E \
        '^(Rss|Pss|Anonymous|Shared_Clean|Shared_Dirty|Private_Clean|Private_Dirty):' \
        "/proc/${pid}/smaps_rollup"

    echo
}


# ============================================================
# Validate inputs
# ============================================================

if [[ ! -f "$PAYLOAD" ]]; then
    echo "ERROR: payload file not found:"
    echo "$PAYLOAD"
    exit 1
fi

if [[ ! -d "$CHECKPOINT_SRC" ]]; then
    echo "ERROR: checkpoint directory not found:"
    echo "$CHECKPOINT_SRC"
    exit 1
fi


# ============================================================
# Print configuration
# ============================================================

echo
echo "========================================"
echo "Clone Sharing Before/After Execution"
echo "========================================"
echo "Benchmark:  $BENCHMARK"
echo "Image:      $IMAGE"
echo "Checkpoint: $CHECKPOINT_SRC"
echo "Payload:    $PAYLOAD"
echo "URL:        $URL"
echo "========================================"


# ============================================================
# Clean Docker environment
# ============================================================

echo
echo "Cleaning Docker environment..."

docker rm -f $(docker ps -aq) 2>/dev/null || true

sleep "$WAIT_SECONDS"


# ============================================================
# Baseline
# ============================================================

M0=$(memory_current)

echo
echo "Parent-cgroup baseline:"
echo "M0 = $M0 bytes"


# ============================================================
# Create both clone containers
# ============================================================

echo
echo "========================================"
echo "Creating clone containers"
echo "========================================"

docker create \
    --name "$CLONE1" \
    --network=host \
    --security-opt seccomp=unconfined \
    "$IMAGE" >/dev/null

docker create \
    --name "$CLONE2" \
    --network=host \
    --security-opt seccomp=unconfined \
    "$IMAGE" >/dev/null


# ============================================================
# Copy checkpoint
# ============================================================

echo
echo "Copying warm checkpoint to clone1..."
copy_checkpoint "$CLONE1"

echo "Copying warm checkpoint to clone2..."
copy_checkpoint "$CLONE2"


# ============================================================
# Restore clone1
# ============================================================

echo
echo "========================================"
echo "Restoring Clone 1"
echo "========================================"

docker start --checkpoint warm "$CLONE1" >/dev/null

sleep "$WAIT_SECONDS"

M1_PARENT=$(memory_current)

# Remove parent-cgroup baseline
M1_BEFORE=$((M1_PARENT - M0))

echo
echo "Parent cgroup: $M1_PARENT bytes"
echo "M1_before:     $M1_BEFORE bytes"


# ============================================================
# Restore clone2
# ============================================================

echo
echo "========================================"
echo "Restoring Clone 2"
echo "========================================"

docker start --checkpoint warm "$CLONE2" >/dev/null

sleep "$WAIT_SECONDS"

M2_PARENT=$(memory_current)

# Remove parent-cgroup baseline
M2_BEFORE=$((M2_PARENT - M0))

echo
echo "Parent cgroup: $M2_PARENT bytes"
echo "M2_before:     $M2_BEFORE bytes"


# ============================================================
# BEFORE execution smaps
# ============================================================

echo
echo "========================================"
echo "SMAPS BEFORE EXECUTION"
echo "========================================"

show_smaps "$CLONE1"
show_smaps "$CLONE2"


# ============================================================
# Invoke Clone 1
# ============================================================

echo
echo "========================================"
echo "Executing ONE Request on Clone 1"
echo "========================================"

echo "Pausing $CLONE2..."
docker pause "$CLONE2" >/dev/null

sleep 0.2

P_BEFORE_C1=$(memory_current)

echo "Parent cgroup before C1 request:"
echo "$P_BEFORE_C1 bytes"

echo
echo "Invoking $CLONE1..."

invoke "clone1"

sleep "$WAIT_SECONDS"

P_AFTER_C1=$(memory_current)

DELTA_C1=$((P_AFTER_C1 - P_BEFORE_C1))

echo
echo "Parent cgroup after C1 request:"
echo "$P_AFTER_C1 bytes"

echo
echo "Delta C1:"
echo "$DELTA_C1 bytes"

echo
echo "Unpausing $CLONE2..."
docker unpause "$CLONE2" >/dev/null

sleep 0.2


# ============================================================
# Invoke Clone 2
# ============================================================

echo
echo "========================================"
echo "Executing ONE Request on Clone 2"
echo "========================================"

echo "Pausing $CLONE1..."
docker pause "$CLONE1" >/dev/null

sleep 0.2

P_BEFORE_C2=$(memory_current)

echo "Parent cgroup before C2 request:"
echo "$P_BEFORE_C2 bytes"

echo
echo "Invoking $CLONE2..."

invoke "clone2"

sleep "$WAIT_SECONDS"

P_AFTER_C2=$(memory_current)

DELTA_C2=$((P_AFTER_C2 - P_BEFORE_C2))

echo
echo "Parent cgroup after C2 request:"
echo "$P_AFTER_C2 bytes"

echo
echo "Delta C2:"
echo "$DELTA_C2 bytes"

echo
echo "Unpausing $CLONE1..."
docker unpause "$CLONE1" >/dev/null

sleep "$WAIT_SECONDS"


# ============================================================
# Final parent-cgroup memory
# ============================================================

P_FINAL=$(memory_current)

echo
echo "Final parent-cgroup memory:"
echo "$P_FINAL bytes"


# ============================================================
# AFTER execution smaps
# ============================================================

echo
echo "========================================"
echo "SMAPS AFTER EXECUTION"
echo "========================================"

show_smaps "$CLONE1"
show_smaps "$CLONE2"


# ============================================================
# Calculate results
# ============================================================

python3 - <<PY

M0 = $M0

M1_before = $M1_BEFORE
M2_before = $M2_BEFORE

delta_c1 = $DELTA_C1
delta_c2 = $DELTA_C2

p_final = $P_FINAL

MiB = 1024 * 1024


# ============================================================
# BEFORE execution
# ============================================================

marginal_before = M2_before - M1_before

sharing_before = (
    (M1_before - marginal_before)
    / M1_before
) * 100


# ============================================================
# AFTER execution
# ============================================================

M1_after = M1_before + delta_c1

M2_after = (
    M2_before
    + delta_c1
    + delta_c2
)

marginal_after = M2_after - M1_after

sharing_after = (
    (M1_after - marginal_after)
    / M1_after
) * 100


# ============================================================
# Sharing change
# ============================================================

sharing_change = sharing_after - sharing_before
sharing_loss = sharing_before - sharing_after


# ============================================================
# Output
# ============================================================

print()
print("============================================================")
print("FINAL RESULTS")
print("============================================================")

print()
print("BEFORE EXECUTION")
print("----------------")
print(f"M1_before:                 {M1_before / MiB:.3f} MiB")
print(f"M2_before:                 {M2_before / MiB:.3f} MiB")
print(f"Second-clone marginal:     {marginal_before / MiB:.3f} MiB")
print(f"Sharing before:            {sharing_before:.2f}%")

print()
print("REQUEST EXECUTION")
print("-----------------")
print(f"Clone 1 memory increase:   {delta_c1 / MiB:.3f} MiB")
print(f"Clone 2 memory increase:   {delta_c2 / MiB:.3f} MiB")

print()
print("AFTER EXECUTION")
print("---------------")
print(f"M1_after (estimated):      {M1_after / MiB:.3f} MiB")
print(f"M2_after (estimated):      {M2_after / MiB:.3f} MiB")
print(f"Second-clone marginal:     {marginal_after / MiB:.3f} MiB")
print(f"Sharing after:             {sharing_after:.2f}%")

print()
print("SHARING CHANGE")
print("--------------")
print(f"Before:                    {sharing_before:.2f}%")
print(f"After:                     {sharing_after:.2f}%")
print(f"Change:                    {sharing_change:+.2f} percentage points")
print(f"Sharing loss:              {sharing_loss:.2f} percentage points")

print()
print("SANITY CHECK")
print("------------")
print(f"Final parent cgroup:       {p_final / MiB:.3f} MiB")
print(f"Final minus baseline:      {(p_final - M0) / MiB:.3f} MiB")
print(f"Estimated M2_after:        {M2_after / MiB:.3f} MiB")

print()
print("============================================================")

