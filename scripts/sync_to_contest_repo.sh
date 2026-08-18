#!/usr/bin/env bash
# 작업 저장소 → 공식 제출 저장소(dis-029) 동기화.
#
# 🔴 왜 스크립트인가 — 손으로 rsync 했다가 두 번 데였다 (2026-08-18):
#
#   1. `--exclude 'docs/3.공시/corpus/'`를 빠뜨려 **5.2GB 코퍼스가 복사**됐다.
#      주최측이 제공한 원본이라 재배포 대상이 아니고, 디스크만 먹는다.
#   2. `--delete`가 **공식 저장소의 `.gitignore`를 작업 저장소 것으로 덮어썼다.**
#      그 파일이 코퍼스·인덱스·tfvars를 막고 있었기 때문에, 덮어쓰는 순간
#      5.2GB 코퍼스와 실키가 든 tfvars가 **전부 추적 대상이 됐다.**
#
#   2번이 특히 위험하다 — 가드 파일 자체를 동기화가 지우는 구조였다.
#   그래서 아래 KEEP 목록은 **동기화에서 제외**한다 (공식 저장소 쪽이 정본).
#
# 사용: ./scripts/sync_to_contest_repo.sh [--dry-run]

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${CONTEST_REPO:-$SRC/../dis-029}"

[ -d "$DEST/.git" ] || { echo "❌ 공식 저장소가 아님: $DEST" >&2; exit 1; }

DRY=()
[ "${1:-}" = "--dry-run" ] && DRY=(--dry-run --itemize-changes)

rsync -a --delete "${DRY[@]}" \
  --exclude '.git/' \
  `# 🔴 공식 저장소가 정본인 가드 파일 — 덮어쓰면 비밀이 샌다` \
  --exclude '.gitignore' \
  --exclude 'infra/.gitignore' \
  `# 비밀` \
  --exclude '.env' --exclude '*.pem' --exclude '*.key' \
  --exclude 'infra/terraform.tfvars' --exclude 'infra/*.tfstate*' \
  --exclude 'infra/.terraform/' \
  `# 대용량 — 코퍼스는 주최측 제공물, 인덱스는 클라우드 링크로 별도 제출` \
  --exclude 'docs/3.공시/' --exclude '**/corpus/' \
  --exclude 'index' --exclude '*.sqlite*' --exclude '*.pkl' \
  `# 재생성 가능` \
  --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '*.py[cod]' \
  --exclude 'eval/*.json' --exclude '*.log' --exclude '.DS_Store' \
  "$SRC/" "$DEST/"

echo "✅ 동기화 완료: $DEST"

# 🔴 사후 검증 — exclude를 빠뜨려도 여기서 잡힌다 (동기화 성공 ≠ 안전)
cd "$DEST"
fail=0
while read -r path label; do
  [ -e "$path" ] || continue
  if git check-ignore -q "$path"; then continue; fi
  echo "🔴 추적 대상에 노출: $path ($label)" >&2
  fail=1
done <<'EOF'
.env CLOVA/NCP_실키
infra/terraform.tfvars NCP_실키
index 인덱스_3.8GB
docs/3.공시 코퍼스_5.2GB
EOF

size=$(du -sm --exclude=.git . 2>/dev/null | cut -f1 || du -sm . | cut -f1)
if [ "${size:-0}" -gt 500 ]; then
  echo "🔴 저장소가 ${size}MB — 대용량이 섞였을 수 있다" >&2
  du -sh ./* 2>/dev/null | sort -rh | head -5 >&2
  fail=1
fi

[ "$fail" -eq 0 ] && echo "✅ 비밀·대용량 검사 통과" || { echo "❌ 검사 실패 — 커밋 금지" >&2; exit 1; }
