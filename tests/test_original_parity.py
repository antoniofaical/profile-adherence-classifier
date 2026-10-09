"""Expected outputs generated from the read-only original revision."""

import json
from pathlib import Path

import pytest

from profile_adherence_classifier.domain.profile import (
    profile_sha256,
    question_set_sha256,
)
from profile_adherence_classifier.domain.scoring import build_fit_result
from profile_adherence_classifier.evidence.chunks import select_evidence_chunks

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/original-parity.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", FIXTURE["scoring"])
def test_same_scores_identities_and_criterion_evidence_as_original(case):
    assert profile_sha256(case["profile"]) == case["profile_sha256"]
    assert question_set_sha256(case["profile"]) == case["question_set_sha256"]
    assert (
        build_fit_result(
            subject="fixture", records=case["records"], profile=case["profile"]
        )
        == case["result"]
    )


@pytest.mark.parametrize("case", FIXTURE["chunking"])
def test_same_chunks_selection_and_page_provenance_as_original(tmp_path, case):
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text(
        "".join(json.dumps(r) + "\n" for r in case["records"]), encoding="utf-8"
    )
    chunks, numbers, strategy, total = select_evidence_chunks(
        evidence, case["chunk_chars"], **case["sampling"]
    )
    assert (chunks, numbers, strategy, total) == (
        case["chunks"],
        case["numbers"],
        case["strategy"],
        case["total"],
    )
