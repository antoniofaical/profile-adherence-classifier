import csv
import json
from importlib.resources import files

from profile_adherence_classifier.domain.profile import criterion_ids
from profile_adherence_classifier.storage import sha256_file, write_json


def profile():
    return json.loads(
        files("profile_adherence_classifier.profiles")
        .joinpath("digital_twin.json")
        .read_text()
    )


def make_batch(root, specs=None):
    specs = specs or [("A", ["A" * 25, "B" * 11], False), ("B", ["C" * 10], False)]
    sites = []
    for index, (name, texts, partial) in enumerate(specs):
        url = f"https://company{index}.test/"
        entry = {"name": name, "url": url}
        if texts is None:
            entry.update(
                status="failed",
                failed_stage="crawl",
                error={"type": "ValueError", "message": "No pages"},
            )
        else:
            directory = root / f"company{index}"
            directory.mkdir(parents=True)
            records = [
                {
                    "url": url + str(i),
                    "text": text,
                    "content_type": "text/html",
                    "language_hint": "pt-BR",
                }
                for i, text in enumerate(texts)
            ]
            evidence = directory / "evidence.jsonl"
            evidence.write_text(
                "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
            )
            manifest = directory / "manifest.json"
            write_json(
                manifest,
                {
                    "site_name": name,
                    "root_url": url,
                    "pages_saved": len(records),
                    "crawl_limited": partial,
                    "page_errors": [],
                    "crawl_limit_reasons": ["page_limit"] if partial else [],
                },
            )
            entry.update(
                status="completed",
                pages_saved=len(records),
                has_text=any(t.strip() for t in texts),
                evidence_path=f"company{index}/evidence.jsonl",
                manifest_path=f"company{index}/manifest.json",
                evidence_sha256=sha256_file(evidence),
                manifest_sha256=sha256_file(manifest),
                evidence_is_partial=partial,
            )
        sites.append(entry)
    failed = sum(s["status"] == "failed" for s in sites)
    batch = {
        "schema_version": 1,
        "status": "completed_with_failures" if failed else "completed",
        "sites_selected": len(sites),
        "sites_completed": len(sites) - failed,
        "sites_failed": failed,
        "sites": sites,
    }
    write_json(root / "batch.json", batch)
    return root / "batch.json"


class FakeJev:
    def __init__(self, *, fail_subject=None, fail_at=1):
        self.calls = []
        self.fail_subject = fail_subject
        self.fail_at = fail_at

    def evaluate(self, *, subject, pages, profile, model):
        self.calls.append((subject, pages, model))
        if (
            subject == self.fail_subject
            and sum(c[0] == subject for c in self.calls) == self.fail_at
        ):
            raise RuntimeError("Provider unavailable")
        value = 0.8 if subject.startswith("A") else 0.3
        values = dict.fromkeys(criterion_ids(profile), value)
        return values, {
            "answers": {k: {"noul": v} for k, v in values.items()},
            "provider_version": "fixture",
        }


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def tree_hashes(root):
    return {
        p.relative_to(root).as_posix(): sha256_file(p)
        for p in root.rglob("*")
        if p.is_file()
    }
