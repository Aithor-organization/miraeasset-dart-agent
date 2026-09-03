import logging

from dart_agent.observability import RedactingFilter, redact, question_id


def test_redact_removes_sensitive_values():
    raw = "api_key=abcdefgh12345678 010-1234-5678 a@b.com 900101-1234567"
    out = redact(raw)
    assert all(x not in out for x in ("abcdefgh12345678", "010-1234-5678", "a@b.com", "900101-1234567"))


def test_question_id_is_stable_and_non_reversible_label():
    assert question_id("매출액") == question_id("매출액")
    assert len(question_id("매출액")) == 16


def test_filter_preserves_numeric_logging_arguments():
    record = logging.LogRecord("t", logging.WARNING, __file__, 1, "timeout %.1fs", (0.3,), None)
    assert RedactingFilter().filter(record)
    assert record.getMessage() == "timeout 0.3s"


def test_filter_redacts_exception_argument():
    record = logging.LogRecord(
        "t", logging.ERROR, __file__, 1, "failed: %s",
        (RuntimeError("api_key=abcdefgh12345678"),), None,
    )
    RedactingFilter().filter(record)
    assert "abcdefgh12345678" not in record.getMessage()
