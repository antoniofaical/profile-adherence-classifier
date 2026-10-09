import copy

import pytest
from helpers import FakeJev, csv_rows, make_batch, profile, tree_hashes

from profile_adherence_classifier.application.batch import (
    execute_batch,
    finish_batch,
    prepare_batch,
)
from profile_adherence_classifier.application.inputs import (
    identity_key,
    import_legacy,
    load_batch,
)
from profile_adherence_classifier.storage import (
    RunStore,
    read_json,
    read_jsonl,
    sha256_file,
    write_json,
)


def prepare(tmp_path, batch=None, **kwargs):
    batch = batch or make_batch(tmp_path / "input")
    return prepare_batch(
        load_batch(batch),
        work_dir=tmp_path / "work",
        output=tmp_path / "scores.csv",
        profile=kwargs.pop("profile", profile()),
        model=kwargs.pop("model", "fixture"),
        chunk_chars=10,
        **kwargs,
    )


def run(prepared, tmp_path, fake=None):
    fake = fake or FakeJev()
    execute_batch(prepared, client_factory=lambda: fake, workers=2)
    code = finish_batch(prepared, tmp_path / "scores.csv")
    return code, csv_rows(tmp_path / "scores.csv"), fake


def test_complete_csv_contains_failures_empty_content_and_partial_scores(tmp_path):
    batch = make_batch(
        tmp_path / "input",
        [
            ("B", ["B" * 15], False),
            ("A", ["A" * 22, "X" * 12], True),
            ("Empty", [" "], False),
            ("Failed", None, False),
        ],
    )
    before = tree_hashes(tmp_path / "input")
    prepared = prepare(tmp_path, batch)
    code, rows, fake = run(prepared, tmp_path)
    assert code == 1
    assert [r["site_name"] for r in rows] == ["A", "B", "Empty", "Failed"]
    assert [r["fit_score"] for r in rows] == ["80.0", "30.0", "", ""]
    assert rows[0]["evidence_is_partial"] == "True"
    assert rows[2]["failed_stage"] == "evidence_plan"
    assert rows[3]["failed_stage"] == "crawl"
    assert len(fake.calls) == prepared.requests
    assert tree_hashes(tmp_path / "input") == before
    assert read_json(prepared.directory / "batch.json")["sites_scored"] == 2


def test_provider_failure_retains_previous_run_and_exports_current_failure(tmp_path):
    first = prepare(tmp_path)
    run(first, tmp_path)
    company = tmp_path / "work/companies" / identity_key(first.content.sites[0])
    old = RunStore(company, profile()).current()[2]
    second = prepare(tmp_path, tmp_path / "input/batch.json")
    fake = FakeJev(fail_subject="A", fail_at=2)
    code, rows, _ = run(second, tmp_path, fake)
    assert code == 1
    assert rows[1]["site_name"] == "A" and rows[1]["fit_score"] == ""
    assert rows[1]["failed_stage"] == "jev"
    assert RunStore(company, profile()).current()[2] == old
    failed = [
        p
        for p in (RunStore(company, profile()).root / "runs").iterdir()
        if read_json(p / "run.json")["status"] == "failed"
    ]
    assert len(failed) == 1
    assert len(read_jsonl(failed[0] / "responses.jsonl")) == 1
    assert not (failed[0] / "result.json").exists()


def test_reuse_requires_same_content_model_profile_and_selection(tmp_path):
    original = prepare(tmp_path)
    run(original, tmp_path)
    reused = prepare(tmp_path, tmp_path / "input/batch.json", only_missing=True)
    assert reused.requests == 0 and len(reused.results) == 2
    code, rows, fake = run(reused, tmp_path)
    assert code == 0 and not fake.calls
    assert {r["status"] for r in rows} == {"reused"}
    assert (
        prepare(
            tmp_path, tmp_path / "input/batch.json", only_missing=True, model="new"
        ).requests
        > 0
    )
    changed_profile = copy.deepcopy(profile())
    changed_profile["score_aggregation"]["bottleneck_weight"] = 0.9
    assert (
        prepare(
            tmp_path,
            tmp_path / "input/batch.json",
            only_missing=True,
            profile=changed_profile,
        ).requests
        > 0
    )
    assert (
        prepare(
            tmp_path, tmp_path / "input/batch.json", only_missing=True, max_chunks=1
        ).requests
        > 0
    )
    evidence = tmp_path / "input/company0/evidence.jsonl"
    evidence.write_text(
        evidence.read_text().replace("A" * 25, "Z" * 25), encoding="utf-8"
    )
    index = read_json(tmp_path / "input/batch.json")
    index["sites"][0]["evidence_sha256"] = sha256_file(evidence)
    write_json(tmp_path / "input/batch.json", index)
    refreshed = prepare(tmp_path, tmp_path / "input/batch.json", only_missing=True)
    assert list(refreshed.plans) == ["A"] and list(refreshed.results) == ["B"]


