# 오라클 프리티어 배포

## 1. 인스턴스 만들기
- Compute → Instances → Create instance
- Image: **Ubuntu 24.04** (또는 22.04)
- Shape: **Ampere (VM.Standard.A1.Flex)** 2 OCPU / 12GB 이상 (Always Free 한도: 합계 4 OCPU / 24GB)
  - "Out of capacity"가 뜨면 시간을 두고 다시 시도하거나 다른 가용 도메인(AD)을 고릅니다.
  - AMD Micro(1GB)는 법령 DB + 서버를 같이 돌리기에 메모리가 부족합니다.
- SSH 키: "Save private key"로 개인키(.key)를 받아 둡니다.
- 만든 뒤 **Public IP**를 확인합니다. → 국가법령정보센터 OPEN API 신청 정보에 이 IP를 추가합니다.

## 2. 포트 열기 (두 군데 모두)
1. 오라클 콘솔: 인스턴스 → Subnet → Security List → Add Ingress Rule
   - Source CIDR: **BE 서버 IP/32** (모르면 임시로 0.0.0.0/0), TCP, Destination port **8000**
2. 서버 안 방화벽 (오라클 우분투 이미지는 기본으로 막혀 있음)
   ```bash
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
   sudo netfilter-persistent save
   ```

   Oracle Linux(사용자 `opc`)는 iptables 대신 firewalld를 씁니다.
   ```bash
   sudo firewall-cmd --permanent --add-port=8000/tcp
   sudo firewall-cmd --reload
   sudo firewall-cmd --list-all        # ports: 8000/tcp, services: ssh 확인
   ```

## 3. 접속 · Docker 설치
Ubuntu (사용자 `ubuntu`)
```bash
ssh -i <개인키.key> ubuntu@<Public IP>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && exit   # 다시 접속하면 sudo 없이 docker 사용
```

Oracle Linux (사용자 `opc`)
```bash
ssh -i <개인키.key> opc@<Public IP>
sudo dnf install -y dnf-plugins-core git
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker opc && exit      # 다시 접속하면 sudo 없이 docker 사용
```
아래 명령의 `/home/ubuntu/`는 Oracle Linux에서는 `/home/opc/`입니다.

## 4. 코드 받기 · 설정
```bash
git clone https://github.com/gachi-2-4/chagok_ai_server.git
cd chagok_ai_server
nano .env        # 로컬 .env 내용을 붙여넣기 (깃허브에는 올리지 않음)
```
`.env`에서 서버용으로 바꿀 값
- `BE_BASE_URL`: BE 서버 주소
- `AI_API_KEY`, `BE_INTERNAL_KEY`: 긴 임의 문자열 (BE와 같은 값 공유)
- `LAW_DB_URL`은 docker-compose가 자동으로 넣으므로 그대로 둬도 됩니다.

## 5. 실행
```bash
docker compose up -d --build
curl localhost:8000/health
docker compose exec ai python -m scripts.sync_laws    # 법령 동기화 (처음 1회, 몇 분)
```

## 6. 주 1회 법령 동기화
```bash
crontab -e
# 매주 월요일 04:00
0 4 * * 1 cd /home/ubuntu/chagok_ai_server && docker compose exec -T ai python -m scripts.sync_laws >> sync.log 2>&1
```

## 7. 업데이트
```bash
cd ~/chagok_ai_server && git pull && docker compose up -d --build
```

## BE에 알려줄 것
- AI 서버 주소: `http://<Public IP>:8000`
- 요청 헤더: `X-API-Key: <AI_API_KEY>`
- API 문서: `http://<Public IP>:8000/docs`
