"""Verify the installed command from outside its repository, without paid calls."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def fake_api_hook(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sitecustomize.py").write_text(
        """import json
import os
from pathlib import Path
import requests

def post(endpoint, *, headers, json, timeout):
    assert endpoint == "https://api.typesafe.ai/v1/systemone"
    assert headers["Authorization"] == "Bearer fixture"
    name = json["state"]["company"]
    with Path(os.environ["FIXTURE_CALLS"]).open("a", encoding="utf-8") as stream:
        stream.write(__import__("json").dumps(json) + "\\n")
    value = 0.85 if name == "A" else 0.25
    if os.environ.get("FIXTURE_VARY_CHUNKS") == "1":
        urls = [p["url"] for p in json["state"]["pages"]]
        if any(url.endswith("/product") for url in urls):
            value = 0.65
        elif any(url.endswith("/about") for url in urls):
            value = 0.1
    payload = {"answers": {key: {"noul": value} for key in json["questions"]}, "fixture": True}
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    return Response()

def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected real network request")

requests.post = post
requests.get = forbidden
requests.sessions.Session.request = forbidden
""",
        encoding="utf-8",
    )


def environment(hook: Path, calls: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(hook.resolve())
    env["TYPESAFE_PSN_DIG_TWIN_CLASS"] = "fixture"
    env["FIXTURE_CALLS"] = str(calls.resolve())
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_command(
    python: str, args: list[str], *, cwd: Path, env: dict[str, str], code: int = 0
) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        [python, "-m", "profile_adherence_classifier", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == code, completed.stdout + completed.stderr
    return completed


def main() -> None:
    for module in (
        "institutional_site_snowballer",
        "startup_adherence",
        "bs4",
        "pypdf",
    ):
        assert importlib.util.find_spec(module) is None, (
            f"Unexpected collector dependency: {module}"
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "content"
        source.mkdir()
        texts = ["A" * 50_005, "B" * 25]
        sites = []
        for i, text in enumerate(texts):
            name = "AB"[i]
            directory = source / name
            directory.mkdir()
            url = f"https://company{i}.example/"
            evidence = directory / "evidence.jsonl"
            evidence.write_text(
                json.dumps(
                    {
                        "url": url,
                        "text": text,
                        "content_type": "text/html",
                        "language_hint": "en",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manifest = directory / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "site_name": name,
                        "root_url": url,
                        "pages_saved": 1,
                        "crawl_limited": False,
                        "page_errors": [],
                    }
                ),
                encoding="utf-8",
            )
            sites.append(
                {
                    "name": name,
                    "url": url,
                    "status": "completed",
                    "evidence_path": f"{name}/evidence.jsonl",
                    "manifest_path": f"{name}/manifest.json",
                    "evidence_sha256": hashlib.sha256(
                        evidence.read_bytes()
                    ).hexdigest(),
                    "manifest_sha256": hashlib.sha256(
                        manifest.read_bytes()
                    ).hexdigest(),
                    "pages_saved": 1,
                    "has_text": True,
                    "evidence_is_partial": False,
                }
            )
        sites.append(
            {
                "name": "Failed",
                "url": "https://failed.example/",
                "status": "failed",
                "failed_stage": "crawl",
                "error": {"type": "Fixture", "message": "No pages"},
            }
        )
        (source / "batch.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "completed_with_failures",
                    "sites_selected": 3,
                    "sites_completed": 2,
                    "sites_failed": 1,
                    "sites": sites,
                }
            ),
            encoding="utf-8",
        )
        before = {
            p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*")
            if p.is_file()
        }
        hook = root / "hook"
        fake_api_hook(hook)
        calls = root / "calls.jsonl"
        env = environment(hook, calls)
        run_command(sys.executable, ["--validate-profile"], cwd=root, env=env)
        args = [
            "--evidence-batch",
            str(source),
            "--output",
            str(root / "scores.csv"),
            "--workers",
            "2",
            "--no-progress",
        ]
        run_command(sys.executable, [*args, "--yes"], cwd=root, env=env, code=1)
        records = [
            json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()
        ]
        assert len(records) == 4
        assert sum(len(p["text"]) for r in records for p in r["state"]["pages"]) == sum(
            map(len, texts)
        )
        with (root / "scores.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [r["site_name"] for r in rows] == ["A", "B", "Failed"]
        assert [r["fit_score"] for r in rows] == ["85.0", "25.0", ""]
        run_command(
            sys.executable, [*args, "--only-missing"], cwd=root, env=env, code=1
        )
        assert len(calls.read_text(encoding="utf-8").splitlines()) == 4
        after = {
            p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*")
            if p.is_file()
        }
        assert before == after
    print(
        "Installed classifier: isolated dependencies, full content, complete CSV, reuse and read-only input verified"
    )


if __name__ == "__main__":
    main()
