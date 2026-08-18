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

resource "ncloud_server" "app" {
  name           = "${var.name_prefix}-server"
  subnet_no      = ncloud_subnet.public.id
  login_key_name = ncloud_login_key.main.key_name

  server_image_number = var.server_image_number
  server_spec_code    = var.server_spec_code

  description = "공시 Agent 평가 API — 마감 후 결과물 변경 금지"
}

# ACG는 서버 생성 후 네트워크 인터페이스에 붙는다.
# 기본 ACG가 자동 적용되므로, 위에서 만든 ACG를 명시적으로 연결한다.
resource "ncloud_network_interface" "app" {
  name                  = "${var.name_prefix}-nic"
  subnet_no             = ncloud_subnet.public.id
  access_control_groups = [ncloud_access_control_group.server.id]
  server_instance_no    = ncloud_server.app.id
}

resource "ncloud_public_ip" "app" {
  server_instance_no = ncloud_server.app.id
  description        = "${var.name_prefix} 평가 엔드포인트"
}
