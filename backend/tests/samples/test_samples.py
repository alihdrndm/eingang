"""The committed sandbox samples match the sandbox table (HANDOFF section 12)."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest

from einvoice.detect import detect, format_label
from einvoice.parse_cii import parse_cii
from einvoice.parse_ubl import parse_ubl
from einvoice.validate import validate, warm_up

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"
MANIFEST: dict[str, Any] = json.loads(  # boundary: json
    (SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8")
)
BY_ID = {entry["id"]: entry for entry in MANIFEST["samples"]}

# The sandbox table (S08 without C13: the precomputed extraction graded every field high).
TABLE = {
    "S01": ("XRechnung · UBL", ["C04"], "awaiting_approval", "valid"),
    "S02": ("XRechnung · CII", [], "awaiting_approval", "valid"),
    "S03": ("ZUGFeRD · EN 16931", [], "awaiting_approval", "valid"),
    "S04": ("ZUGFeRD · BASIC WL", ["C04", "C11"], "needs_review", "not_applicable"),
    "S05": ("XRechnung · UBL", ["C15"], "needs_review", "invalid"),
    "S06": ("XRechnung · UBL", ["C02"], "needs_review", "valid"),
    "S07": ("XRechnung · CII", ["C05"], "needs_review", "valid"),
    "S08": ("Plain PDF", ["C04", "C11"], "needs_review", "not_applicable"),
    "S09": ("Scanned PDF", ["C10", "C11"], "needs_review", "not_applicable"),
    "S10": ("ZUGFeRD · EN 16931", ["C04", "C09"], "needs_review", "valid"),
    "S11": ("XRechnung · UBL · credit note", ["C04"], "approved", "valid"),
    "S12": ("XRechnung · CII", ["C04", "C08"], "rejected", "valid"),
}
RECEIVED_OFFSETS = {
    "S01": 2550, "S02": 62, "S03": 980, "S04": 1318, "S05": 1425, "S06": 48,
    "S07": 2815, "S08": 3796, "S09": 4290, "S10": 5508, "S11": 10085, "S12": 34480,
}  # fmt: skip


def sample_bytes(sample_id: str) -> bytes:
    filename: str = BY_ID[sample_id]["file"]
    return (SAMPLES_DIR / filename).read_bytes()


def test_manifest_lists_the_twelve_samples_and_the_buyer() -> None:
    assert list(BY_ID) == list(TABLE)
    assert MANIFEST["buyer"]["name"] == "Holzwerk Brandt GmbH"
    assert MANIFEST["buyer"]["vat_id"].startswith("DE")


@pytest.mark.parametrize("sample_id", list(TABLE))
def test_sample_files_match_their_manifest_hash(sample_id: str) -> None:
    digest = hashlib.sha256(sample_bytes(sample_id)).hexdigest()
    assert digest == BY_ID[sample_id]["sha256"]


@pytest.mark.parametrize("sample_id", list(TABLE))
def test_sample_manifest_matches_the_sandbox_table(sample_id: str) -> None:
    label, checks, status, _validation = TABLE[sample_id]
    entry = BY_ID[sample_id]
    assert (entry["format_label"], entry["expected_checks"], entry["expected_status"]) == (
        label,
        checks,
        status,
    )
    assert entry["received_offset_minutes"] == RECEIVED_OFFSETS[sample_id]


@pytest.mark.parametrize("sample_id", list(TABLE))
def test_sample_detects_and_validates_as_the_table_says(sample_id: str) -> None:
    label, _checks, _status, expected_validation = TABLE[sample_id]
    detection = detect(sample_bytes(sample_id), BY_ID[sample_id]["file"])
    invoice = None
    if detection.xml is not None and detection.syntax is not None:
        parse = parse_ubl if detection.syntax.value == "ubl" else parse_cii
        invoice = parse(detection.xml)
    assert format_label(detection, invoice.type_code if invoice else None) == label
    report = validate(detection)
    assert report.status == expected_validation
    precomputed = json.loads(
        (SAMPLES_DIR / "precomputed" / f"{sample_id}.json").read_text(encoding="utf-8")
    )
    assert precomputed["validation"]["status"] == report.status
    assert precomputed["detection"]["format_label"] == label


def test_BR_DE_15_sample_S05_fails_with_exactly_that_rule() -> None:
    report = validate(detect(sample_bytes("S05"), "S05.xml"))
    assert [issue.rule_id for issue in report.issues if issue.severity == "fatal"] == ["BR-DE-15"]


def test_sample_S10_prints_a_different_gross_than_its_xml() -> None:
    precomputed = json.loads((SAMPLES_DIR / "precomputed" / "S10.json").read_text("utf-8"))
    assert precomputed["invoice"]["gross_total"] == "1200.00"
    assert "Brutto: 1.190,00 EUR" in precomputed["text"]


def test_sample_decisions_for_S11_and_S12() -> None:
    assert BY_ID["S11"]["decision"]["decision"] == "approved"
    assert BY_ID["S12"]["decision"] == {
        "decision": "rejected",
        "by": "Jonas Brandt (sample)",
        "comment": "Wrong cost centre, please ask for a corrected invoice.",
    }


def test_sample_S01_validates_in_under_two_seconds_after_warm_up(
    capsys: pytest.CaptureFixture[str],
) -> None:
    warm_up()
    detection = detect(sample_bytes("S01"), "S01.xml")
    started = time.perf_counter()
    report = validate(detection)
    seconds = time.perf_counter() - started
    assert report.status == "valid"
    assert seconds < 2.0
    with capsys.disabled():
        print(f"\nS01 validation after warm-up: {seconds * 1000:.0f} ms")
