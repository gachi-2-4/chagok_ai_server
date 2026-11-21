#!/usr/bin/env bash
# 작은 서버(메모리 1GB 이하)용 설치: Docker·법령 DB 없이 Python으로 바로 실행합니다.
# Oracle Linux 9 / Ubuntu 22.04+ 에서 저장소 폴더 안에서 실행:  bash scripts/install_server.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(whoami)"
cd "$APP_DIR"

echo "== 1. 스왑 확인 (메모리가 작으면 설치 중 멈추지 않게) =="
SWAP_MB=$(free -m | awk '/Swap:/ {print $2}')
if [ "${SWAP_MB:-0}" -lt 2000 ] && [ ! -f /swapfile2 ]; then
  sudo fallocate -l 2G /swapfile2
  sudo chmod 600 /swapfile2
  sudo mkswap /swapfile2 >/dev/null
  sudo swapon /swapfile2
  echo '/swapfile2 none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
free -h

echo "== 2. 메모리 많이 쓰는 기본 에이전트 끄기 (오라클) =="
if systemctl list-unit-files | grep -q '^oracle-cloud-agent'; then
  sudo systemctl disable --now oracle-cloud-agent || true
fi

echo "== 3. Python 3.11+ 설치 =="
if command -v dnf >/dev/null; then
  sudo dnf install -y --setopt=install_weak_deps=False python3.11 python3.11-pip
  PY=python3.11
else
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip
  PY=python3
fi
$PY --version

echo "== 4. 가상환경 + 패키지 =="
[ -d .venv ] || $PY -m venv .venv
.venv/bin/pip install --no-cache-dir -q --upgrade pip
.venv/bin/pip install --no-cache-dir -q -r requirements.txt

echo "== 5. 확인 =="
[ -f .env ] || { echo "!! .env 파일이 없어요. 먼저 .env 를 올리세요."; exit 1; }
[ -f data/law_index.npz ] || echo "!! data/law_index.npz 가 없어요. 법령 검색 없이 규칙 엔진만 동작합니다."

echo "== 6. systemd 서비스 등록 =="
sudo tee /etc/systemd/system/chagok-ai.service >/dev/null <<EOF
[Unit]
Description=chagok AI server
After=network-online.target
Wants=network-online.target

[Service]
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
# 작은 서버에서는 법령 DB 대신 data/law_index.npz 파일로 검색 (.env 의 LAW_DB_URL 무시)
Environment=LAW_DB_URL=
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now chagok-ai
sudo systemctl restart chagok-ai

echo "== 7. 방화벽 8000번 =="
if systemctl is-active --quiet firewalld; then
  sudo firewall-cmd --permanent --add-port=8000/tcp >/dev/null && sudo firewall-cmd --reload >/dev/null
fi

sleep 5
curl -s localhost:8000/health && echo
echo "완료. 로그 보기: sudo journalctl -u chagok-ai -f"
