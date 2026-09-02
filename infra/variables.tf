# 변수 정의
#
# 🔴 access_key / secret_key는 여기 기본값을 넣지 말 것.
#    환경변수로 주입한다 (terraform.tfvars.example 참조).

# 🔴 `null` 기본값은 편의가 아니라 **키를 파일에 안 쓰기 위한 조건**이다.
#    required로 두면 tfvars에 키를 적을 수밖에 없고, 그 파일은 평문이다.
#    null이면 provider가 NCLOUD_ACCESS_KEY / NCLOUD_SECRET_KEY 환경변수로 폴백한다.
#
#    두 경로 다 된다:
#      export NCLOUD_ACCESS_KEY=... NCLOUD_SECRET_KEY=...   (권장 — provider 폴백)
#      export TF_VAR_access_key=... TF_VAR_secret_key=...   (변수 주입)
variable "access_key" {
  description = "NCP 액세스 키. 미지정 시 NCLOUD_ACCESS_KEY 환경변수 사용"
  type        = string
  sensitive   = true
  default     = null
}

variable "secret_key" {
  description = "NCP 시크릿 키. 미지정 시 NCLOUD_SECRET_KEY 환경변수 사용"
  type        = string
  sensitive   = true
  default     = null
}

variable "region" {
  description = "NCP 리전"
  type        = string
  default     = "KR"
}

variable "zone" {
  description = "가용 영역. KR 리전은 KR-1 / KR-2"
  type        = string
  default     = "KR-2"
}

variable "name_prefix" {
  description = "모든 리소스 이름 접두어"
  type        = string
  default     = "dart-agent"

  validation {
    # NCP 리소스 이름은 소문자·숫자·하이픈만 허용한다.
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name_prefix))
    error_message = "소문자로 시작하는 2~21자 소문자/숫자/하이픈이어야 합니다."
  }
}

# ── 서버 사양 ───────────────────────────────────────────────────────────────
#
# 🔴 이 두 값은 리전·시점에 따라 바뀐다. apply 전에 반드시 실제 코드를 조회할 것:
#     terraform console
#     > data.ncloud_server_images.all
#   또는 콘솔 → Server → 서버 생성 화면에서 확인.
#
# 아래 기본값은 **자리표시자다.** 그대로 apply하면 실패한다 —
# 틀린 값으로 조용히 다른 사양이 뜨는 것보다 명시적으로 실패하는 편이 낫다.

variable "server_image_number" {
  description = "서버 이미지 번호 (Ubuntu 22.04 등). 🔴 apply 전 실제 값 확인 필수"
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.server_image_number))
    error_message = <<-EOT
      server_image_number를 지정하세요 (숫자).
      확인: NCP 콘솔 → Server → 서버 생성 → 이미지 선택 화면
      또는: terraform console 에서 data source 조회
    EOT
  }
}

variable "server_spec_code" {
  description = "서버 사양 코드. 권장 High-CPU 2vCPU/4GB. 🔴 apply 전 실제 값 확인 필수"
  type        = string

  validation {
    condition     = length(var.server_spec_code) > 0
    error_message = "server_spec_code를 지정하세요 (예: s2-g3). 콘솔 서버 생성 화면에서 확인."
  }
}

# ── 접근 제어 ───────────────────────────────────────────────────────────────

variable "allowed_http_cidr" {
  description = <<-EOT
    HTTP 80 인바운드 허용 대역.
    기본은 전체 개방 — 주최측 발신 IP 대역이 공지되면 그것으로 좁힐 것.
  EOT
  type        = string
  default     = "0.0.0.0/0"
}

variable "admin_cidr" {
  description = <<-EOT
    SSH 22 허용 대역. 🔴 반드시 본인 IP로 지정할 것.
    확인: curl -s ifconfig.me
    예: "1.2.3.4/32"
  EOT
  type        = string

  validation {
    condition     = var.admin_cidr != "0.0.0.0/0"
    error_message = "SSH를 전체 개방하지 마세요. 본인 IP/32를 지정하세요."
  }
}

# 🔴 데이터 볼륨 (2026-09-02 리허설에서 신설)
#   루트 디스크는 이미지 기본값 10GB로 고정된다 (base_block_storage_size는 read-only).
#   실측: `df -h /` → 9.8G total / 5.7G used / **3.6G avail**.
#   인덱스 3.69GB + 이미지 0.73GB가 들어가지 않으므로 별도 볼륨을 붙인다.
variable "data_volume_size_gb" {
  description = "인덱스용 데이터 볼륨(GB). /data 에 마운트한다"
  type        = number
  default     = 30

  validation {
    condition     = var.data_volume_size_gb >= 10
    error_message = "인덱스 3.7GB + 여유를 담아야 한다. NCP 블록스토리지 최소 단위도 고려할 것."
  }
}
