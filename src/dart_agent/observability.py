"""저의존성 관측·감사 유틸리티. 로그 기록 전에 민감값을 제거한다."""
from __future__ import annotations
import hashlib, logging, re, uuid

_SECRET = re.compile(r"(?i)(?:sk-[a-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|(?:api[_ -]?key|secret|password|access[_ -]?token)\s*[:=]\s*[^\s]+|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
_RRN = re.compile(r"\b\d{6}\s*[-–]\s*\d{7}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\b0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}\b")

def redact(value: object) -> str:
    text = str(value)
    text = _SECRET.sub("[REDACTED_SECRET]", text)
    text = _RRN.sub("[REDACTED_RRN]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    return _PHONE.sub("[REDACTED_PHONE]", text)

def question_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]

def new_trace_id() -> str:
    return uuid.uuid4().hex

def _redact_arg(value: object) -> object:
    """logging의 `%d`/`%f` 포맷을 깨지 않도록 문자열만 마스킹한다."""
    if isinstance(value, (str, BaseException)):
        return redact(value)
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(x) for x in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        elif record.args:
            record.args = _redact_arg(record.args)
        return True
