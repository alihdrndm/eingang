import pytest

from eingang import config
from eingang.config import DEV_SECRET_KEY, Settings


def test_defaults_match_env_example() -> None:
    settings = Settings(_env_file=None)
    assert settings.DATABASE_URL == "postgres://eingang:eingang@localhost:5452/eingang"
    assert settings.TEMPORAL_ADDRESS == "localhost:7233"
    assert settings.TEMPORAL_NAMESPACE == "eingang"
    assert settings.MAX_UPLOAD_BYTES == 4_194_304
    assert settings.SANDBOX_STORAGE_BUDGET_BYTES == 314_572_800
    assert settings.LLM_ENABLED is False
    assert settings.allowed_hosts == ["localhost", "127.0.0.1"]


def test_production_rejects_the_development_secret_key() -> None:
    with pytest.raises(ValueError, match="DJANGO_SECRET_KEY"):
        Settings(_env_file=None, DJANGO_DEBUG=False, DJANGO_SECRET_KEY=DEV_SECRET_KEY)


def test_s3_storage_requires_credentials() -> None:
    with pytest.raises(ValueError, match="S3_ENDPOINT_URL") as error:
        Settings(_env_file=None, STORAGE_BACKEND="s3")
    assert "S3_SECRET_ACCESS_KEY" in str(error.value)


def test_mailbox_requires_connection_values() -> None:
    with pytest.raises(ValueError, match="MAILBOX_HOST"):
        Settings(_env_file=None, MAILBOX_ENABLED=True)


def test_invalid_configuration_prints_every_problem_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "not-a-number")
    monkeypatch.setenv("STORAGE_BACKEND", "ftp")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exit_info:
            config.get_settings()
    finally:
        config.get_settings.cache_clear()
    assert exit_info.value.code == 1
    printed = capsys.readouterr().err
    assert "MAX_UPLOAD_BYTES" in printed
    assert "STORAGE_BACKEND" in printed


def test_llm_budget_defaults() -> None:
    settings = Settings(_env_file=None)
    assert str(settings.LLM_BUDGET_USD_LIFETIME) == "2.00"
    assert str(settings.LLM_BUDGET_USD_MONTHLY) == "1.50"
    assert str(settings.LLM_BUDGET_USD_DAILY_PUBLIC) == "0.10"
    assert settings.LLM_MAX_CALLS_PER_SANDBOX == 3
    assert str(settings.EVAL_BUDGET_USD) == "0.75"


def test_the_env_example_loads_as_it_is() -> None:
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / ".env.example"
    settings = Settings(_env_file=example)
    assert settings.LLM_ENABLED is False
    assert settings.OPENAI_MODEL == "gpt-5-nano"
    assert str(settings.OPENAI_PRICE_INPUT_PER_MTOK) == "0.05"
    assert str(settings.OPENAI_PRICE_CACHED_INPUT_PER_MTOK) == "0.005"
    assert str(settings.OPENAI_PRICE_OUTPUT_PER_MTOK) == "0.40"
