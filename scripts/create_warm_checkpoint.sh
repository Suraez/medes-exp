#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# Usage
# ============================================================

if [ $# -ne 2 ]; then
    echo "Usage: sudo $0 <benchmark> <payload.json>"
    echo "Example: sudo $0 modeltrain modeltrain.json"
    exit 1
fi

BENCHMARK=$(echo "$1" | tr '[:upper:]' '[:lower:]')
PAYLOAD="$2"

IMAGE="imsuraj/${BENCHMARK}-checkpoint:latest"
PARENT="${BENCHMARK}-parent"

# Since script is normally run with sudo, use the original user's home.
if [ -n "${SUDO_USER:-}" ]; then
    USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    USER_HOME="$HOME"
fi

CKPT="${USER_HOME}/checkpoints/${BENCHMARK}/warm-parent"


# ============================================================
# Checks
# ============================================================

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: Docker image not found: $IMAGE"
    exit 1
fi

if [ ! -f "$PAYLOAD" ]; then
    echo "ERROR: Payload file not found: $PAYLOAD"
    exit 1
fi


# ============================================================
# Clean previous parent/checkpoint
# ============================================================

docker rm -f "$PARENT" >/dev/null 2>&1 || true

rm -rf "$CKPT"
mkdir -p "$CKPT"


# ============================================================
# Start parent
# ============================================================

docker run -d \
    --name "$PARENT" \
    --network=host \
    --security-opt seccomp=unconfined \
    "$IMAGE" >/dev/null


# ============================================================
# Wait until HTTP server is ready
# ============================================================

for i in $(seq 1 60); do
    if curl -s \
        --connect-timeout 1 \
        -o /dev/null \
        http://127.0.0.1:8080/; then
        break
    fi

    sleep 0.5

    if [ "$i" -eq 60 ]; then
        echo "ERROR: Parent container did not become ready."
        docker rm -f "$PARENT" >/dev/null 2>&1 || true
        exit 1
    fi
done


# ============================================================
# Warm parent with one invocation
#
# This is NOT part of checkpoint-creation time.
# ============================================================

HTTP_CODE=$(curl -s \
    -o /tmp/${BENCHMARK}-warm-response.txt \
    -w "%{http_code}" \
    -X POST \
    http://127.0.0.1:8080/run \
    -H "Content-Type: application/json" \
    --data-binary "@$PAYLOAD")

if [ "$HTTP_CODE" -lt 200 ] || [ "$HTTP_CODE" -ge 300 ]; then
    echo "ERROR: Warm invocation failed (HTTP $HTTP_CODE)."
    cat /tmp/${BENCHMARK}-warm-response.txt
    echo
    exit 1
fi


# ============================================================
# Create Docker/CRIU checkpoint
#
# Only this operation is timed.
# ============================================================

T0=$(date +%s%N)

docker checkpoint create "$PARENT" warm >/dev/null

T1=$(date +%s%N)


# ============================================================
# Copy checkpoint to permanent location
#
# This copy is intentionally NOT included in checkpoint
# creation time.
# ============================================================

PARENT_ID=$(docker inspect -f '{{.Id}}' "$PARENT")

cp -a \
    "/var/lib/docker/containers/${PARENT_ID}/checkpoints/warm/." \
    "$CKPT/"


# ============================================================
# Remove checkpointed parent container
# ============================================================

docker rm -f "$PARENT" >/dev/null 2>&1 || true


# ============================================================
# Result
# ============================================================

python3 - <<PY
t0 = $T0
t1 = $T1

checkpoint_ms = (t1 - t0) / 1e6
checkpoint_s  = (t1 - t0) / 1e9

print()
print("========================================")
print("Warm-Parent Checkpoint Created")
print("========================================")
print("Benchmark:        $BENCHMARK")
print("Checkpoint:       $CKPT")
print(f"Creation time:    {checkpoint_ms:.3f} ms")
print(f"                  {checkpoint_s:.3f} s")
print("========================================")
PY