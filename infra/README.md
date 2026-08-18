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

목표 사양은 주최측 권장인 **High-CPU 2vCPU / 4GB / 20GB**다.

> 값을 비워두면 `terraform plan`이 **실패한다.** 의도된 동작이다 —
> 틀린 값으로 조용히 다른 사양이 뜨는 것보다 명시적으로 막는 편이 낫다.

### 3. 변수 파일

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars     # 키 + 사양 코드 + admin_cidr 채우기
```

`admin_cidr`은 **본인 IP**로 지정한다. 전체 개방(`0.0.0.0/0`)이면 validation이 막는다.

```bash
curl -s ifconfig.me     # 본인 공인 IP 확인 → "1.2.3.4/32" 형식으로 기입
```

---

## 실행

```bash
terraform init
terraform plan      # 🔴 무엇이 만들어지는지 반드시 눈으로 확인
terraform apply
```

`apply`가 끝나면 `next_steps` 출력에 이어서 할 일이 순서대로 나온다.

### 이어지는 작업

```bash
# 1) 인덱스 전송 (3.9GB)
ssh root@<공인IP> "mkdir -p /data/index"
scp index/dart.sqlite root@<공인IP>:/data/index/

# 2) Docker 실행 — 외부 80 → 컨테이너 8000
docker run -d --name dart-agent --restart always \
  -p 80:8000 -v /data:/data -e CLOVA_API_KEY=nv-... dart-agent

# 3) 계약 검증
curl -G "http://<공인IP>/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2024년 연결기준 매출액은?"

# 4) README.md §0의 <공인IP> 교체  ← 제출 필수
```

---

## 파기

```bash
terraform destroy
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
| **`plan` / `apply` 실행** | ❌ **미실행** — API 키 필요 |
| 서버 사양 코드 | ❌ **미확인** — 콘솔에서 확인 필요 |

> `validate`는 리소스 타입·인자 이름·타입을 provider 스키마와 대조한다.
> 즉 **오타나 존재하지 않는 인자는 여기서 잡힌다.**
>
> 잡히지 **않는** 것: 값의 유효성(사양 코드가 실재하는지), 권한, 리전별 가용성.
> 그건 `plan`/`apply`에서만 드러난다.
