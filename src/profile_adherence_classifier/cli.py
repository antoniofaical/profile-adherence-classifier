"""Content bundle -> chunks -> Jev -> profile score -> complete CSV."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback
from importlib.resources import files
from pathlib import Path

from . import __version__
from .adapters.jev import JevClient
from .application.batch import execute_batch, failure, finish_batch, prepare_batch
from .application.inputs import import_legacy, load_batch
from .domain.profile import load_profile, question_set_sha256, validate_profile

JEV_API_KEY_ENV = "TYPESAFE_PSN_DIG_TWIN_CLASS"


def positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return result


def nonnegative_int(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("Must be nonnegative")
    return result


def positive_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Must be finite and positive")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify collected content against a scoring profile"
    )
    parser.add_argument("--version", action="version", version=__version__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--evidence-batch", type=Path)
    inputs.add_argument("--import-legacy", type=Path, metavar="EVIDENCE_ROOT")
    parser.add_argument(
        "--profile", type=Path, help="Default: packaged digital_twin profile"
    )
    parser.add_argument("--validate-profile", action="store_true")
    parser.add_argument(
        "--output", type=Path, help="CSV destination outside evidence input"
    )
    parser.add_argument(
        "--work-dir", type=Path, help="Default: classification/ next to the CSV"
    )
    parser.add_argument("--workers", type=positive_int, default=1)
    parser.add_argument("--chunk-chars", type=positive_int, default=20_000)
    sampling = parser.add_mutually_exclusive_group()
    sampling.add_argument("--percentage", type=positive_float)
    sampling.add_argument("--max-chunks", type=nonnegative_int, default=0)
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument("--api-key-env", default=JEV_API_KEY_ENV)
    parser.add_argument("--request-timeout", type=positive_float, default=60.0)
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Reuse only compatible completed results",
    )
    parser.add_argument("--mode", choices=("classify", "score"), default="classify")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and save a plan without API calls",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Authorize planned paid Jev calls"
    )
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--traceback", action="store_true")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)
    if not args.validate_profile and (
        not (args.evidence_batch or args.import_legacy) or not args.output
    ):
        parser.error("Provide --evidence-batch (or --import-legacy) and --output")
    if args.percentage is not None and args.percentage > 100:
        parser.error("Percentage must be at most 100")
    if not args.model.strip() or not args.api_key_env.strip():
        parser.error("Model and API key environment name must not be empty")
    if args.mode == "score" and args.only_missing:
        parser.error("--only-missing applies to classify mode")
    return args


def approve() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input("Autorizar chamadas Jev pagas? [s/N] ").strip().casefold() in {
            "s",
            "sim",
            "y",
            "yes",
        }
    except EOFError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        profile = (
            load_profile(args.profile)
            if args.profile
            else json.loads(
                files("profile_adherence_classifier.profiles")
                .joinpath("digital_twin.json")
                .read_text(encoding="utf-8")
            )
        )
        validate_profile(profile)
        if args.validate_profile:
            print(f"Perfil válido: {profile['id']}@{profile['version']}")
            print(f"question_set_sha256: {question_set_sha256(profile)}")
            return 0
        content = (
            import_legacy(args.import_legacy)
            if args.import_legacy
            else load_batch(args.evidence_batch)
        )
        prepared = prepare_batch(
            content,
            work_dir=args.work_dir or args.output.parent / "classification",
            output=args.output,
            profile=profile,
            model=args.model,
            chunk_chars=args.chunk_chars,
            percentage=args.percentage,
            max_chunks=args.max_chunks,
            only_missing=args.only_missing,
            mode=args.mode,
        )
        requests = prepared.state["jev_requests_planned"]
        print(
            f"PLANO: empresas={len(content.sites)}, chamadas Jev={requests}, reutilizadas={len(prepared.results)}"
        )
        print(f"Registro: {prepared.directory.resolve()}")
        if args.dry_run:
            return (
                1
                if any(s["status"] == "failed" for s in prepared.state["sites"])
                else 0
            )
        if requests and not (args.yes or approve()):
            for entry in prepared.state["sites"]:
                if entry["status"] == "planned":
                    entry.update(
                        status="cancelled",
                        failed_stage="authorization",
                        error={
                            "type": "NotAuthorized",
                            "message": "Paid Jev calls not authorized",
                        },
                    )
            print("Cancelado; nenhuma chamada paga realizada.")
            return finish_batch(prepared, args.output)
        api_key = os.getenv(args.api_key_env, "")
        if requests and not api_key:
            for entry in prepared.state["sites"]:
                if entry["status"] == "planned":
                    failure(
                        entry,
                        ValueError(f"Missing Jev API key: {args.api_key_env}"),
                        "configuration",
                    )
            finish_batch(prepared, args.output)
            print("Chave Jev ausente.", file=sys.stderr)
            return 2
        execute_batch(
            prepared,
            client_factory=lambda: JevClient(api_key, timeout=args.request_timeout),
            workers=args.workers,
            progress=None
            if args.no_progress
            else lambda message: print(message, file=sys.stderr),
        )
        code = finish_batch(prepared, args.output)
        if args.traceback or args.verbose:
            for entry in prepared.state["sites"]:
                if entry.get("error"):
                    print(f"[{entry['name']}] {entry['error']}", file=sys.stderr)
                    if args.traceback and entry.get("traceback"):
                        print(entry["traceback"], file=sys.stderr)
        print(
            f"CSV: {args.output.resolve()}; empresas com score={len(prepared.results)}"
        )
        return code
    except Exception as exc:  # noqa: BLE001 - isolate each company or report CLI failure
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        if args.traceback:
            traceback.print_exc()
        return 2
