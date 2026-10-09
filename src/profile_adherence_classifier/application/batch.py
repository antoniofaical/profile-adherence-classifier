"""Plan, authorize externally, execute and export independent company jobs."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..domain.profile import profile_sha256
from ..storage import RunStore, timestamp, write_json
from .classification import (
    ClassificationPlan,
    classify_saved,
    current_result,
    plan_classification,
    plan_sha256,
    replay,
)
from .failures import JobFailure
from .inputs import ContentBatch, identity_key, validated_content
from .reporting import export_csv, rows_for_results


@dataclass
class PreparedBatch:
    content: ContentBatch
    directory: Path
    work_dir: Path
    profile: dict[str, Any]
    model: str
    state: dict[str, Any]
    plans: dict[str, ClassificationPlan]
    snapshots: dict[str, Path]
    results: dict[str, dict[str, Any]]

    @property
    def requests(self) -> int:
        return sum(p.jev_requests for p in self.plans.values())

    def save(self) -> None:
        write_json(self.directory / "batch.json", self.state)


def ensure_output_separation(
    content: ContentBatch, work_dir: Path, output: Path
) -> None:
    source, work, csv = content.root.resolve(), work_dir.resolve(), output.resolve()
    if work.is_relative_to(source) or source.is_relative_to(work):
        raise ValueError(
            "Classifier work directory must be separate from content input"
        )
    if csv.is_relative_to(source):
        raise ValueError("CSV output must be outside content input")
    if csv.is_relative_to(work) or work.is_relative_to(csv):
        raise ValueError("CSV output must be outside classifier work directory")
    if output.exists() and output.is_dir():
        raise ValueError("CSV output is a directory")


def failure(entry: dict[str, Any], exc: Exception, stage: str) -> None:
    error = JobFailure.from_exception(exc, stage)
    entry.update(
        status="failed",
        failed_stage=error.stage,
        error={"type": error.error_type, "message": error.message},
        traceback=error.traceback_text,
        result_path=None,
    )


def compatible_result(
    directory: Path,
    profile: dict[str, Any],
    model: str,
    plan: ClassificationPlan,
    site: dict[str, Any],
    *,
    rescore: bool = False,
) -> dict[str, Any]:
    _, state, previous = RunStore(directory, profile).current()
    if state["evidence_sha256"] != plan.evidence_sha256 or state["model"] != model:
        raise ValueError("Saved evidence or model differs")
    parameters = state["parameters"]
    if parameters.get("plan_sha256") != plan_sha256(plan):
        raise ValueError("Saved chunk selection differs")
    if parameters.get("manifest_sha256") != site["manifest_sha256"]:
        raise ValueError("Saved crawl coverage differs")
    if (
        previous.get("subject") != site["name"]
        or previous.get("site_url") != site["url"]
    ):
        raise ValueError("Saved company identity differs")
    if not rescore and state["profile_sha256"] != profile_sha256(profile):
        raise ValueError("Saved scoring profile differs")
    return previous if rescore else current_result(directory, profile)


def prepare_batch(
    content: ContentBatch,
    *,
    work_dir: Path,
    output: Path,
    profile: dict[str, Any],
    model: str,
    chunk_chars: int = 20_000,
    percentage: float | None = None,
    max_chunks: int = 0,
    only_missing: bool = False,
    mode: str = "classify",
) -> PreparedBatch:
    ensure_output_separation(content, work_dir, output)
    directory = work_dir / "batches" / uuid4().hex
    state = {
        "schema_version": 1,
        "status": "planned",
        "created_at": timestamp(),
        "profile_id": profile["id"],
        "profile_version": profile["version"],
        "profile_sha256": profile_sha256(profile),
        "model": model,
        "mode": mode,
        "sites_selected": len(content.sites),
        "sites": [],
        "legacy_import": content.legacy,
    }
    prepared = PreparedBatch(
        content, directory, work_dir, profile, model, state, {}, {}, {}
    )
    write_json(directory / "content-index.json", content.index)
    write_json(directory / "profile.json", profile)
    for site in content.sites:
        name, key = site["name"], identity_key(site)
        entry = {
            "name": name,
            "url": site["url"],
            "status": "planned",
            "failed_stage": None,
            "error": None,
            "result_path": None,
            "company_key": key,
            "evidence_is_partial": site.get("evidence_is_partial"),
        }
        state["sites"].append(entry)
        if site["status"] == "failed":
            entry.update(
                status="failed",
                failed_stage=site.get("failed_stage") or "crawl",
                error=site.get("error")
                or {"type": "CrawlFailure", "message": "Collection failed"},
            )
            continue
        try:
            evidence, manifest = validated_content(content, site)
            snapshot = directory / "inputs" / key
            snapshot.mkdir(parents=True)
            (snapshot / "evidence.jsonl").write_bytes(evidence)
            (snapshot / "manifest.json").write_bytes(manifest)
            plan = plan_classification(
                snapshot / "evidence.jsonl",
                chunk_chars=chunk_chars,
                percentage=percentage,
                max_chunks=max_chunks,
            )
            prepared.snapshots[name] = snapshot
            entry.update(
                evidence_sha256=plan.evidence_sha256,
                manifest_sha256=site["manifest_sha256"],
                requests_planned=plan.jev_requests,
                total_chunks=plan.total_chunks,
                sampling_strategy=plan.strategy,
            )
            company_dir = work_dir / "companies" / key
            if only_missing or mode == "score":
                try:
                    previous = compatible_result(
                        company_dir, profile, model, plan, site, rescore=mode == "score"
                    )
                    if mode != "score":
                        prepared.results[name] = previous
                        entry["status"] = "reused"
                        write_json(directory / "results" / f"{key}.json", previous)
                        entry["result_path"] = f"results/{key}.json"
                        continue
                except (OSError, ValueError, KeyError, TypeError):
                    if mode == "score":
                        raise
            prepared.plans[name] = plan
        except Exception as exc:  # noqa: BLE001 - isolate each company or report CLI failure
            failure(
                entry, exc, "evidence_plan" if mode == "classify" else "saved_result"
            )
    state["jev_requests_planned"] = prepared.requests if mode == "classify" else 0
    prepared.save()
    return prepared


def execute_batch(
    prepared: PreparedBatch,
    *,
    client_factory: Callable[[], Any] | None = None,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
) -> None:
    if workers < 1:
        raise ValueError("Workers must be positive")
    if (
        prepared.state["mode"] == "classify"
        and prepared.plans
        and client_factory is None
    ):
        raise ValueError("Authorized Jev client required")
    prepared.state["status"] = "running"
    prepared.save()
    entries = {s["name"]: s for s in prepared.state["sites"]}
    sites = {s["name"]: s for s in prepared.content.sites}

    def job(name: str) -> dict[str, Any]:
        company_dir = prepared.work_dir / "companies" / entries[name]["company_key"]
        if prepared.state["mode"] == "score":
            return replay(
                site_name=name, site_dir=company_dir, profile=prepared.profile
            )
        snapshot = prepared.snapshots[name]
        return classify_saved(
            site_name=name,
            site_dir=company_dir,
            profile=prepared.profile,
            plan=prepared.plans[name],
            jev_client=client_factory(),
            model=prepared.model,
            evidence_file=snapshot / "evidence.jsonl",
            manifest_file=snapshot / "manifest.json",
            extra_metadata={
                "site_url": sites[name]["url"],
                "manifest_sha256": sites[name]["manifest_sha256"],
                "content_snapshot": str(snapshot.resolve()),
            },
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(job, name): name for name in prepared.plans}
        for future in as_completed(futures):
            name = futures[future]
            entry = entries[name]
            try:
                result = future.result()
                destination = (
                    prepared.directory / "results" / f"{entry['company_key']}.json"
                )
                write_json(destination, result)
                prepared.results[name] = result
                entry.update(
                    status="rescored"
                    if prepared.state["mode"] == "score"
                    else "classified",
                    result_path=destination.relative_to(prepared.directory).as_posix(),
                )
            except Exception as exc:  # noqa: BLE001 - isolate each company or report CLI failure
                failure(
                    entry,
                    exc,
                    "classification"
                    if prepared.state["mode"] == "classify"
                    else "scoring",
                )
            prepared.save()
            if progress:
                progress(f"[{name}] {entry['status']}")


def finish_batch(prepared: PreparedBatch, output: Path) -> int:
    scored = rows_for_results(
        prepared.content.sites, prepared.results, prepared.profile
    )
    rows = {row["site_name"]: row for row in scored}
    for entry in prepared.state["sites"]:
        name = entry["name"]
        row = rows.setdefault(
            name,
            {
                "site_name": name,
                "site_url": entry["url"],
                "fit_score": None,
                "profile_id": prepared.profile["id"],
                "profile_version": prepared.profile["version"],
                "profile_sha256": profile_sha256(prepared.profile),
                "evidence_is_partial": entry.get("evidence_is_partial"),
            },
        )
        row.update(
            status=entry["status"],
            failed_stage=entry.get("failed_stage") or "",
            error=json.dumps(entry["error"], ensure_ascii=False)
            if entry.get("error")
            else "",
        )
    export_csv(list(rows.values()), prepared.profile, output)
    failed = sum(s["status"] == "failed" for s in prepared.state["sites"])
    cancelled = any(s["status"] == "cancelled" for s in prepared.state["sites"])
    prepared.state.update(
        status="cancelled"
        if cancelled
        else "completed_with_failures"
        if failed
        else "completed",
        completed_at=timestamp(),
        sites_scored=len(prepared.results),
        sites_failed=failed,
        csv_path=str(output.resolve()),
    )
    prepared.save()
    return 1 if failed or cancelled else 0
