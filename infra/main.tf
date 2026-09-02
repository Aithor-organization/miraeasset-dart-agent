# 공시 Agent — NCP 평가 서버 인프라
#
# 왜 Terraform인가: 이 공모전은 인스턴스를 만들었다 지웠다 해야 한다.
#   8월 리허설 → 파기 → 운영 구간 재생성 → 종료 후 파기
# 콘솔 클릭으로 VPC·Subnet·ACG·서버·공인IP를 매번 순서대로 만들면 실수가 난다.
# 그리고 `destroy`는 "정지 시 과금이 멈추는가"라는 미확인 항목을 우회한다 —
# 파기하면 확실히 안 나간다.
#
# 🔴 운영 배포(09.06) 이후에는 이 구성을 건드리지 말 것.
#    공인 IP를 재생성하면 주소가 바뀌고, README에 제출한 엔드포인트가 무효가 된다.

terraform {
  required_version = ">= 1.5"

  required_providers {
    ncloud = {
      source  = "NaverCloudPlatform/ncloud"
      version = "~> 3.0"
    }
  }
}

provider "ncloud" {
  access_key  = var.access_key
  secret_key  = var.secret_key
  region      = var.region
  site        = "public"
  support_vpc = true # 공식 문서: VPC 환경만 지원하므로 반드시 true
}

# ── 네트워크 ────────────────────────────────────────────────────────────────

resource "ncloud_vpc" "main" {
  name            = "${var.name_prefix}-vpc"
  ipv4_cidr_block = "10.0.0.0/16"
}

# 공인 IP를 붙이려면 PUBLIC 서브넷이어야 한다 (NCP 제약).
resource "ncloud_subnet" "public" {
  name           = "${var.name_prefix}-subnet"
  vpc_no         = ncloud_vpc.main.vpc_no
  subnet         = cidrsubnet(ncloud_vpc.main.ipv4_cidr_block, 8, 1) # 10.0.1.0/24
  zone           = var.zone
  network_acl_no = ncloud_vpc.main.default_network_acl_no
  subnet_type    = "PUBLIC"
  usage_type     = "GEN"
}

# ── 방화벽 (ACG) ────────────────────────────────────────────────────────────

resource "ncloud_access_control_group" "server" {
  name        = "${var.name_prefix}-acg"
  description = "공시 Agent 평가 API 서버"
  vpc_no      = ncloud_vpc.main.vpc_no
}

resource "ncloud_access_control_group_rule" "server" {
  access_control_group_no = ncloud_access_control_group.server.id

  # 🔴 주최측은 HTTP 80으로 호출한다 (표준 포트, 08-11 공지).
  #    allowed_http_cidr을 좁히면 주최측 발신 IP 대역만 허용할 수 있다 —
  #    해당 대역은 추후 공지 예정이므로 기본값은 전체 개방이다.
  inbound {
    protocol    = "TCP"
    ip_block    = var.allowed_http_cidr
    port_range  = "80"
    description = "평가 API (주최측 GET /answer)"
  }

  # SSH — 내 IP만 열어둔다. 0.0.0.0/0으로 두지 말 것.
  inbound {
    protocol    = "TCP"
    ip_block    = var.admin_cidr
    port_range  = "22"
    description = "관리자 SSH"
  }

  # 아웃바운드 전체 — CLOVA Studio API 호출에 필요하다.
  outbound {
    protocol    = "TCP"
    ip_block    = "0.0.0.0/0"
    port_range  = "1-65535"
    description = "CLOVA API 등 아웃바운드"
  }

  outbound {
    protocol    = "UDP"
    ip_block    = "0.0.0.0/0"
    port_range  = "1-65535"
    description = "DNS 등"
  }
}

# ── 서버 ────────────────────────────────────────────────────────────────────

resource "ncloud_login_key" "main" {
  key_name = "${var.name_prefix}-key"
}

# 🔴 NIC는 **서버 생성 시점에** 붙인다 — `server_instance_no`로 사후 부착하면 안 된다.
#
#   2026-09-02 리허설 실측: 사후 부착 방식은 NCP가 거부한다.
#     returnCode 1002048 — "When assigning additional Network Interface to a server,
#     Internet Gateway-only Subnet is not allowed."
#   서버는 기본 NIC를 달고 생성되므로, 우리 ACG를 적용하려면 NIC를 *먼저* 만들고
#   서버의 `network_interface` 블록에서 order=0으로 지정해 그 NIC로 태어나게 해야 한다.
#
#   ⚠️ `terraform validate`는 이걸 못 잡는다 — 인자 이름·타입이 전부 유효하기 때문이다.
#      apply에서만 드러나는 종류이고, 그래서 리허설이 필요했다.
resource "ncloud_network_interface" "app" {
  name                  = "${var.name_prefix}-nic"
  subnet_no             = ncloud_subnet.public.id
  access_control_groups = [ncloud_access_control_group.server.id]
}

