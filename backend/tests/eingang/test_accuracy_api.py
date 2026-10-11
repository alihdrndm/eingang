"""GET /api/v1/accuracy: the published evals/latest.json, public and read-only."""

import json
from pathlib import Path

import pytest
from django.test import Client, override_settings
from pydantic import BaseModel

from eingang import accuracy, accuracy_api
from eingang.problem import PROBLEM_CONTENT_TYPE
from eingang.serializers import Serializer

URL = "/api/v1/accuracy"

LATEST = {
    "generated_at": "2026-10-09T13:02:11Z",
    "dataset": {"name": "zugferd-corpus-hybrid-cii", "documents": 104, "corpus_commit": "d89145"},
    "systems": [
        {
            "id": "regex-baseline",
            "critical_correct": {"value": 0.4, "ci_low": 0.31, "ci_high": 0.5},
            "fields": {"invoice_number": {"accuracy": 0.6, "abstention": 0.2}},
            "cost_usd": "0",
            "usd_per_doc": "0",
            "latency_p50_ms": 2.0,
            "latency_p95_ms": 5.0,
        },
        {
            "id": "llm:gpt-x",
            "model": "gpt-x",
            "prompt_version": "extract_invoice.v1",
            "critical_correct": {"value": 0.9, "ci_low": 0.84, "ci_high": 0.95},
            "fields": {
                "invoice_number": {"accuracy": 0.97, "abstention": 0.0},
                "due_date": {"accuracy": 0.9, "hallucination": 0.1, "abstention": 0.05},
            },
            "cost_usd": "0.123456",
            "usd_per_doc": "0.001187",
            "latency_p50_ms": 1830.4,
            "latency_p95_ms": 4210.0,
        },
    ],
    "parity": {"files": 180, "excluded": 12, "verdict_agreement": 1.0, "rule_set_agreement": 1.0},
}


@pytest.fixture
def latest_file(tmp_path: Path) -> Path:
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(LATEST), encoding="utf-8")
    return path


def test_serves_latest_json_without_a_session(latest_file: Path) -> None:
    with override_settings(EVALS_LATEST=latest_file):
        response = Client().get(URL)
    assert response.status_code == 200
    assert response.json() == LATEST


def test_get_is_not_csrf_protected_and_ignores_cookies(latest_file: Path) -> None:
    client = Client(enforce_csrf_checks=True)
    client.cookies["sessionid"] = "not-a-session"
    with override_settings(EVALS_LATEST=latest_file):
        response = client.get(URL)
    assert response.status_code == 200


def test_is_read_only(latest_file: Path) -> None:
    with override_settings(EVALS_LATEST=latest_file):
        response = Client(enforce_csrf_checks=True).post(URL, {}, content_type="application/json")
    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"


def test_404_not_available_before_results_are_published(tmp_path: Path) -> None:
    with override_settings(EVALS_LATEST=tmp_path / "missing.json"):
        response = Client().get(URL)
    assert response.status_code == 404
    assert response["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["code"] == "NOT_AVAILABLE"


def test_an_invalid_file_is_an_internal_error(tmp_path: Path) -> None:
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({**LATEST, "systems": "none"}), encoding="utf-8")
    with override_settings(EVALS_LATEST=path):
        response = Client(raise_request_exception=False).get(URL)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL"


def test_the_setting_points_at_the_committed_location() -> None:
    from django.conf import settings

    repo = Path(__file__).resolve().parents[3]
    assert repo / "evals" / "latest.json" == settings.EVALS_LATEST


def test_the_committed_report_has_both_systems_and_parity() -> None:
    from django.conf import settings

    report = accuracy.LatestReport.model_validate_json(Path(settings.EVALS_LATEST).read_bytes())
    ids = {system.id for system in report.systems}
    assert "regex-baseline" in ids
    assert any(system_id.startswith("llm:") for system_id in ids)
    assert report.parity is not None
    assert report.dataset.documents == 104


SCHEMA_PAIRS: list[tuple[type[Serializer], type[BaseModel]]] = [
    (accuracy_api.AccuracyReportSerializer, accuracy.LatestReport),
    (accuracy_api.AccuracyDatasetSerializer, accuracy.Dataset),
    (accuracy_api.SystemResultSerializer, accuracy.SystemResult),
    (accuracy_api.CriticalCorrectSerializer, accuracy.CriticalCorrect),
    (accuracy_api.FieldScoreSerializer, accuracy.FieldScore),
    (accuracy_api.ParitySerializer, accuracy.Parity),
]


@pytest.mark.parametrize(("serializer", "model"), SCHEMA_PAIRS)
def test_openapi_serializers_match_the_models(
    serializer: type[Serializer], model: type[BaseModel]
) -> None:
    declared = serializer().fields
    assert set(declared) == set(model.model_fields)
    for name, field in model.model_fields.items():
        assert declared[name].required == field.is_required(), name
