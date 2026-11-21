#!/usr/bin/env bash
# 새 작은 서버 준비: 스왑 → 코드 받기 → (.env 가 있으면) 설치까지. dnf/apt 를 쓰지 않습니다 (메모리 500MB 서버에서 dnf 가 죽음).
#   curl -sL https://raw.githubusercontent.com/gachi-2-4/chagok_ai_server/main/scripts/bootstrap.sh | bash
# 다시 실행하면 코드만 최신으로 바꾸고 다시 설치합니다 (.env, .venv 는 유지).
set -euxo pipefail

APP=~/chagok_ai_server

# 1. 스왑
SWAP_MB=$(free -m | awk '/Swap:/ {print $2}')
if [ "${SWAP_MB:-0}" -lt 2000 ] && [ ! -f /swapfile2 ]; then
  sudo fallocate -l 2G /swapfile2
  sudo chmod 600 /swapfile2
  sudo mkswap /swapfile2
  sudo swapon /swapfile2
  echo '/swapfile2 none swap sw 0 0' | sudo tee -a /etc/fstab
fi
if systemctl list-unit-files | grep -q '^oracle-cloud-agent'; then
  sudo systemctl disable --now oracle-cloud-agent || true
fi
free -h

# 2. 코드 (git 없이 압축 파일로)
TMP=$(mktemp -d)
curl -sL https://codeload.github.com/gachi-2-4/chagok_ai_server/tar.gz/refs/heads/main | tar xz -C "$TMP"
mkdir -p "$APP"
cp -r "$TMP"/chagok_ai_server-main/. "$APP"/
rm -rf "$TMP"
ls "$APP"

# 3. 설치 (.env 가 있을 때만)
if [ -f "$APP/.env" ]; then
  bash "$APP/scripts/install_server.sh"
else
  echo "BOOTSTRAP_DONE_NEED_ENV: $APP/.env 를 올린 뒤 bash $APP/scripts/install_server.sh 를 실행하세요."
fi
echo BOOTSTRAP_FINISHED
