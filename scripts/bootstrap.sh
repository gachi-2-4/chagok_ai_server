#!/usr/bin/env bash
# 새 작은 서버 준비: 스왑 → git → 코드 받기 → (.env 가 있으면) 설치까지.
#   curl -sL https://raw.githubusercontent.com/gachi-2-4/chagok_ai_server/main/scripts/bootstrap.sh | bash
# 진행 상황은 ~/bootstrap.log 에 남습니다.
set -euxo pipefail

# 1. 스왑 (메모리 1GB 이하에서 dnf/pip 가 멈추지 않게)
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

# 2. git
if ! command -v git >/dev/null; then
  if command -v dnf >/dev/null; then
    sudo dnf install -y --setopt=install_weak_deps=False git
  else
    sudo apt-get update && sudo apt-get install -y git
  fi
fi

# 3. 코드
if [ -d ~/chagok_ai_server/.git ]; then
  git -C ~/chagok_ai_server pull
else
  git clone https://github.com/gachi-2-4/chagok_ai_server.git ~/chagok_ai_server
fi

# 4. 설치 (.env 가 있을 때만)
if [ -f ~/chagok_ai_server/.env ]; then
  bash ~/chagok_ai_server/scripts/install_server.sh
else
  echo "BOOTSTRAP_DONE_NEED_ENV: ~/chagok_ai_server/.env 를 올린 뒤 bash ~/chagok_ai_server/scripts/install_server.sh 를 실행하세요."
fi
echo BOOTSTRAP_FINISHED
