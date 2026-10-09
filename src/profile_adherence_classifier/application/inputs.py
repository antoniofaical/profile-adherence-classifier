"""Read-only, validated content bundles and legacy crawl imports."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..storage import read_json, sha256_file


@dataclass(frozen=True)
class ContentBatch:
    root: Path
    index: dict[str, Any]
    sites: list[dict[str, Any]]
    legacy: bool = False


def identity_key(site: dict[str, Any]) -> str:
    value = json.dumps([site["name"], site["url"]], ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_identity(site: dict[str, Any]) -> None:
    for key in ("name", "url"):
        if not isinstance(site.get(key), str) or not site[key].strip():
            raise ValueError(f"Site {key} must be a non-empty string")
    parsed = urlsplit(site["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Site URL must be HTTP(S) with a host")


def contained_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Artifact path must be a relative path with forward slashes")
    if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
        raise ValueError("Absolute artifact path is forbidden")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate == root.resolve():
        raise ValueError("Artifact path escapes the content batch")
    if not candidate.is_file():
        raise FileNotFoundError(f"Artifact missing: {relative}")
    return candidate


def load_batch(path: Path) -> ContentBatch:
    index_path = path / "batch.json" if path.is_dir() else path
    root = index_path.resolve().parent
    index = read_json(index_path)
    if type(index.get("schema_version")) is not int or index["schema_version"] != 1:
        raise ValueError("Unsupported content batch schema_version")
    if index.get("status") not in {"completed", "completed_with_failures"}:
        raise ValueError("Content batch has not completed")
    sites = index.get("sites")
    if not isinstance(sites, list) or not sites:
        raise ValueError("Content batch must contain sites")
    names: set[str] = set()
    for site in sites:
        if not isinstance(site, dict):
            raise TypeError("Site entry must be an object")
        validate_identity(site)
        name = site["name"].casefold()
        if name in names:
            raise ValueError("Content batch contains duplicate company names")
        names.add(name)
        if site.get("status") not in {"completed", "failed"}:
            raise ValueError("Content batch contains unfinished site entries")
    for field, expected in (
        ("sites_selected", len(sites)),
        ("sites_completed", sum(s["status"] == "completed" for s in sites)),
        ("sites_failed", sum(s["status"] == "failed" for s in sites)),
    ):
        if type(index.get(field)) is not int or index[field] != expected:
            raise ValueError(f"Content batch counter differs: {field}")
    expected_status = (
        "completed_with_failures" if index["sites_failed"] else "completed"
    )
    if index["status"] != expected_status:
        raise ValueError("Content batch status disagrees with site outcomes")
    return ContentBatch(root, index, sites)


def import_legacy(root: Path) -> ContentBatch:
    """Import manifested historical crawls without changing source files."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Legacy evidence root must be a directory")
    sites = []
    for path in sorted(root.glob("*/manifest.json")):
        path = contained_path(root, path.relative_to(root).as_posix())
        manifest = read_json(path)
        site = {"name": manifest.get("site_name"), "url": manifest.get("root_url")}
        validate_identity(site)
        evidence = path.parent / "evidence.jsonl"
        exists = evidence.is_file()
        sites.append(
            {
                **site,
                "status": "completed" if exists else "failed",
                "evidence_path": evidence.relative_to(root).as_posix()
                if exists
                else None,
                "manifest_path": path.relative_to(root).as_posix() if exists else None,
                "evidence_sha256": sha256_file(evidence) if exists else None,
                "manifest_sha256": sha256_file(path) if exists else None,
                "pages_saved": manifest.get("pages_saved"),
                "evidence_is_partial": bool(
                    manifest.get("crawl_limited", manifest.get("stopped_by_page_limit"))
                )
                or bool(manifest.get("page_errors")),
                "failed_stage": None if exists else "evidence_import",
                "error": None
                if exists
                else {
                    "type": "FileNotFoundError",
                    "message": "Legacy evidence file missing",
                },
            }
        )
    if not sites:
        raise ValueError("No legacy company manifests found")
    if len({s["name"].casefold() for s in sites}) != len(sites):
        raise ValueError("Duplicate names in legacy manifests")
    index = {
        "schema_version": 1,
        "status": "completed_with_failures"
        if any(s["status"] == "failed" for s in sites)
        else "completed",
        "producer": {"name": "legacy-crawl-import"},
        "sites_selected": len(sites),
        "sites_completed": sum(s["status"] == "completed" for s in sites),
        "sites_failed": sum(s["status"] == "failed" for s in sites),
        "sites": sites,
    }
    return ContentBatch(root, index, sites, legacy=True)


def validated_content(batch: ContentBatch, site: dict[str, Any]) -> tuple[bytes, bytes]:
    """Return exact validated bytes to freeze before paid calls."""
    evidence = contained_path(batch.root, site.get("evidence_path"))
    manifest_path = contained_path(batch.root, site.get("manifest_path"))
    contents = []
    for path, field in (
        (evidence, "evidence_sha256"),
        (manifest_path, "manifest_sha256"),
    ):
        expected = site.get(field)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid {field}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Artifact hash differs: {field}")
        contents.append(data)
    manifest = json.loads(contents[1].decode("utf-8"))
    if not isinstance(manifest, dict):
        raise TypeError("Manifest must be an object")
    if (
        manifest.get("site_name") != site["name"]
        or manifest.get("root_url") != site["url"]
    ):
        raise ValueError("Manifest company identity differs from content batch")
    records = []
    for line in contents[0].decode("utf-8").splitlines():
        if not line.strip():
            raise ValueError("Blank evidence record")
        record = json.loads(line)
        required = (
            ("url", "text")
            if batch.legacy
            else ("url", "text", "content_type", "language_hint")
        )
        if not isinstance(record, dict) or any(
            not isinstance(record.get(k), str) for k in required
        ):
            raise ValueError("Invalid evidence page record")
        records.append(record)
    count = len(records)
    if (
        not count
        or manifest.get("pages_saved") != count
        or site.get("pages_saved") != count
    ):
        raise ValueError("Evidence page count differs from manifest/batch")
    has_text = any(record["text"].strip() for record in records)
    if not batch.legacy and site.get("has_text") is not has_text:
        raise ValueError("Content batch has_text differs from evidence")
    partial = bool(
        manifest.get("crawl_limited", manifest.get("stopped_by_page_limit"))
    ) or bool(manifest.get("page_errors"))
    if site.get("evidence_is_partial") is not partial:
        raise ValueError("Content batch partial flag differs from manifest")
    if not has_text:
        raise ValueError("No textual evidence found")
    return contents[0], contents[1]
