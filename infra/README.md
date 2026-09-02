# NCP 인프라 (Terraform)

공시 Agent 평가 서버를 코드로 만든다. **생성·파기를 반복**하기 위한 것이다.

```
8월 리허설  →  terraform destroy  →  운영 구간 재생성  →  종료 후 파기
```

`destroy`는 "서버를 정지하면 과금이 멈추는가"라는 미확인 항목을 우회한다 —
파기하면 확실히 안 나간다.

---

## 만들어지는 것

| 리소스 | 용도 |
|---|---|
| `ncloud_vpc` | 10.0.0.0/16 |
| `ncloud_subnet` | 10.0.1.0/24 · **PUBLIC** (공인 IP 필수 조건) |
| `ncloud_access_control_group` + `_rule` | 인바운드 80·22 / 아웃바운드 전체 |
| `ncloud_login_key` | SSH 키 |
| `ncloud_server` | 앱 서버 |
| `ncloud_network_interface` | 위 ACG를 서버에 연결 |
| `ncloud_public_ip` | 제출할 엔드포인트 주소 |

---

## 사전 준비

### 1. NCP API 키 발급

콘솔 → **마이페이지 → 인증키 관리 → 신규 API 인증키 생성**
(CLOVA Studio 키와는 별개다)

### 2. 서버 사양 코드 확인 🔴

`server_image_number`와 `server_spec_code`는 **리전·시점에 따라 바뀐다.**
콘솔 → **Server → 서버 생성** 화면에서 실제 값을 확인한다.

목표 사양은 주최측 권장인 **High-CPU 2vCPU / 4GB**다 (`c2-g3`, 2026-09-02 apply로 실재 확인).

🔴 **디스크는 별도다.** 이미지 기본 루트가 **10GB**이고 OS가 5.7GB를 쓴다 — 인덱스 3.7GB가
들어가지 않는다. `ncloud_server.base_block_storage_size`는 provider에서 read-only라 키울 수
없으므로 `ncloud_block_storage`(기본 30GB)를 붙여 `/data`에 마운트한다.

> 값을 비워두면 `terraform plan`이 **실패한다.** 의도된 동작이다 —
> 틀린 값으로 조용히 다른 사양이 뜨는 것보다 명시적으로 막는 편이 낫다.

### 3. 변수 파일

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars     # 사양 코드 + admin_cidr 채우기 (키는 .env → 환경변수)
```

`admin_cidr`은 **본인 IP**로 지정한다. 전체 개방(`0.0.0.0/0`)이면 validation이 막는다.

```bash
curl -s ifconfig.me     # 본인 공인 IP 확인 → "1.2.3.4/32" 형식으로 기입
```

🔴 **공인 IP는 바뀐다.** 2026-09-02 리허설에서 tfvars의 `admin_cidr`이 옛 IP로 남아 있었고,
그대로 배포했으면 3.7GB 인덱스 전송 단계에서 SSH가 막혔다. **apply 직전에 매번 대조할 것.**

### 4. 배포용 SSH 키페어 🔴

```bash
cd infra
ssh-keygen -t ed25519 -N "" -C "dart-agent-deploy" -f deploy-key
```

**NCP의 기본 접속은 root 비밀번호다** — `ncloud_login_key`는 SSH 키가 아니라 그 비밀번호를
복호화하는 용도다. 평가 기간에 비밀번호를 손으로 넣을 수 없으므로, init script가 부팅 시
이 공개키를 주입해 키 기반 접속을 만든다.

⚠️ `deploy-key*`는 `.gitignore` 대상이라 **clone 직후에는 없다.** 만들지 않으면
`terraform validate`부터 `file()` 해석에서 실패한다.

---

## 실행

```bash
set -a && . ../.env && set +a
export NCLOUD_ACCESS_KEY="$NCP_ACCESS_KEY" NCLOUD_SECRET_KEY="$NCP_SECRET_KEY"

