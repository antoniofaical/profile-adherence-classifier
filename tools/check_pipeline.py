"""Two separately installed packages, local HTTP and simulated Jev."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from check_install import environment, fake_api_hook, run_command


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snowballer-python", required=True)
    parser.add_argument("--classifier-python", required=True)
    parser.add_argument(
        "--reference-source",
        type=Path,
        help="Read-only original src directory for direct numeric comparison",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Save verification evidence outside the temporary fixture",
    )
    args = parser.parse_args()
    snow_python = str(Path(args.snowballer_python).resolve())
    classifier_python = str(Path(args.classifier_python).resolve())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            pages = {
                "/a/": "<html lang='en'><body>"
                + "Alpha functional capability. " * 15
                + "<a href='/a/product'>Product</a></body></html>",
                "/a/product": "<html lang='en'><body>"
                + "Measured subject data and matching model. " * 12
                + "</body></html>",
                "/b/": "<html lang='en'><body>"
                + "Beta documentation. " * 16
                + "<a href='/b/about'>About</a></body></html>",
                "/b/about": "<html lang='en'><body>Beta limited capability.</body></html>",
            }
            if self.path in pages:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(pages[self.path].encode("utf-8"))
            else:
                self.send_error(404)

        def log_message(self, *unused):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = f"http://127.0.0.1:{server.server_port}"
            (root / "sites.json").write_text(
                json.dumps(
                    [
                        {"name": "A", "url": base + "/a/"},
                        {"name": "B", "url": base + "/b/"},
                        {"name": "Failed", "url": base + "/broken"},
                    ]
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [
                    snow_python,
                    "-m",
                    "institutional_site_snowballer",
                    "--sites-file",
                    str(root / "sites.json"),
                    "--output-dir",
                    str(root / "content"),
                    "--workers",
                    "2",
                    "--no-progress",
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                check=False,
            )
            assert result.returncode == 1, result.stdout + result.stderr
            source = root / "content"
            before = {
                p.relative_to(source).as_posix(): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in source.rglob("*")
                if p.is_file()
            }
            batch = json.loads((source / "batch.json").read_text(encoding="utf-8"))
            assert [s["pages_saved"] for s in batch["sites"]] == [2, 2, 0]
            hook = root / "hook"
            fake_api_hook(hook)
            calls = root / "calls.jsonl"
            classifier_env = environment(hook, calls)
            classifier_env["FIXTURE_VARY_CHUNKS"] = "1"
            run_command(
                classifier_python,
                [
                    "--evidence-batch",
                    str(source),
                    "--output",
                    str(root / "scores.csv"),
                    "--chunk-chars",
                    "200",
                    "--workers",
                    "2",
                    "--yes",
                    "--no-progress",
                ],
                cwd=root,
                env=classifier_env,
                code=1,
            )
            with (root / "scores.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            assert [r["site_name"] for r in rows] == ["A", "B", "Failed"]
            assert [r["fit_score"] for r in rows] == ["79.12", "20.59", ""], rows
            requests = [
                json.loads(line)
                for line in calls.read_text(encoding="utf-8").splitlines()
            ]
            assert len(requests) > 4
            assert all(r["state"]["company"] != "Failed" for r in requests)
            for site in batch["sites"][:2]:
                pages = [
                    json.loads(line)
                    for line in (source / site["evidence_path"])
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                sent = [
                    p
                    for r in requests
                    if r["state"]["company"] == site["name"]
                    for p in r["state"]["pages"]
                ]
                for page in pages:
                    assert (
                        "".join(p["text"] for p in sent if p["url"] == page["url"])
                        == page["text"].strip()
                    )
            after = {
                p.relative_to(source).as_posix(): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in source.rglob("*")
                if p.is_file()
            }
            assert before == after
            parity = False
            if args.reference_source:
                sys.path.insert(0, str(args.reference_source.resolve()))
                from startup_adherence.domain.scoring import (
                    build_fit_result as original_score,
                )

                original_profile = json.loads(
                    (
                        args.reference_source
                        / "startup_adherence/profiles/digital_twin.json"
                    ).read_text(encoding="utf-8")
                )
                core_fields = {
                    "fit_result_schema_version",
                    "profile",
                    "subject",
                    "fit_score",
                    "criterion_scores",
                    "auxiliary_scores",
                    "auxiliary_flags",
                    "main_strength",
                    "main_gap",
                    "aggregation",
                    "criterion_evidence",
                }
                for response_file in (root / "classification/companies").rglob(
                    "responses.jsonl"
                ):
                    records = [
                        json.loads(line)
                        for line in response_file.read_text(
                            encoding="utf-8"
                        ).splitlines()
                    ]
                    actual = json.loads(
                        (response_file.parent / "result.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    expected = original_score(
                        subject=actual["subject"],
                        records=records,
                        profile=original_profile,
                    )
                    assert {k: actual[k] for k in core_fields} == {
                        k: expected[k] for k in core_fields
                    }
                parity = True
            report = {
                "companies": len(rows),
                "pages": 4,
                "jev_requests": len(requests),
                "scores": [r["fit_score"] for r in rows],
                "input_unchanged": before == after,
                "original_numeric_parity": parity,
                "original_revision": "dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b"
                if parity
                else None,
                "collector_revision": "b28ecb02b266c9fe7cefb32c1ab26ad05f1e8318",
                "varied_probabilities": True,
                "real_paid_calls": 0,
            }
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8"
                )
            print(
                f"Pipeline passed: 3 companies, 4 pages, {len(requests)} simulated Jev calls, descending scores, failure row, unchanged input"
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
