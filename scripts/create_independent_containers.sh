#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Create N independent containers and optionally warm them
#
# Usage:
#   ./create_independent_containers.sh <N> <image> [payload.json]
#
# Examples:
#
#   With warm-up request:
#   ./create_independent_containers.sh 3 \
#       imsuraj/modeltrain-checkpoint:latest \
#       modeltrain.json
#
#   Without warm-up request:
#   ./create_independent_containers.sh 3 \
#       imsuraj/modeltrain-checkpoint:latest
# ============================================================

if [[ $# -lt 2 || $# -gt 3 ]]; then
    echo "Usage:"
    echo "  $0 <N> <image> [payload.json]"
    exit 1
fi

N="$1"
IMAGE="$2"
JSON_FILE="${3:-}"

START_PORT=8081

# ------------------------------------------------------------
# Validate N
# ------------------------------------------------------------

if ! [[ "$N" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: N must be a positive integer."
    exit 1
fi

# ------------------------------------------------------------
# Validate Docker image
# ------------------------------------------------------------

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "ERROR: Docker image not found:"
    echo "  $IMAGE"
    exit 1
fi

# ------------------------------------------------------------
# Validate JSON file if supplied
# ------------------------------------------------------------

if [[ -n "$JSON_FILE" ]]; then
    if [[ ! -f "$JSON_FILE" ]]; then
        echo "ERROR: JSON file not found:"
        echo "  $JSON_FILE"
        exit 1
    fi
fi

# ------------------------------------------------------------
# Derive benchmark name from image
#
# Example:
# imsuraj/modeltrain-checkpoint:latest
#              ↓
#          modeltrain
# ------------------------------------------------------------

IMAGE_BASENAME="${IMAGE##*/}"
BENCHMARK="${IMAGE_BASENAME%%-checkpoint:*}"

echo
echo "========================================"
echo "Creating Independent Containers"
echo "========================================"
echo "Benchmark: $BENCHMARK"
echo "Image:     $IMAGE"
echo "N:         $N"

if [[ -n "$JSON_FILE" ]]; then
    echo "Payload:   $JSON_FILE"
else
    echo "Payload:   none"
fi

echo "========================================"
echo

# ------------------------------------------------------------
# Check whether target container names already exist
# ------------------------------------------------------------

for i in $(seq 1 "$N"); do

    NAME="${BENCHMARK}-iC${i}"

    if docker inspect "$NAME" >/dev/null 2>&1; then
        echo "ERROR: Container already exists:"
        echo "  $NAME"
        echo
        echo "Remove existing containers before running this script."
        exit 1
    fi

done

# ------------------------------------------------------------
# Create independent containers
# ------------------------------------------------------------

echo "Creating $N independent containers..."
echo

for i in $(seq 1 "$N"); do

    NAME="${BENCHMARK}-iC${i}"
    PORT=$((START_PORT + i - 1))

    echo "[$i/$N] $NAME"
    echo "      host port: $PORT"

    docker run -d \
        --name "$NAME" \
        -p "${PORT}:8080" \
        "$IMAGE" >/dev/null

done

echo
echo "Containers created."
echo

# ------------------------------------------------------------
# Wait for HTTP servers
# ------------------------------------------------------------

echo "Waiting for containers to become reachable..."

for i in $(seq 1 "$N"); do

    NAME="${BENCHMARK}-iC${i}"
    PORT=$((START_PORT + i - 1))

    printf "  %-25s " "$NAME"

    READY=0

    for attempt in $(seq 1 60); do

        # Any HTTP response means something is listening.
        HTTP_CODE=$(curl -s \
            -o /dev/null \
            -w "%{http_code}" \
            "http://127.0.0.1:${PORT}/" || true)

        if [[ "$HTTP_CODE" != "000" ]]; then
            READY=1
            break
        fi

        sleep 1

    done

    if [[ "$READY" -eq 1 ]]; then
        echo "ready"
    else
        echo "FAILED"
        echo
        echo "ERROR: $NAME did not become reachable."
        exit 1
    fi

done

# ------------------------------------------------------------
# Warm containers in parallel if JSON payload was supplied
# ------------------------------------------------------------

if [[ -n "$JSON_FILE" ]]; then

    echo
    echo "========================================"
    echo "Warming Containers in Parallel"
    echo "========================================"
    echo

    PIDS=()
    STATUS_FILES=()
    RESPONSE_FILES=()

    TMPDIR_WARM=$(mktemp -d /tmp/warm-containers.XXXXXX)

    # --------------------------------------------------------
    # Launch one warm-up request per container in parallel
    # --------------------------------------------------------

    for i in $(seq 1 "$N"); do

        NAME="${BENCHMARK}-iC${i}"
        PORT=$((START_PORT + i - 1))

        STATUS_FILE="${TMPDIR_WARM}/${NAME}.status"
        RESPONSE_FILE="${TMPDIR_WARM}/${NAME}.response"

        STATUS_FILES+=("$STATUS_FILE")
        RESPONSE_FILES+=("$RESPONSE_FILE")

        echo "[$i/$N] Launching warm-up for $NAME on port $PORT..."

        (
            HTTP_CODE=$(curl -s \
                -o "$RESPONSE_FILE" \
                -w "%{http_code}" \
                -X POST \
                "http://127.0.0.1:${PORT}/run" \
                -H "Content-Type: application/json" \
                --data-binary "@${JSON_FILE}" || echo "000")

            echo "$HTTP_CODE" > "$STATUS_FILE"

        ) &

        PIDS+=("$!")

    done

    echo
    echo "Waiting for all warm-up requests to complete..."

    # --------------------------------------------------------
    # Wait for every warm-up request
    # --------------------------------------------------------

    for pid in "${PIDS[@]}"; do
        wait "$pid" || true
    done

    # --------------------------------------------------------
    # Check results
    # --------------------------------------------------------

    echo
    echo "Warm-up Results"
    echo "========================================"

    WARM_FAILED=0

    for i in "${!STATUS_FILES[@]}"; do

        NAME="${BENCHMARK}-iC$((i + 1))"
        STATUS_FILE="${STATUS_FILES[$i]}"
        RESPONSE_FILE="${RESPONSE_FILES[$i]}"

        if [[ -f "$STATUS_FILE" ]]; then
            HTTP_CODE=$(cat "$STATUS_FILE")
        else
            HTTP_CODE="000"
        fi

        if [[ "$HTTP_CODE" == "200" ]]; then
            printf "%-25s SUCCESS (HTTP 200)\n" "$NAME"
        else
            printf "%-25s FAILED (HTTP %s)\n" "$NAME" "$HTTP_CODE"

            echo "Response:"
            if [[ -f "$RESPONSE_FILE" ]]; then
                cat "$RESPONSE_FILE"
                echo
            fi

            WARM_FAILED=1
        fi

    done

    echo "========================================"

    rm -rf "$TMPDIR_WARM"

    if [[ "$WARM_FAILED" -ne 0 ]]; then
        echo
        echo "ERROR: One or more warm-up requests failed."
        exit 1
    fi

fi

# ------------------------------------------------------------
# Final verification
# ------------------------------------------------------------

echo
echo "========================================"
echo "Independent Containers Ready"
echo "========================================"

docker ps \
    --filter "name=^/${BENCHMARK}-iC" \
    --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo
echo "Expected containers: $N"

ACTUAL=$(docker ps -q \
    --filter "name=^/${BENCHMARK}-iC" | wc -l)

echo "Running containers:  $ACTUAL"

if [[ "$ACTUAL" -ne "$N" ]]; then
    echo
    echo "WARNING: Expected $N running containers but found $ACTUAL."
    exit 1
fi

echo
echo "Done."
echo