terraform init
terraform plan      # 🔴 무엇이 만들어지는지 반드시 눈으로 확인
terraform apply
```

`apply`가 끝나면 `next_steps` 출력에 이어서 할 일이 순서대로 나온다.

### 이어지는 작업

> 🔴 아래는 2026-09-02 리허설에서 **전 단계 실측한 절차**다. 총 15분 내외
> (인덱스 전송 205초 · 이미지 전송 43초 · 서버 생성 3분 35초).

```bash
IP=<공인IP>
SSH="ssh -i deploy-key -o StrictHostKeyChecking=no root@$IP"

# 1) 데이터 볼륨 마운트 (1회) — 루트 디스크 10GB로는 인덱스가 안 들어간다
#    실측: 루트 9.8G 중 여유 3.6G < 인덱스 3.7G. 그래서 30GB 볼륨을 따로 붙인다.
$SSH 'mkfs.ext4 -q -F /dev/vdb && mkdir -p /data \
      && echo "/dev/vdb /data ext4 defaults,nofail 0 2" >> /etc/fstab \
      && mount -a && mkdir -p /data/index && df -h /data'

# 2) 인덱스 전송 — 🔴 3개 파일 전부 (dart.sqlite만 올리면 하이브리드가 죽는다)
scp -i deploy-key index/dart.sqlite index/bm25.pkl index/embeddings.sqlite root@$IP:/data/index/

# 3) 이미지 — 🔴 x86_64로 빌드해야 한다 (맥은 arm64라 그냥 build하면 exec format error)
docker buildx build --platform linux/amd64 -t dart-agent:amd64 --load ..
docker save dart-agent:amd64 | gzip -1 | $SSH 'gunzip | docker load'

# 4) 실행 — 🔴 DART_VECTORS_PATH 필수. 빠뜨리면 앱이 컨테이너 안 /app/index/를 보고
#    못 찾아서 **에러 없이 BM25 단독으로 강등**된다. /ready 의 notes 로 확인할 것.
$SSH "docker run -d --name dart-agent --restart always -p 80:8000 -v /data:/data \
  -e CLOVA_API_KEY=nv-... \
  -e DART_VECTORS_PATH=/data/index/embeddings.sqlite \
  dart-agent:amd64"

# 5) 검증 — ready:true + 하이브리드 ON 확인 후 계약 호출
curl -s  "http://$IP/ready"
curl -sG "http://$IP/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2024년 연결기준 매출액은?"

# 6) 제출 — 둘 다 해야 한다
#    a. README.md §0의 <공인IP> 교체
#    b. Endpoint 제출 구글폼 기입 (공지 2026-09-01, 기한 09.06 23:59)
```

### 평가 기간 운영 — 정지/기동으로 크레딧을 아낀다

2026-09-02 실측: **서버를 정지해도 공인 IP가 유지된다** (IP 리소스가 서버에 계속 부착됨).
`terraform plan`도 정지 상태를 drift로 보지 않는다(`No changes`). 재기동 후에는
`restart always` + fstab 덕에 **SSH 개입 없이 10초 만에 서비스가 복구**된다.

공지 [서버 구성] 6항이 *"불가피한 사유에 따른 서버 재기동은 실격 사유가 아니다"* 라고
명시하므로, 공지된 평가 구간에만 켜두면 된다.

```bash
# 정지 / 기동 — terraform은 전원 상태를 관리하지 않으므로 콘솔 또는 API를 쓴다
# (API 서명 예시는 리허설 스크래치 참조. 콘솔에서 눌러도 동일하다)
```

⚠️ **정지 시 과금이 실제로 멈추는지는 [미확인]**이다. 블록 스토리지 30GB는 정지와
무관하게 과금될 가능성이 높다. 콘솔 → 마이페이지 → 결제 관리에서 확인할 것.

---

## 파기

🔴 **서버를 먼저 정지시킨다.** 곧바로 `destroy`하면 블록 스토리지에서 막힌다 (2026-09-02 실측):

```
returnCode 3001008 — The storage is mounted on the server.
                     Please unmount the storage and try again.
