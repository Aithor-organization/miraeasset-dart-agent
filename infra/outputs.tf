# 출력 — apply 후 필요한 값만
#
# 🔴 endpoint_url은 README.md §0에 그대로 붙여넣는다 (제출 필수 항목).

output "public_ip" {
  description = "서버 공인 IP"
  value       = ncloud_public_ip.app.public_ip
}

output "endpoint_url" {
  description = "🔴 제출용 평가 API End-point — README.md §0에 기입할 값"
  value       = "http://${ncloud_public_ip.app.public_ip}/answer"
}

output "ssh_command" {
  description = "SSH 접속 (비밀번호는 아래 명령으로 조회)"
  value       = "ssh root@${ncloud_public_ip.app.public_ip}"
}

output "scp_index_command" {
  description = "인덱스 전송 — 로컬에서 만든 dart.sqlite를 올린다"
  value       = "scp index/dart.sqlite root@${ncloud_public_ip.app.public_ip}:/data/index/"
}

output "server_instance_no" {
  description = "서버 인스턴스 번호 (콘솔 조회·API용)"
  value       = ncloud_server.app.id
}

output "login_key_name" {
  description = <<-EOT
    로그인 키 이름. 최초 비밀번호는 콘솔 또는 API로 조회한다:
      콘솔 → Server → 해당 서버 → 관리자 비밀번호 확인 → <키이름>.pem 업로드
  EOT
  value       = ncloud_login_key.main.key_name
}

output "next_steps" {
  description = "apply 직후 할 일"
  value       = <<-EOT

    ── apply 완료 후 순서 ────────────────────────────────────────────
    1) 관리자 비밀번호 조회 (콘솔 → Server → 관리자 비밀번호 확인)
       ※ ${ncloud_login_key.main.key_name}.pem 파일이 필요하다

    2) 인덱스 디렉터리 생성 + 전송 (3.9GB, 수 분~수십 분)
       ssh root@${ncloud_public_ip.app.public_ip} "mkdir -p /data/index"
       scp index/dart.sqlite root@${ncloud_public_ip.app.public_ip}:/data/index/

    3) Docker 설치 + 이미지 빌드 후 실행 (외부 80 → 컨테이너 8000)
       docker run -d --name dart-agent --restart always \\
         -p 80:8000 -v /data:/data -e CLOVA_API_KEY=nv-... dart-agent

    4) 계약 검증
       curl -G "http://${ncloud_public_ip.app.public_ip}/answer" \\
         --data-urlencode "question_id=Q-001" \\
         --data-urlencode "question=삼성전자의 2024년 연결기준 매출액은?"

    5) 🔴 README.md §0의 <공인IP>를 ${ncloud_public_ip.app.public_ip} 로 교체

    ── 리허설이 끝나면 ──────────────────────────────────────────────
       terraform destroy    # 과금 즉시 중단
       ※ 재생성하면 공인 IP가 바뀐다. 운영 배포 후에는 절대 실행 금지.
  EOT
}
