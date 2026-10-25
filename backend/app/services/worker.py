"""Isolated subprocess worker entry point (`REQ-SEC-06`).

Never receives application database secrets. Enforces `RLIMIT_FSIZE` (64 MiB)
and 5 MiB stdin payload bounds.
"""

from __future__ import annotations

import json
import resource
import sys
from pathlib import Path
from typing import Any

from app.schemas.content import GeneratedContent
from app.services import gemini
from app.services.export import export_content
from app.services.gemini import _generate_once_local_with_usage
from app.services.pptx_writer import calculate_slide_count
from app.services.retry import extract_status_code, is_transient_error


def run_worker_entrypoint(argv: list[str] | None = None) -> None:
    """Execute the requested isolated worker action (`export`, `generate`, or `slides`)."""
    args = argv if argv is not None else sys.argv
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    raw_input = sys.stdin.buffer.read(5 * 1024 * 1024 + 1)
    if len(raw_input) > 5 * 1024 * 1024:
        raise ValueError("Worker input payload exceeds 5 MiB")

    payload: dict[str, Any] = json.loads(raw_input)
    mode = args[1] if len(args) > 1 else ""

    if mode == "export":
        content = GeneratedContent.model_validate(payload["content"])
        fmt = str(payload["format"])
        data, _, _ = export_content(content, fmt)
        sys.stdout.buffer.write(data)
    elif mode == "generate":
        gemini.MODEL = str(payload.get("model") or gemini.MODEL)
        try:
            gen_result = _generate_once_local_with_usage(
                str(payload["prompt"]),
                payload.get("key"),
                float(payload["timeout"]) if payload.get("timeout") else None,
                system_instruction=payload.get("system_instruction"),
            )
            result: dict[str, Any] = {
                "content": gen_result.content.model_dump(mode="json"),
                "prompt_tokens": gen_result.prompt_tokens,
                "candidate_tokens": gen_result.candidate_tokens,
                "total_tokens": gen_result.total_tokens,
                "model_name": gen_result.model_name,
                "latency_ms": gen_result.latency_ms,
            }
        except Exception as exc:  # noqa: BLE001
            result = {
                "error": {
                    "code": extract_status_code(exc),
                    "retryable": is_transient_error(exc),
                    "message": str(exc),
                }
            }
        sys.stdout.write(json.dumps(result))
    elif mode == "slides":
        content = GeneratedContent.model_validate(payload)
        sys.stdout.write(str(calculate_slide_count(content)))
    else:
        raise ValueError(f"Unknown worker operation: {mode!r}")


if Path(sys.argv[0]).name == "worker.py":
    run_worker_entrypoint()