```

terraform은 의존성 순서상 **스토리지를 서버보다 먼저** 지우려 하는데, 마운트 상태에서는
NCP가 거부한다. 문제는 그 시점에 **공인 IP가 이미 제거돼 SSH로 umount할 수 없다**는 것이다.

⚠️ 더 나쁜 것은 이것이 **부분 파기 상태**를 남긴다는 점이다 — 실측에서 공인 IP와 ACG 규칙은
사라졌는데 서버는 살아서 과금이 계속됐다. 여기서 손을 떼면 접근도 안 되는 서버가 돌아간다.
**끝까지 진행할 것.**

```bash
# 1) 서버 정지 (콘솔에서 눌러도 된다). 정지되면 스토리지가 자동으로 풀린다.
#    IP가 없어 SSH가 안 되므로 API/콘솔이 유일한 경로다.
#    server_instance_no는 `terraform output server_instance_no`

# 2) stopped 확인 후
terraform destroy

# 3) 잔존 확인 — 비어 있어야 한다
terraform state list
```

**과금이 즉시 멈춘다.** 리허설이 끝나면 바로 실행할 것.

---

## 🔴 하지 말아야 할 것

| | 이유 |
|---|---|
| **운영 배포 후 `destroy`·`apply`** | 공인 IP가 **바뀐다**. 제출한 엔드포인트가 무효가 되고 평가를 못 받는다 |
| `terraform.tfvars` 커밋 | 실키가 들어간다 (`.gitignore`에 있지만 `-f`로 강제하지 말 것) |
| `.tfstate` 커밋 | 리소스 ID와 일부 민감값이 평문 |
| SSH 22 전체 개방 | validation이 막지만 우회하지 말 것 |

---

## 비용

사용자 확인 기준 **시간당 237원**:

| 가동 | 시간 | 비용 | 크레딧(20만원) 대비 |
|---|---:|---:|---:|
| 리허설 (2시간) | 2 h | 474원 | 0.2% |
| 운영 1주 상시 | 168 h | 39,816원 | 20% |
| 운영 1주 × 09–16시 | 49 h | 11,613원 | **6%** |

운영 기간은 09.07–20 중 **별도 공지되는 구간**이며 최대 1주다.
크레딧은 서버·네트워크·CLOVA가 **하나의 지갑**을 쓴다 — 서버를 오래 켜면 추론 몫이 준다.

---

## 검증 상태

| 항목 | 상태 |
|---|---|
| provider `NaverCloudPlatform/ncloud` | ✅ **3.3.2 설치됨** |
| `terraform fmt` | ✅ 통과 |
| `terraform init` | ✅ 통과 |
| **`terraform validate`** | ✅ **통과 — 전 리소스가 실제 provider 스키마와 대조됨** |
| **`plan` / `apply` 실행** | ✅ **통과 — 2026-09-02 리허설에서 10 리소스 실제 생성** |
| 서버 사양 코드 `c2-g3` | ✅ **실재 확인** — apply 성공 (2vCPU / 3GB 가용 / 루트 10GB) |
| 배포 전 구간 (전송→기동→`/answer`) | ✅ **HTTP 200 실측** — 6.9초, 계약 5필드 |
| 정지 후 공인 IP 유지 | ✅ **실측** — 재기동 후 동일 IP + 10초 자동 복구 |

> `validate`는 리소스 타입·인자 이름·타입을 provider 스키마와 대조한다.
> 즉 **오타나 존재하지 않는 인자는 여기서 잡힌다.**
>
> 잡히지 **않는** 것: 값의 유효성(사양 코드가 실재하는지), 권한, 리전별 가용성.
> 그건 `plan`/`apply`에서만 드러난다.
