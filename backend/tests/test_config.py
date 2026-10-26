import pytest
from pydantic import ValidationError

from app.config import DEV_SECRET, Settings


def make(**kw):
    return Settings(_env_file=None, **kw)


def test_production_refuses_the_development_or_a_short_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)  # the test harness sets a good one; here it must be absent
    with pytest.raises(ValidationError):
        make(environment="production")  # falls back to the built-in development key
    with pytest.raises(ValidationError):
        make(environment="production", secret_key="short")
    with pytest.raises(ValidationError):
        make(environment="production", secret_key=DEV_SECRET)
    assert make(environment="production", secret_key="x" * 40).environment == "production"


def test_private_fetching_can_never_be_enabled_in_production():
    with pytest.raises(ValidationError):
        make(environment="production", secret_key="x" * 40, allow_private_fetch=True)
    assert make(environment="development", allow_private_fetch=True).allow_private_fetch is True  # tests/dev only


def test_secure_cookies_default_to_production_only_and_blank_values_are_unset(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert make(environment="production", secret_key="x" * 40).secure_cookies is True
    assert make(environment="development").secure_cookies is False
    assert make(environment="production", secret_key="x" * 40, cookie_secure=False).secure_cookies is False
    s = make(smtp_host="  ", smtp_password="", admin_email="")
    assert s.smtp_host is None and s.smtp_password is None and s.admin_email is None


def test_secret_status_reports_presence_never_values():
    s = make(smtp_password="very-secret-value", anthropic_api_key="sk-ant-xyz-123456")
    st = s.secret_status()
    assert st["SMTP_PASSWORD"] is True and st["ANTHROPIC_API_KEY"] is True and st["TAVILY_API_KEY"] is False
    assert "very-secret-value" not in repr(s) and "sk-ant-xyz-123456" not in repr(s) and "very-secret-value" not in str(s.model_dump())