def test_coverage_change_invalidates_reuse(tmp_path):
    first = prepare(tmp_path)
    run(first, tmp_path)
    index = read_json(tmp_path / "input/batch.json")
    manifest_path = tmp_path / "input/company0/manifest.json"
    manifest = read_json(manifest_path)
    manifest["page_errors"] = [{"error": "timeout"}]
    write_json(manifest_path, manifest)
    index["sites"][0].update(
        manifest_sha256=sha256_file(manifest_path), evidence_is_partial=True
    )
    write_json(tmp_path / "input/batch.json", index)
    next_run = prepare(tmp_path, tmp_path / "input/batch.json", only_missing=True)
    assert list(next_run.plans) == ["A"]


def test_offline_rescore_uses_no_client_and_preserves_original(tmp_path):
    first = prepare(tmp_path)
    run(first, tmp_path)
    p = copy.deepcopy(profile())
    p["score_aggregation"]["bottleneck_weight"] = 0.1
    second = prepare(tmp_path, tmp_path / "input/batch.json", mode="score", profile=p)
    assert second.state["jev_requests_planned"] == 0
    execute_batch(second, workers=2)
    assert finish_batch(second, tmp_path / "scores.csv") == 0
    assert {r["status"] for r in csv_rows(tmp_path / "scores.csv")} == {"rescored"}
    company = tmp_path / "work/companies" / identity_key(first.content.sites[0])
    assert RunStore(company, profile()).current()[2] == first.results["A"]


def test_missing_offline_results_are_rows_not_live_requests(tmp_path):
    prepared = prepare(tmp_path, mode="score")
    execute_batch(prepared)
    assert finish_batch(prepared, tmp_path / "scores.csv") == 1
    assert all(r["fit_score"] == "" for r in csv_rows(tmp_path / "scores.csv"))


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(evidence_path="../../outside.jsonl"),
        lambda s: s.update(evidence_path="C:/outside.jsonl"),
        lambda s: s.update(evidence_path="/outside.jsonl"),
        lambda s: s.update(evidence_sha256="0" * 64),
        lambda s: s.update(manifest_sha256="0" * 64),
        lambda s: s.update(pages_saved=100),
        lambda s: s.update(has_text=False),
        lambda s: s.update(evidence_is_partial=True),
    ],
)
def test_bad_company_evidence_isolated_before_provider_calls(tmp_path, change):
    path = make_batch(tmp_path / "input")
    index = read_json(path)
    change(index["sites"][0])
    write_json(path, index)
    prepared = prepare(tmp_path, path)
    code, rows, fake = run(prepared, tmp_path)
    assert code == 1 and len(rows) == 2
    assert all(call[0] == "B" for call in fake.calls)


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b.update(schema_version=2),
        lambda b: b.update(schema_version=True),
        lambda b: b.update(status="running"),
        lambda b: b.update(sites_selected=99),
        lambda b: b["sites"][0].update(status="pending"),
        lambda b: b["sites"][1].update(name="a"),
    ],
)
def test_invalid_batch_rejected_before_preparation(tmp_path, change):
    path = make_batch(tmp_path / "input")
    index = read_json(path)
    change(index)
    write_json(path, index)
    with pytest.raises(ValueError):
        load_batch(path)
    assert not (tmp_path / "work").exists()


def test_input_changes_after_planning_do_not_change_frozen_chunks(tmp_path):
    prepared = prepare(tmp_path)
    (tmp_path / "input/company0/evidence.jsonl").write_text("corrupted after planning")
    _, _, fake = run(prepared, tmp_path)
    assert all(
        "corrupted" not in p["text"] for _, pages, _ in fake.calls for p in pages
    )


def test_sampling_explicitly_marks_partial_and_ties_sort_by_name(tmp_path):
    path = make_batch(
        tmp_path / "input",
        [("A Zebra", ["A" * 25], False), ("A Alpha", ["B" * 25], False)],
    )
    prepared = prepare(tmp_path, path, percentage=50)
    _, rows, _ = run(prepared, tmp_path)
    assert [r["site_name"] for r in rows] == ["A Alpha", "A Zebra"]
    assert all(r["evidence_is_partial"] == "True" for r in rows)
    assert all(r["evidence_chunks_sent"] == "2" for r in rows)


def test_legacy_import_records_index_only_in_classifier_area(tmp_path):
    make_batch(tmp_path / "input")
    (tmp_path / "input/batch.json").unlink()
    before = tree_hashes(tmp_path / "input")
    content = import_legacy(tmp_path / "input")
    prepared = prepare_batch(
        content,
        work_dir=tmp_path / "work",
        output=tmp_path / "scores.csv",
        profile=profile(),
        model="fixture",
    )
    code, _, _ = run(prepared, tmp_path)
    assert code == 0 and prepared.state["legacy_import"]
    assert tree_hashes(tmp_path / "input") == before
    assert (prepared.directory / "content-index.json").is_file()


def test_output_cannot_touch_input_or_overlap_own_work(tmp_path):
    path = make_batch(tmp_path / "input")
    for work, output in [
        (tmp_path / "input/work", tmp_path / "scores.csv"),
        (tmp_path / "work", tmp_path / "input/scores.csv"),
        (tmp_path / "work", tmp_path / "work/scores.csv"),
    ]:
        with pytest.raises(ValueError):
            prepare_batch(
                load_batch(path),
                work_dir=work,
                output=output,
                profile=profile(),
                model="fixture",
            )
