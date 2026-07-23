#!/usr/bin/env bash
set -euo pipefail

readonly ADB_TARGET="${KAKAO_ADB_TARGET:-127.0.0.1:5555}"
readonly IRIS_ENDPOINT="${KAKAO_IRIS_ENDPOINT:-http://127.0.0.1:3000}"

printf 'redroid='
sudo docker inspect -f '{{.State.Status}}' redroid 2>/dev/null || echo missing

printf 'adb='
adb -s "$ADB_TARGET" get-state 2>/dev/null || echo unavailable

printf 'iris='
if curl -fsS "$IRIS_ENDPOINT/config" >/dev/null 2>&1; then
  echo ready
else
  echo unavailable
fi
