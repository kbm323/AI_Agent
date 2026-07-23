#!/usr/bin/env bash
set -euo pipefail

readonly IRIS_ROOT="${IRIS_ROOT:-/home/ubuntu/iris}"
readonly ADB_TARGET="${KAKAO_ADB_TARGET:-127.0.0.1:5555}"
readonly IRIS_ENDPOINT="${KAKAO_IRIS_ENDPOINT:-http://127.0.0.1:3000}"

sudo modprobe binder_linux devices=binder,hwbinder,vndbinder
sudo install -d -m 0755 /dev/binderfs
if ! mountpoint -q /dev/binderfs; then
  sudo mount -t binder binder /dev/binderfs
fi
sudo ln -sfn /dev/binderfs/binder /dev/binder
sudo ln -sfn /dev/binderfs/hwbinder /dev/hwbinder
sudo ln -sfn /dev/binderfs/vndbinder /dev/vndbinder

if ! sudo docker inspect redroid >/dev/null 2>&1; then
  echo "redroid container is not installed" >&2
  exit 1
fi
sudo docker start redroid >/dev/null

for _ in $(seq 1 30); do
  if adb connect "$ADB_TARGET" >/dev/null 2>&1 &&
    adb -s "$ADB_TARGET" get-state 2>/dev/null | grep -qx device; then
    break
  fi
  sleep 1
done
adb -s "$ADB_TARGET" get-state | grep -qx device
adb -s "$ADB_TARGET" root >/dev/null
sleep 1
adb connect "$ADB_TARGET" >/dev/null

for _ in $(seq 1 60); do
  if adb -s "$ADB_TARGET" shell getprop sys.boot_completed 2>/dev/null |
    tr -d '\r' | grep -qx 1; then
    break
  fi
  sleep 1
done
adb -s "$ADB_TARGET" shell getprop sys.boot_completed |
  tr -d '\r' | grep -qx 1

if [[ ! -f "$IRIS_ROOT/Iris.apk" ]]; then
  echo "Iris.apk is missing from $IRIS_ROOT" >&2
  exit 1
fi

if ! pgrep -f "party.qwer.iris.Main" >/dev/null; then
  nohup adb -s "$ADB_TARGET" shell \
    CLASSPATH=/data/local/tmp/Iris.apk \
    app_process / party.qwer.iris.Main \
    >"$IRIS_ROOT/iris-runtime.log" 2>&1 </dev/null &
fi

for _ in $(seq 1 20); do
  if curl -fsS "$IRIS_ENDPOINT/config" >/dev/null; then
    echo "kakaotalk readonly stack is ready"
    exit 0
  fi
  sleep 1
done

echo "Iris did not become ready" >&2
exit 1