# 🔴 NCP의 기본 접속은 **root 비밀번호**다 (login key는 그 비밀번호를 복호화하는 용도이지
#    SSH 키가 아니다). 2026-09-02 리허설 실측: 생성된 서버에 root/ubuntu/centos 어느 계정으로도
#    publickey 인증이 되지 않았다 — `Permission denied (publickey,password)`.
#    평가 기간에 비밀번호를 손으로 넣어가며 운영할 수는 없으므로, 부팅 시 공개키를 주입해
#    **키 기반 접속을 만든다**. Docker도 여기서 깔아 배포 단계를 하나 줄인다.
#
#    ⚠️ `deploy-key`/`deploy-key.pub`은 .gitignore 대상이다. 없으면 apply가 여기서 멈춘다 —
#       `ssh-keygen -t ed25519 -N "" -f deploy-key` 로 먼저 만들 것.
resource "ncloud_init_script" "bootstrap" {
  name    = "${var.name_prefix}-init"
  os_type = "LNX"

  # 🔴 content는 **ASCII만** 써야 한다. 한글 주석을 넣으면 NCP API가 거부하고,
  #    provider는 원인을 알려주지 않는다 — 포인터 덤프만 나온다:
  #      `Error: Create Vpc Init Script, err params={0x14000... <nil> ...}`
  #    2026-09-02 리허설 실측: 동일 스크립트에서 주석만 한글↔영문으로 바꿔 재현 확인
  #    (한글 ❌ / 영문 ✅). 설명은 이렇게 HCL 주석에 두면 전송되지 않아 안전하다.
  #
  #    스크립트가 하는 일: (1) 공개키 주입 + 키 기반 SSH 활성화
  #    (2) Docker 설치 — 배포판 패키지를 쓴다(원격 스크립트 파이프 실행 회피)
  #    (3) 인덱스 마운트 지점 생성
  content = <<-EOT
    #!/bin/bash
    set -eux

    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    echo '${trimspace(file("${path.module}/deploy-key.pub"))}' >> /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
    sed -i 's/^#\\?PubkeyAuthentication.*/PubkeyAuthentication yes/'     /etc/ssh/sshd_config
    systemctl restart ssh || systemctl restart sshd || true

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y docker.io
    systemctl enable --now docker

    mkdir -p /data/index
  EOT
}

resource "ncloud_server" "app" {
  name           = "${var.name_prefix}-server"
  subnet_no      = ncloud_subnet.public.id
  login_key_name = ncloud_login_key.main.key_name
  init_script_no = ncloud_init_script.bootstrap.id

  server_image_number = var.server_image_number
  server_spec_code    = var.server_spec_code

  network_interface {
    network_interface_no = ncloud_network_interface.app.id
    order                = 0
  }

  description = "공시 Agent 평가 API — 마감 후 결과물 변경 금지"
}

# 🔴 인덱스는 루트 디스크에 들어가지 않는다 — 별도 볼륨이 필요하다.
#    2026-09-02 리허설 실측: 루트 10GB 중 여유 3.6GB뿐인데 인덱스만 3.69GB다.
#    `ncloud_server.base_block_storage_size`는 provider에서 read-only라 키울 수 없다.
#    ⚠️ 붙기만 하고 마운트되지는 않는다 — 서버 생성 후 1회 수동 마운트가 필요하다.
#       절차는 RUNBOOK "인덱스 볼륨 마운트" 참조.
resource "ncloud_block_storage" "data" {
  server_instance_no = ncloud_server.app.id
  name               = "${var.name_prefix}-data"
  size               = var.data_volume_size_gb
  description        = "index volume (dart.sqlite / bm25.pkl / embeddings.sqlite)"

  # 🔴 KVM 계열 스펙(c2-g3)에는 hypervisor_type을 명시해야 한다. 생략하면 NCP가 거부한다:
  #    returnCode 1100000 — "Parameter is invalid. KVM not support getHypervisorCode by computeNo"
  #    (2026-09-02 리허설 실측)
  #    ⚠️ hypervisor_type과 volume_type은 **함께** 지정해야 한다 (provider 검증).
  hypervisor_type = "KVM"
  volume_type     = "CB1" # KVM은 CB1(고성능) 또는 FB1만 허용 — 2026-09-02 실측
  zone            = var.zone
}

resource "ncloud_public_ip" "app" {
  server_instance_no = ncloud_server.app.id
  description        = "${var.name_prefix} 평가 엔드포인트"
}
