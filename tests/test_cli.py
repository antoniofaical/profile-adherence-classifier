import pytest
from helpers import FakeJev, csv_rows, make_batch

from profile_adherence_classifier import cli
from profile_adherence_classifier.storage import read_json


def args(tmp_path, *extra):
    return [
        "--evidence-batch",
        str(tmp_path / "input/batch.json"),
        "--output",
        str(tmp_path / "scores.csv"),
        "--work-dir",
        str(tmp_path / "work"),
        "--chunk-chars",
        "10",
        *extra,
    ]


def factory(monkeypatch, fake):
    monkeypatch.setattr(cli, "JevClient", lambda *a, **kw: fake)
    monkeypatch.setenv(cli.JEV_API_KEY_ENV, "fixture")


def test_declined_confirmation_makes_zero_calls_and_csv_marks_cancelled(
    tmp_path, monkeypatch
):
    make_batch(tmp_path / "input")
    fake = FakeJev()
    factory(monkeypatch, fake)
    monkeypatch.setattr(cli, "approve", lambda: False)
    assert cli.main(args(tmp_path)) == 1
    assert not fake.calls
    assert {r["status"] for r in csv_rows(tmp_path / "scores.csv")} == {"cancelled"}


def test_explicit_authorization_processes_all_then_reuses_without_prompt(
    tmp_path, monkeypatch
):
    make_batch(tmp_path / "input")
    fake = FakeJev()
    factory(monkeypatch, fake)
    monkeypatch.setattr(cli, "approve", lambda: pytest.fail("No prompt expected"))
    assert cli.main(args(tmp_path, "--yes", "--workers", "2")) == 0
    assert len(fake.calls) == 6
    fake.calls.clear()
    assert cli.main(args(tmp_path, "--only-missing")) == 0
    assert not fake.calls


def test_confirmation_is_after_evidence_validation_and_plan(tmp_path, monkeypatch):
    make_batch(tmp_path / "input")
    fake = FakeJev()
    factory(monkeypatch, fake)

    def approve():
        assert not fake.calls
        batches = list((tmp_path / "work/batches").iterdir())
        assert read_json(batches[0] / "batch.json")["jev_requests_planned"] == 6
        return True

    monkeypatch.setattr(cli, "approve", approve)
    assert cli.main(args(tmp_path)) == 0


def test_dry_run_and_profile_validation_do_not_call_provider(tmp_path, monkeypatch):
    make_batch(tmp_path / "input")
    monkeypatch.setattr(
        cli,
        "JevClient",
        lambda *a, **kw: pytest.fail("Provider must not be constructed"),
    )
    assert cli.main(args(tmp_path, "--dry-run")) == 0
    assert not (tmp_path / "scores.csv").exists()
    assert cli.main(["--validate-profile"]) == 0


def test_missing_key_exports_configuration_failures(tmp_path, monkeypatch):
    make_batch(tmp_path / "input")
    monkeypatch.delenv(cli.JEV_API_KEY_ENV, raising=False)
    assert cli.main(args(tmp_path, "--yes")) == 2
    rows = csv_rows(tmp_path / "scores.csv")
    assert len(rows) == 2 and all(r["failed_stage"] == "configuration" for r in rows)


@pytest.mark.parametrize(
    "extra",
    [
        ["--workers", "0"],
        ["--percentage", "0"],
        ["--percentage", "101"],
        ["--percentage", "nan"],
        ["--request-timeout", "inf"],
        ["--max-chunks", "-1"],
        ["--chunk-chars", "0"],
        ["--mode", "score", "--only-missing"],
    ],
)
def test_invalid_options_rejected(tmp_path, extra):
    with pytest.raises(SystemExit) as exc:
        cli.main(args(tmp_path, *extra))
    assert exc.value.code == 2
