#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# Usage
# ============================================================

if [ $# -ne 1 ]; then
    echo "Usage: sudo $0 <benchmark>"
    echo
    echo "Example:"
    echo "  sudo $0 imagepro"
    exit 1
fi

BENCHMARK="$1"
BENCHMARK=$(echo "$BENCHMARK" | tr '[:upper:]' '[:lower:]')

# ============================================================
# Configuration
# ============================================================

IMAGE="imsuraj/${BENCHMARK}-checkpoint:latest"
CKPT="/home/suraj/checkpoints/${BENCHMARK}/warm-parent"

echo
echo "========================================"
echo "Warm-Pool Replacement Overhead"
echo "========================================"
echo "Benchmark:  $BENCHMARK"
echo "Image:      $IMAGE"
echo "Checkpoint: $CKPT"
echo "========================================"
echo


# ============================================================
# Basic checks
# ============================================================

if [ ! -d "$CKPT" ]; then
    echo "ERROR: Checkpoint directory does not exist:"
    echo "  $CKPT"
    exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: Docker image does not exist:"
    echo "  $IMAGE"
    exit 1
fi


# ============================================================
# Find all existing containers for this benchmark
# ============================================================

mapfile -t CONTAINERS < <(
    docker ps -aq \
        --filter "name=^/${BENCHMARK}-"
)

NUM_CONTAINERS=${#CONTAINERS[@]}

if [ "$NUM_CONTAINERS" -eq 0 ]; then
    echo "ERROR: No containers found for benchmark '$BENCHMARK'."
    exit 1
fi

echo "Existing containers:"
docker ps -a \
    --filter "name=^/${BENCHMARK}-" \
    --format "  {{.Names}}"

echo
echo "Number of containers to replace: $NUM_CONTAINERS"
echo


# ============================================================
# TIMED REPLACEMENT
# ============================================================

T0=$(date +%s%N)


# ---------------------------------------------------------
# 1. Destroy ALL existing containers
# ---------------------------------------------------------

docker rm -f "${CONTAINERS[@]}" >/dev/null

T1=$(date +%s%N)


# ---------------------------------------------------------
# 2. Create N replacement containers
# ---------------------------------------------------------

for i in $(seq 1 "$NUM_CONTAINERS"); do

    docker create \
        --name "${BENCHMARK}-clone${i}" \
        --network=host \
        --security-opt seccomp=unconfined \
        "$IMAGE" >/dev/null

done

T2=$(date +%s%N)


# ---------------------------------------------------------
# 3. Copy common-parent checkpoint to all N containers
# ---------------------------------------------------------

for i in $(seq 1 "$NUM_CONTAINERS"); do

    NAME="${BENCHMARK}-clone${i}"

    CID=$(docker inspect -f '{{.Id}}' "$NAME")

    mkdir -p \
        "/var/lib/docker/containers/$CID/checkpoints/warm"

    cp -a \
        "$CKPT"/. \
        "/var/lib/docker/containers/$CID/checkpoints/warm/"

done

T3=$(date +%s%N)


# ---------------------------------------------------------
# 4. Restore all N containers
# ---------------------------------------------------------

for i in $(seq 1 "$NUM_CONTAINERS"); do

    docker start \
        --checkpoint warm \
        "${BENCHMARK}-clone${i}" >/dev/null

done

T4=$(date +%s%N)


# ============================================================
# Calculate results
# ============================================================

python3 - <<PY
t0=$T0
t1=$T1
t2=$T2
t3=$T3
t4=$T4
n=$NUM_CONTAINERS

destroy=(t1-t0)/1e6
create=(t2-t1)/1e6
copy=(t3-t2)/1e6
restore=(t4-t3)/1e6
total=(t4-t0)/1e6

print()
print("========================================")
print("Warm-Pool Replacement Overhead")
print("========================================")
print(f"Containers replaced: {n}")
print()
print(f"Destroy all:          {destroy:.3f} ms")
print(f"Create all:           {create:.3f} ms")
print(f"Checkpoint copy all:  {copy:.3f} ms")
print(f"CRIU restore all:     {restore:.3f} ms")
print("----------------------------------------")
print(f"Total:                {total:.3f} ms")
print()
print(f"Average per container: {total/n:.3f} ms")
print("========================================")
PY


# ============================================================
# Show resulting cloned warm pool
# ============================================================

echo
echo "Restored containers:"
docker ps \
    --filter "name=^/${BENCHMARK}-clone" \
    --format "  {{.Names}}    {{.Status}}"