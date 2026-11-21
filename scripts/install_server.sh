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

echo "== 3. Python 3.11 설치 (uv 사용, dnf/apt 없이) =="
UV="$HOME/.local/bin/uv"
if [ ! -x "$UV" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
"$UV" --version

echo "== 4. 가상환경 + 패키지 =="
[ -x .venv/bin/python ] || "$UV" venv --python 3.11 .venv
UV_NO_CACHE=1 "$UV" pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python --version

# Oracle Linux 등 SELinux 가 켜진 서버: 홈 폴더의 실행 파일을 서비스가 실행할 수 있게 표시
if command -v getenforce >/dev/null && [ "$(getenforce)" = "Enforcing" ]; then
  sudo chcon -R -t bin_t .venv/bin "$("$UV" python dir)"
fi

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
