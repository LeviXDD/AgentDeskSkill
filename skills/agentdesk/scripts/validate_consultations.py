#!/usr/bin/env python3
"""Validate AgentDesk's optional public consultation control plane.

Consultation records are deliberately separate from the execution ledger.
Each record is JSON-compatible YAML at
``docs/pm/consultations/CNNN/consultation.yaml``.  Provider task IDs, host IDs,
callback IDs, and other live routing facts belong only in the gitignored
``.agentdesk/runtime/consultations.yaml`` file and are never accepted here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional


POLICY_FILE = Path("docs/pm/CONSULTATION-POLICY.yaml")
CONSULTATION_ROOT = Path("docs/pm/consultations")
RUNTIME_FILE = Path(".agentdesk/runtime/consultations.yaml")
EXECUTION_ROUTES_FILE = Path(".agentdesk/runtime/routes.yaml")
DECISIONS_FILE = Path("docs/pm/DECISIONS.md")
POLICY_SCHEMA_VERSION = "agentdesk.consultation-policy/v1"
RECORD_SCHEMA_VERSION = "agentdesk.consultation/v1"
RUNTIME_SCHEMA_VERSION = "agentdesk.consultation-runtime/v1"
BRIEF_SCHEMA_VERSION = "agentdesk.consultation-brief/v1"
RESPONSE_SCHEMA_VERSION = "agentdesk.consultation-response/v1"
SYNTHESIS_SCHEMA_VERSION = "agentdesk.consultation-synthesis/v1"

POLICY_FIELDS = (
    "schema_version",
    "enabled",
    "authorization_mode",
    "max_seats",
    "max_rounds",
    "require_independent_first_round",
    "allow_recursive_sessions",
    "default_access",
    "require_leader_synthesis",
    "require_owner_confirmation_for_derivation",
)
RECORD_FIELDS = (
    "schema_version",
    "consultation_id",
    "topic",
    "short_topic",
    "state",
    "created_at",
    "updated_at",
    "cancellation_reason",
    "creation_authorization",
    "seat_count",
    "round_limit",
    "seats",
    "rounds",
    "synthesis_path",
    "outcome_status",
    "confirmation",
    "derived_refs",
)
AUTHORIZATION_FIELDS = (
    "authorization_id",
    "kind",
    "granted_by",
    "granted_at",
    "topic_scope",
    "max_visible_sessions",
    "max_rounds",
    "read_only_required",
    "authorized_titles",
)
SEAT_FIELDS = (
    "seat_no",
    "seat_id",
    "seat_name",
    "expected_thread_title",
    "focus",
    "model_requirement",
    "status",
)
MODEL_REQUIREMENT_FIELDS = (
    "minimum_tier",
    "deliberation_tier",
    "required_capabilities",
)
ROUND_FIELDS = (
    "round",
    "independent",
    "brief_path",
    "brief_digest",
    "trigger_reason",
    "responses",
)
RESPONSE_FIELDS = (
    "response_id",
    "seat_id",
    "status",
    "path",
    "callback_id",
)
CONFIRMATION_FIELDS = (
    "confirmation_id",
    "status",
    "confirmed_by",
    "confirmed_scope",
    "recorded_at",
)
DERIVED_REFS_FIELDS = ("decision_ids", "task_card_paths")
BRIEF_FIELDS = (
    "schema_version",
    "consultation_id",
    "topic",
    "short_topic",
    "decision_owner",
    "authorization_id",
    "round_limit",
    "created_by",
    "created_at",
)
RESPONSE_ARTIFACT_FIELDS = (
    "schema_version",
    "consultation_id",
    "response_id",
    "seat_id",
    "seat_name",
    "round",
    "callback_id",
    "response_status",
    "transport_mode",
    "confidence",
    "created_at",
)
SYNTHESIS_FIELDS = (
    "schema_version",
    "consultation_id",
    "topic",
    "rounds_used",
    "outcome_status",
    "confirmation_required",
    "created_by",
    "created_at",
)
RUNTIME_FIELDS = ("schema_version", "updated_at", "consultations")
RUNTIME_CONSULTATION_FIELDS = (
    "source",
    "seats",
    "prompt_receipts",
    "response_receipts",
)
RUNTIME_SOURCE_FIELDS = (
    "thread_id",
    "host_id",
    "status",
    "verified_at",
)
RUNTIME_SEAT_FIELDS = (
    "expected_title",
    "actual_title",
    "thread_id",
    "host_id",
    "created_at",
    "creation_evidence",
    "status",
    "verified_at",
    "model_binding",
)
MODEL_BINDING_FIELDS = (
    "requested_model",
    "request_source",
    "actual_model",
    "status",
    "provider_evidence",
)
PROMPT_RECEIPT_FIELDS = (
    "prompt_id",
    "seat_id",
    "round",
    "source_thread_id",
    "destination_thread_id",
    "brief_digest",
    "status",
    "provider_receipt",
    "sent_at",
)
RESPONSE_RECEIPT_FIELDS = (
    "callback_id",
    "prompt_id",
    "response_id",
    "seat_id",
    "round",
    "source_thread_id",
    "destination_thread_id",
    "response_digest",
    "status",
    "provider_receipt",
    "received_at",
)

CONSULTATION_STATES = frozenset(
    {
        "draft",
        "awaiting_authorization",
        "authorized",
        "round_1_in_progress",
        "round_1_complete",
        "round_2_in_progress",
        "round_2_complete",
        "awaiting_confirmation",
        "closed",
        "cancelled",
    }
)
SEAT_STATUSES = frozenset(
    {"planned", "created", "prompted", "responded", "missing", "blocked", "cancelled"}
)
RESPONSE_STATUSES = frozenset({"received", "missing", "blocked"})
OUTCOME_STATUSES = frozenset(
    {"none", "proposed", "confirmed", "rejected", "deferred"}
)
CONFIRMATION_STATUSES = frozenset(
    {"pending", "confirmed", "rejected", "deferred", "not_required"}
)
MODEL_TIERS = frozenset({"basic", "standard", "advanced", "expert"})
DELIBERATION_TIERS = frozenset({"efficient", "balanced", "deep"})
AUTHORIZATION_KINDS = frozenset({"explicit_batch", "scoped_auto"})

CONSULTATION_ID_RE = re.compile(r"^C[0-9]{3}$")
SEAT_NO_RE = re.compile(r"^[0-9]{2}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
AUTHORIZATION_ID_RE = re.compile(r"^CA-(C[0-9]{3})-[A-Z0-9]{4,32}$")
CONFIRMATION_ID_RE = re.compile(r"^CC-(C[0-9]{3})-[A-Z0-9]{4,32}$")
ACTOR_RE = re.compile(r"^(?:User|Owner)(?::[A-Za-z0-9._-]{1,64})?$")
CONSULTATION_ROLE_NO_RE = re.compile(r"^C[0-9]{3}\.[0-9]{2}$")
CONSULTATION_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(C[0-9]{3})(?![A-Za-z0-9_-])"
)
CONFIRMATION_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(CC-(C[0-9]{3})-[A-Z0-9]{4,32})(?![A-Za-z0-9_-])"
)
RUNTIME_ONLY_KEYS = frozenset(
    {
        "thread_id",
        "client_thread_id",
        "source_thread_id",
        "destination_thread_id",
        "host_id",
        "worktree",
        "cursor",
        "token",
        "provider_receipt",
        "provider_evidence",
        "transport_receipt",
        "receipt_id",
        "actual_title",
        "verified_at",
    }
)
PLACEHOLDER_FRAGMENTS = (
    "<",
    "pending",
    "unbound",
    "placeholder",
    "local-codex-current",
    "local-codex-worker",
)
PLACEHOLDER_ID_RE = re.compile(r"(?:XXXX|TODO|TBD)", re.IGNORECASE)
PUBLIC_PLACEHOLDER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:XXXX|TODO|TBD)(?![A-Za-z0-9])", re.IGNORECASE
)
PUBLIC_ARTIFACT_RUNTIME_KEY_RE = re.compile(
    r"(?:"
    r"(?:\b(?:thread[-_. \u00a0]?id|threadId|client[-_. \u00a0]?thread[-_. \u00a0]?id|clientThreadId|"
    r"source[-_. \u00a0]?thread[-_. \u00a0]?id|sourceThreadId|destination[-_. \u00a0]?thread[-_. \u00a0]?id|destinationThreadId|"
    r"session[-_. \u00a0]?id|sessionId|host[-_. \u00a0]?id|hostId|worktree|local[-_. \u00a0]?path|localPath|"
    r"absolute[-_. \u00a0]?path|absolutePath|cursor|token|access[-_. \u00a0]?token|accessToken|api[-_. \u00a0]?key|"
    r"apiKey|secret|password|credential|prompt[-_. \u00a0]?id|promptId|callback[-_. \u00a0]?id|callbackId|"
    r"provider[-_. \u00a0]?receipt|providerReceipt|provider[-_. \u00a0]?evidence|providerEvidence|"
    r"transport[-_. \u00a0]?receipt|transportReceipt|receipt[-_. \u00a0]?id|receiptId|actual[-_. \u00a0]?title|"
    r"actualTitle|verified[-_. \u00a0]?at|verifiedAt)\b)"
    r"(?:[\"'`]?\s*(?::|：|=)\s*[^\s|}`]+|\s*\|\s*(?![-|\s])[^|\n]+\|)"
    r"|(?:\b(?:thread[-_. \u00a0]?id|threadId|client[-_. \u00a0]?thread[-_. \u00a0]?id|clientThreadId|"
    r"source[-_. \u00a0]?thread[-_. \u00a0]?id|sourceThreadId|destination[-_. \u00a0]?thread[-_. \u00a0]?id|destinationThreadId|"
    r"session[-_. \u00a0]?id|sessionId|host[-_. \u00a0]?id|hostId|prompt[-_. \u00a0]?id|promptId|"
    r"callback[-_. \u00a0]?id|callbackId|receipt[-_. \u00a0]?id|receiptId)\b\s+is\s+[^\s|}`]+)"
    r")",
    re.IGNORECASE,
)
PUBLIC_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s`'\"(=\[])(?:/(?:Users|home|root|tmp|opt|etc|workspace|mnt|srv|"
    r"private|Applications|Library|System|Volumes|usr|var)/|[A-Za-z]:\\)",
    re.MULTILINE | re.IGNORECASE,
)
PUBLIC_ARTIFACT_TEMPLATE_PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")
NON_SUCCESS_EVIDENCE_STATUSES = frozenset(
    {
        "cancelled",
        "denied",
        "error",
        "failed",
        "failure",
        "in_progress",
        "not_delivered",
        "pending",
        "processing",
        "queued",
        "rejected",
        "retrying",
        "timed_out",
        "timeout",
        "unavailable",
        "unknown",
    }
)
PROMPT_EVIDENCE_SUCCESS_STATUSES = frozenset(
    {"accepted", "acknowledged", "completed", "delivered", "ok", "sent", "succeeded", "success"}
)
RESPONSE_EVIDENCE_SUCCESS_STATUSES = frozenset(
    {"accepted", "completed", "delivered", "ok", "received", "succeeded", "success"}
)
MODEL_EVIDENCE_SUCCESS_STATUSES = frozenset(
    {"accepted", "completed", "ok", "succeeded", "success", "verified"}
)
CREATION_EVIDENCE_SUCCESS_STATUSES = frozenset(
    {"accepted", "completed", "created", "ok", "succeeded", "success"}
)

AUTH_REQUIRED_STATES = frozenset(
    {
        "authorized",
        "round_1_in_progress",
        "round_1_complete",
        "round_2_in_progress",
        "round_2_complete",
        "awaiting_confirmation",
        "closed",
    }
)
ROUND_ONE_REQUIRED_STATES = frozenset(
    {
        "round_1_in_progress",
        "round_1_complete",
        "round_2_in_progress",
        "round_2_complete",
        "awaiting_confirmation",
        "closed",
    }
)
ROUND_ONE_COMPLETE_STATES = frozenset(
    {
        "round_1_complete",
        "round_2_in_progress",
        "round_2_complete",
        "awaiting_confirmation",
        "closed",
    }
)
ROUND_TWO_REQUIRED_STATES = frozenset(
    {"round_2_in_progress", "round_2_complete"}
)
ROUND_TWO_COMPLETE_STATES = frozenset(
    {"round_2_complete", "awaiting_confirmation", "closed"}
)
ROUND_TWO_ALLOWED_STATES = ROUND_TWO_REQUIRED_STATES | ROUND_TWO_COMPLETE_STATES | {
    "cancelled"
}
SYNTHESIS_REQUIRED_STATES = frozenset({"awaiting_confirmation", "closed"})


class Reporter:
    """Collect stable diagnostics for CLI use and unit tests."""

    def __init__(self) -> None:
        self.passes = 0
        self.warnings = 0
        self.errors = 0

    def passed(self, message: str) -> None:
        self.passes += 1
        print(f"PASS  {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"WARN  {message}")

    def error(self, message: str) -> None:
        self.errors += 1
        print(f"ERROR {message}")

    def summary(self) -> None:
        print(
            "SUMMARY "
            f"{self.passes} pass(es), {self.warnings} warning(s), "
            f"{self.errors} error(s)"
        )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonplaceholder_public_string(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    lowered = str(value).strip().lower()
    return (
        lowered
        not in {
            "pending",
            "unbound",
            "placeholder",
            "local-codex-current",
            "local-codex-worker",
        }
        and re.fullmatch(r"<[^<>\n]+>", str(value).strip()) is None
        and PUBLIC_PLACEHOLDER_TOKEN_RE.search(str(value)) is None
        and re.search(r"[A-Za-z0-9\u4e00-\u9fff]", str(value)) is not None
    )


def _parse_rfc3339_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or "T" not in value:
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed


def _parse_frontmatter_scalar(raw_value: str) -> Any:
    value = raw_value.strip()
    if value in {"null", "~"}:
        return None
    if value in {"true", "false"}:
        return value == "true"
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _read_artifact_created_at(project: Path, raw_path: Any) -> Optional[datetime]:
    """Read one already-public-validated artifact timestamp without new diagnostics."""

    if not _nonempty_string(raw_path):
        return None
    logical = PurePosixPath(str(raw_path))
    if logical.is_absolute() or "." in logical.parts or ".." in logical.parts:
        return None
    candidate = project.joinpath(*logical.parts)
    if candidate.is_symlink() or not candidate.is_file():
        return None
    try:
        lines = candidate.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("created_at:"):
            return _parse_rfc3339_utc(
                _parse_frontmatter_scalar(line.split(":", 1)[1])
            )
    return None


def _read_frontmatter_artifact(
    path: Path,
    context: str,
    expected_fields: Optional[Iterable[str]],
    reporter: Reporter,
) -> Optional[tuple[dict[str, Any], str]]:
    """Read the narrow, flat YAML frontmatter used by consultation artifacts."""

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        reporter.error(f"cannot read {context}: {exc}")
        return None
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        reporter.error(f"{context} must start with YAML frontmatter")
        return None
    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration:
        reporter.error(f"{context} has no closing YAML frontmatter delimiter")
        return None

    values: dict[str, Any] = {}
    for line_number, line in enumerate(lines[1:closing_index], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace():
            if expected_fields is None:
                continue
            reporter.error(
                f"{context} frontmatter line {line_number} must be one flat key/value"
            )
            continue
        if ":" not in line:
            reporter.error(
                f"{context} frontmatter line {line_number} must be one flat key/value"
            )
            continue
        key, raw_value = line.split(":", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
            reporter.error(f"{context} frontmatter line {line_number} has an invalid key")
            continue
        if key in values:
            reporter.error(f"{context} frontmatter contains duplicate key {key!r}")
            continue
        values[key] = _parse_frontmatter_scalar(raw_value)

    if expected_fields is not None:
        _require_exact_keys(values, expected_fields, f"{context} frontmatter", reporter)
    body = "\n".join(lines[closing_index + 1 :])
    if PUBLIC_ARTIFACT_RUNTIME_KEY_RE.search(body):
        reporter.error(
            f"{context} contains a runtime-only routing or receipt field"
        )
    if (
        PUBLIC_ARTIFACT_TEMPLATE_PLACEHOLDER_RE.search(body)
        or PUBLIC_PLACEHOLDER_TOKEN_RE.search(body)
    ):
        reporter.error(f"{context} contains an unfilled template placeholder")
    if PUBLIC_ABSOLUTE_PATH_RE.search(content):
        reporter.error(f"{context} contains an absolute local filesystem path")
    return values, content


def _section_has_substantive_content(lines: list[str]) -> bool:
    structural_rows: set[int] = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and re.fullmatch(
            r"\|?(?:\s*:?-{3,}:?\s*\|)+\s*", stripped
        ):
            structural_rows.add(index)
            if index > 0 and lines[index - 1].strip().startswith("|"):
                structural_rows.add(index - 1)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or index in structural_rows:
            continue
        if stripped.startswith(("#", "<!--")):
            continue
        without_placeholders = PUBLIC_ARTIFACT_TEMPLATE_PLACEHOLDER_RE.sub(
            "", stripped
        )
        without_placeholders = PUBLIC_PLACEHOLDER_TOKEN_RE.sub(
            "", without_placeholders
        )
        normalized = re.sub(
            r"[\s|#>*_`~\[\]{}():;,.!?。，：；！？\-+/=]+",
            "",
            without_placeholders,
        )
        if re.search(r"[A-Za-z0-9\u4e00-\u9fff]", normalized):
            return True
    return False


def _require_sections(
    content: str, sections: Iterable[str], context: str, reporter: Reporter
) -> None:
    lines = content.splitlines()
    headings = {
        line.strip(): index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("## ")
    }
    for section in sections:
        if section not in headings:
            reporter.error(f"{context} is missing required section {section!r}")
            continue
        start = headings[section] + 1
        end = next(
            (
                index
                for index in range(start, len(lines))
                if lines[index].lstrip().startswith("## ")
            ),
            len(lines),
        )
        if not _section_has_substantive_content(lines[start:end]):
            reporter.error(
                f"{context} section {section!r} must contain substantive, "
                "non-placeholder content"
            )


def _validate_actor(value: Any, context: str, reporter: Reporter) -> None:
    if not isinstance(value, str):
        reporter.error(
            f"{context} must be 'User', 'Owner', or that role plus a stable ':id' suffix"
        )
        return

    actor = value.strip()
    if ACTOR_RE.fullmatch(actor) is None:
        reporter.error(
            f"{context} must be 'User', 'Owner', or that role plus a stable ':id' suffix"
        )
        return

    if ":" not in actor:
        return

    suffix = actor.split(":", 1)[1]
    if not _nonplaceholder_public_string(suffix) or not any(
        character.isalnum() for character in suffix
    ):
        reporter.error(
            f"{context} stable ':id' suffix must contain letters or digits and "
            "must not contain placeholder values"
        )


def _contains_exact_identifier(text: str, identifier: str) -> bool:
    return (
        re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(identifier)}(?![A-Za-z0-9_-])",
            text,
        )
        is not None
    )


def _require_exact_keys(
    value: dict[str, Any], fields: Iterable[str], context: str, reporter: Reporter
) -> bool:
    expected = set(fields)
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        reporter.error(f"{context} is missing required key(s): {', '.join(missing)}")
    if extra:
        reporter.error(f"{context} has unsupported key(s): {', '.join(extra)}")
    return not missing and not extra


def _load_json_object(
    path: Path, context: str, reporter: Reporter
) -> Optional[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        reporter.error(f"cannot read {context}: {exc}")
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        reporter.error(
            f"{context} is not JSON-compatible YAML: JSON parse error at "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )
        return None
    if not isinstance(value, dict):
        reporter.error(f"{context} must contain one JSON object at its root")
        return None
    return value


def _reject_runtime_fields(value: Any, context: str, reporter: Reporter) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_context = f"{context}.{key}"
            if key in RUNTIME_ONLY_KEYS:
                reporter.error(
                    f"{child_context} is runtime-only and must be stored under "
                    ".agentdesk/runtime/consultations.yaml"
                )
            _reject_runtime_fields(child, child_context, reporter)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_runtime_fields(child, f"{context}[{index}]", reporter)


def _reject_public_sensitive_strings(
    value: Any, context: str, reporter: Reporter
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_public_sensitive_strings(child, f"{context}.{key}", reporter)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_public_sensitive_strings(child, f"{context}[{index}]", reporter)
    elif isinstance(value, str):
        if PUBLIC_ARTIFACT_RUNTIME_KEY_RE.search(value):
            reporter.error(
                f"{context} contains a runtime-only routing, receipt, or credential value"
            )
        if PUBLIC_ABSOLUTE_PATH_RE.search(value):
            reporter.error(f"{context} contains an absolute local filesystem path")


def _safe_public_path(
    project: Path,
    raw_path: Any,
    context: str,
    consultation_id: str,
    reporter: Reporter,
    required: bool,
) -> Optional[Path]:
    if raw_path is None and not required:
        return None
    if not _nonempty_string(raw_path):
        reporter.error(f"{context} must be a non-empty project-relative path")
        return None
    logical = PurePosixPath(str(raw_path))
    if logical.is_absolute() or "." in logical.parts or ".." in logical.parts:
        reporter.error(f"{context} must not be absolute or contain '.'/'..'")
        return None
    required_prefix = PurePosixPath("docs/pm/consultations") / consultation_id
    if logical.parts[: len(required_prefix.parts)] != required_prefix.parts:
        reporter.error(
            f"{context} must be under {required_prefix.as_posix()}/"
        )
        return None
    candidate = project.joinpath(*logical.parts)
    try:
        if os.path.commonpath((str(project), str(candidate.resolve(strict=False)))) != str(project):
            reporter.error(f"{context} escapes the project root")
            return None
    except (OSError, ValueError) as exc:
        reporter.error(f"cannot resolve {context}: {exc}")
        return None
    current = project
    for part in logical.parts:
        current /= part
        if current.is_symlink():
            reporter.error(f"{context} must not traverse a symlink")
            return None
    if required and not candidate.is_file():
        reporter.error(f"{context} does not exist as a regular file: {raw_path}")
        return None
    if candidate.exists() and not candidate.is_file():
        reporter.error(f"{context} must identify a regular file: {raw_path}")
        return None
    return candidate


def _validate_string_array(
    value: Any, context: str, reporter: Reporter
) -> Optional[list[str]]:
    if not isinstance(value, list) or any(not _nonempty_string(item) for item in value):
        reporter.error(f"{context} must be an array of non-empty strings")
        return None
    result = [str(item).strip() for item in value]
    if len(result) != len(set(result)):
        reporter.error(f"{context} must not contain duplicates")
    if any(PUBLIC_PLACEHOLDER_TOKEN_RE.search(item) for item in result):
        reporter.error(f"{context} must not contain TODO/TBD/XXXX placeholders")
    if any(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", item) is None for item in result):
        reporter.error(f"{context} items must contain substantive text")
    return result


def _validate_brief_artifact(
    path: Path,
    context: str,
    consultation_id: str,
    topic: str,
    short_topic: str,
    round_limit: int,
    authorization_id: Optional[str],
    reporter: Reporter,
) -> Optional[datetime]:
    result = _read_frontmatter_artifact(path, context, BRIEF_FIELDS, reporter)
    if result is None:
        return None
    frontmatter, content = result
    expected = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "consultation_id": consultation_id,
        "topic": topic,
        "short_topic": short_topic,
        "round_limit": round_limit,
        "created_by": "Leader",
    }
    for key, value in expected.items():
        if frontmatter.get(key) != value:
            reporter.error(f"{context} frontmatter {key} must equal {value!r}")
    _validate_actor(
        frontmatter.get("decision_owner"),
        f"{context} frontmatter decision_owner",
        reporter,
    )
    if authorization_id is not None and frontmatter.get("authorization_id") != authorization_id:
        reporter.error(
            f"{context} frontmatter authorization_id must equal {authorization_id!r}"
        )
    created_at = _parse_rfc3339_utc(frontmatter.get("created_at"))
    if created_at is None:
        reporter.error(f"{context} frontmatter created_at must be a UTC RFC3339 timestamp")
    _require_sections(
        content,
        (
            "## 为什么需要研讨",
            "## 需要回答的问题",
            "## 已知背景",
            "## 关键假设",
            "## 范围",
            "## 决策标准",
            "## 可读上下文",
            "## 公共安全和权限边界",
            "## 回调要求",
        ),
        context,
        reporter,
    )
    return created_at
def _validate_response_artifact(
    path: Path,
    context: str,
    consultation_id: str,
    seat: Optional[dict[str, Any]],
    response_id: str,
    round_number: int,
    reporter: Reporter,
) -> Optional[datetime]:
    result = _read_frontmatter_artifact(
        path, context, RESPONSE_ARTIFACT_FIELDS, reporter
    )
    if result is None:
        return None
    frontmatter, content = result
    seat_id = str(seat.get("seat_id")) if isinstance(seat, dict) else ""
    seat_name = str(seat.get("seat_name")) if isinstance(seat, dict) else ""
    expected = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "consultation_id": consultation_id,
        "response_id": response_id,
        "seat_id": seat_id,
        "seat_name": seat_name,
        "round": round_number,
        "callback_id": None,
        "response_status": "completed",
    }
    for key, value in expected.items():
        if frontmatter.get(key) != value:
            reporter.error(f"{context} frontmatter {key} must equal {value!r}")
    if frontmatter.get("transport_mode") not in {
        "proactive_callback",
        "polled_recovery",
    }:
        reporter.error(
            f"{context} frontmatter transport_mode must be proactive_callback or polled_recovery"
        )
    if frontmatter.get("confidence") not in {"low", "medium", "high"}:
        reporter.error(f"{context} frontmatter confidence must be low, medium, or high")
    created_at = _parse_rfc3339_utc(frontmatter.get("created_at"))
    if created_at is None:
        reporter.error(f"{context} frontmatter created_at must be a UTC RFC3339 timestamp")
    _require_sections(
        content,
        (
            "## 推荐结论",
            "## 核心依据",
            "## 关键假设",
            "## 风险与反例",
            "## 不确定事项",
            "## 会改变本结论的信息",
            "## 建议下一步",
            "## 置信度",
        ),
        context,
        reporter,
    )
    if round_number == 2:
        _require_sections(content, ("## Round 2 补充（仅第二轮填写）",), context, reporter)
        if re.search(
            r"^\s*-?\s*结论是否改变[：:]\s*`?(?:unchanged|narrowed|changed)`?\s*$",
            content,
            re.MULTILINE,
        ) is None:
            reporter.error(
                f"{context} Round 2 must declare exactly one of unchanged, narrowed, or changed"
            )
    return created_at


def _validate_synthesis_artifact(
    path: Path,
    context: str,
    consultation_id: str,
    topic: str,
    rounds: list[Any],
    reporter: Reporter,
) -> Optional[datetime]:
    result = _read_frontmatter_artifact(path, context, SYNTHESIS_FIELDS, reporter)
    if result is None:
        return None
    frontmatter, content = result
    rounds_used = len(rounds)
    expected = {
        "schema_version": SYNTHESIS_SCHEMA_VERSION,
        "consultation_id": consultation_id,
        "topic": topic,
        "rounds_used": rounds_used,
        "outcome_status": "proposed",
        "confirmation_required": True,
        "created_by": "Leader",
    }
    for key, value in expected.items():
        if frontmatter.get(key) != value:
            reporter.error(f"{context} frontmatter {key} must equal {value!r}")
    created_at = _parse_rfc3339_utc(frontmatter.get("created_at"))
    if created_at is None:
        reporter.error(f"{context} frontmatter created_at must be a UTC RFC3339 timestamp")
    _require_sections(
        content,
        (
            "## 席位覆盖",
            "## 一致共识",
            "## 条件性共识",
            "## 关键分歧",
            "## 少数意见",
            "## 关键假设与残余风险",
            "## Leader 综合建议",
            "## 总体置信度",
            "## 需要 User / Owner 裁决",
            "## 不完整性与降级",
            "## 确认门禁",
        ),
        context,
        reporter,
    )
    content_lines = content.splitlines()
    for round_record in rounds:
        if not isinstance(round_record, dict):
            continue
        responses = round_record.get("responses")
        if not isinstance(responses, list):
            continue
        for response in responses:
            if not isinstance(response, dict) or response.get("status") not in {
                "missing",
                "blocked",
            }:
                continue
            seat_id = str(response.get("seat_id"))
            status = str(response.get("status"))
            if not any(seat_id in line and status in line.lower() for line in content_lines):
                reporter.error(
                    f"{context} must disclose {seat_id} as {status} in its seat coverage or degradation section"
                )
    return created_at


def _validate_policy(value: dict[str, Any], reporter: Reporter) -> Optional[dict[str, Any]]:
    context = POLICY_FILE.as_posix()
    starting_errors = reporter.errors
    _require_exact_keys(value, POLICY_FIELDS, context, reporter)
    if value.get("schema_version") != POLICY_SCHEMA_VERSION:
        reporter.error(
            f"{context}.schema_version must be {POLICY_SCHEMA_VERSION!r}"
        )
    if not isinstance(value.get("enabled"), bool):
        reporter.error(f"{context}.enabled must be a boolean")
    if value.get("authorization_mode") != "ask":
        reporter.error(f"{context}.authorization_mode must be 'ask' in V1")
    max_seats = value.get("max_seats")
    if not _is_int(max_seats) or not 1 <= max_seats <= 3:
        reporter.error(f"{context}.max_seats must be an integer from 1 through 3")
    max_rounds = value.get("max_rounds")
    if not _is_int(max_rounds) or not 1 <= max_rounds <= 2:
        reporter.error(f"{context}.max_rounds must be an integer from 1 through 2")
    expected_safety = {
        "require_independent_first_round": True,
        "allow_recursive_sessions": False,
        "default_access": "read_only",
        "require_leader_synthesis": True,
        "require_owner_confirmation_for_derivation": True,
    }
    for field, expected in expected_safety.items():
        if value.get(field) != expected:
            reporter.error(f"{context}.{field} must be {expected!r} in V1")
    if reporter.errors == starting_errors:
        reporter.passed(f"validated {context}")
    return value


def _validate_model_requirement(
    value: Any, context: str, reporter: Reporter
) -> None:
    if not isinstance(value, dict):
        reporter.error(f"{context} must be an object")
        return
    _require_exact_keys(value, MODEL_REQUIREMENT_FIELDS, context, reporter)
    if value.get("minimum_tier") not in MODEL_TIERS:
        reporter.error(
            f"{context}.minimum_tier must be one of: {', '.join(sorted(MODEL_TIERS))}"
        )
    if value.get("deliberation_tier") not in DELIBERATION_TIERS:
        reporter.error(
            f"{context}.deliberation_tier must be one of: "
            f"{', '.join(sorted(DELIBERATION_TIERS))}"
        )
    _validate_string_array(
        value.get("required_capabilities"),
        f"{context}.required_capabilities",
        reporter,
    )


def _validate_seats(
    value: Any,
    consultation_id: str,
    short_topic: str,
    declared_count: Any,
    maximum: int,
    context: str,
    reporter: Reporter,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(value, list):
        reporter.error(f"{context} must be an array")
        return [], []
    if not 1 <= len(value) <= maximum:
        reporter.error(f"{context} must contain 1 through {maximum} seats")
    if declared_count != len(value):
        reporter.error(f"seat_count must equal len(seats), got {declared_count!r}")

    seats: list[dict[str, Any]] = []
    titles: list[str] = []
    seen_seat_ids: set[str] = set()
    seen_focuses: set[str] = set()
    for index, raw_seat in enumerate(value):
        seat_context = f"{context}[{index}]"
        if not isinstance(raw_seat, dict):
            reporter.error(f"{seat_context} must be an object")
            continue
        seat = raw_seat
        _require_exact_keys(seat, SEAT_FIELDS, seat_context, reporter)
        seat_no = seat.get("seat_no")
        expected_no = f"{index + 1:02d}"
        if not isinstance(seat_no, str) or SEAT_NO_RE.fullmatch(seat_no) is None:
            reporter.error(f"{seat_context}.seat_no must contain exactly two digits")
        elif seat_no != expected_no:
            reporter.error(
                f"{seat_context}.seat_no must be contiguous and equal {expected_no!r}"
            )
        expected_seat_id = f"{consultation_id}.{seat_no}"
        seat_id = seat.get("seat_id")
        if seat_id != expected_seat_id:
            reporter.error(
                f"{seat_context}.seat_id must equal {expected_seat_id!r}"
            )
        if isinstance(seat_id, str):
            if seat_id in seen_seat_ids:
                reporter.error(f"duplicate seat_id: {seat_id}")
            seen_seat_ids.add(seat_id)
        seat_name = seat.get("seat_name")
        if not _nonplaceholder_public_string(seat_name):
            reporter.error(
                f"{seat_context}.seat_name must be a non-placeholder string"
            )
            seat_name = ""
        if any(character in str(seat_name) for character in ("\n", "\r", "\uff5c")):
            reporter.error(
                f"{seat_context}.seat_name must be one line and must not contain '｜'"
            )
        expected_title = f"{expected_seat_id} . {seat_name}｜{short_topic}"
        if seat.get("expected_thread_title") != expected_title:
            reporter.error(
                f"{seat_context}.expected_thread_title must equal {expected_title!r}"
            )
        titles.append(expected_title)
        if not _nonplaceholder_public_string(seat.get("focus")):
            reporter.error(f"{seat_context}.focus must be a non-placeholder string")
        else:
            focus = str(seat.get("focus")).strip()
            if focus in seen_focuses:
                reporter.error(f"{seat_context}.focus must be unique within the panel")
            seen_focuses.add(focus)
        _validate_model_requirement(
            seat.get("model_requirement"),
            f"{seat_context}.model_requirement",
            reporter,
        )
        if seat.get("status") not in SEAT_STATUSES:
            reporter.error(
                f"{seat_context}.status must be one of: "
                f"{', '.join(sorted(SEAT_STATUSES))}"
            )
        seats.append(seat)
    return seats, titles


def _validate_authorization(
    value: Any,
    consultation_id: str,
    topic: str,
    expected_titles: list[str],
    seat_count: int,
    round_limit: int,
    policy_max_sessions: int,
    policy_max_rounds: int,
    required: bool,
    context: str,
    reporter: Reporter,
) -> None:
    if value is None:
        if required:
            reporter.error(
                f"{context} is required before any visible consultation session is created"
            )
        return
    if not isinstance(value, dict):
        reporter.error(f"{context} must be null or an object")
        return
    _require_exact_keys(value, AUTHORIZATION_FIELDS, context, reporter)
    authorization_id = value.get("authorization_id")
    match = (
        AUTHORIZATION_ID_RE.fullmatch(str(authorization_id))
        if _nonempty_string(authorization_id)
        else None
    )
    if (
        match is None
        or match.group(1) != consultation_id
        or PLACEHOLDER_ID_RE.search(str(authorization_id)) is not None
    ):
        reporter.error(
            f"{context}.authorization_id must match CA-{consultation_id}-<4-32 uppercase alphanumerics> without placeholders"
        )
    _validate_actor(value.get("granted_by"), f"{context}.granted_by", reporter)
    if value.get("kind") not in AUTHORIZATION_KINDS:
        reporter.error(
            f"{context}.kind must be one of: {', '.join(sorted(AUTHORIZATION_KINDS))}"
        )
    if _parse_rfc3339_utc(value.get("granted_at")) is None:
        reporter.error(f"{context}.granted_at must be a UTC RFC3339 timestamp")
    if value.get("topic_scope") != topic:
        reporter.error(f"{context}.topic_scope must exactly equal consultation.topic")
    maximum_sessions = value.get("max_visible_sessions")
    if not _is_int(maximum_sessions) or not 1 <= maximum_sessions <= 3:
        reporter.error(f"{context}.max_visible_sessions must be an integer from 1 through 3")
    elif maximum_sessions < seat_count:
        reporter.error(
            f"{context}.max_visible_sessions does not authorize all declared seats"
        )
    elif maximum_sessions > policy_max_sessions:
        reporter.error(
            f"{context}.max_visible_sessions must not exceed policy max_seats {policy_max_sessions}"
        )
    maximum_rounds = value.get("max_rounds")
    if not _is_int(maximum_rounds) or not 1 <= maximum_rounds <= 2:
        reporter.error(f"{context}.max_rounds must be an integer from 1 through 2")
    elif maximum_rounds < round_limit:
        reporter.error(f"{context}.max_rounds does not authorize round_limit")
    elif maximum_rounds > policy_max_rounds:
        reporter.error(
            f"{context}.max_rounds must not exceed policy max_rounds {policy_max_rounds}"
        )
    if value.get("read_only_required") is not True:
        reporter.error(f"{context}.read_only_required must be true")
    authorized_titles = _validate_string_array(
        value.get("authorized_titles"), f"{context}.authorized_titles", reporter
    )
    if authorized_titles is not None:
        kind = value.get("kind")
        if kind == "explicit_batch" and authorized_titles != expected_titles:
            reporter.error(
                f"{context}.authorized_titles must exactly match the ordered seat titles "
                "for explicit_batch authorization"
            )
        if kind == "scoped_auto" and authorized_titles not in ([], expected_titles):
            reporter.error(
                f"{context}.authorized_titles must be empty or exactly match the "
                "ordered seat titles for scoped_auto authorization"
            )


def _validate_response(
    project: Path,
    response: Any,
    round_number: int,
    seats_by_id: dict[str, dict[str, Any]],
    consultation_id: str,
    context: str,
    reporter: Reporter,
) -> Optional[tuple[str, str, Optional[datetime]]]:
    if not isinstance(response, dict):
        reporter.error(f"{context} must be an object")
        return None
    _require_exact_keys(response, RESPONSE_FIELDS, context, reporter)
    seat_id = response.get("seat_id")
    if seat_id not in seats_by_id:
        reporter.error(f"{context}.seat_id must identify a declared seat")
    expected_response_id = f"{seat_id}.R{round_number}"
    if response.get("response_id") != expected_response_id:
        reporter.error(
            f"{context}.response_id must equal {expected_response_id!r}"
        )
    status = response.get("status")
    if status not in RESPONSE_STATUSES:
        reporter.error(
            f"{context}.status must be one of: {', '.join(sorted(RESPONSE_STATUSES))}"
        )
        status = "__invalid__"
    path_required = status == "received"
    path = response.get("path")
    artifact_created_at: Optional[datetime] = None
    if not path_required and path is not None:
        reporter.error(f"{context}.path must be null unless status is 'received'")
    if path is not None or path_required:
        artifact_path = _safe_public_path(
            project,
            path,
            f"{context}.path",
            consultation_id,
            reporter,
            required=path_required,
        )
        if path_required and artifact_path is not None:
            artifact_created_at = _validate_response_artifact(
                artifact_path,
                str(path),
                consultation_id,
                seats_by_id.get(str(seat_id)),
                str(response.get("response_id")),
                round_number,
                reporter,
            )
    if response.get("callback_id") is not None:
        reporter.error(
            f"{context}.callback_id must be null in public records; store the live "
            "callback ID under .agentdesk/runtime/consultations.yaml"
        )
    return (str(seat_id), str(status), artifact_created_at)


def _validate_rounds(
    project: Path,
    value: Any,
    consultation_id: str,
    topic: str,
    short_topic: str,
    authorization_id: Optional[str],
    seats: list[dict[str, Any]],
    round_limit: int,
    state: str,
    context: str,
    reporter: Reporter,
) -> tuple[dict[str, datetime], dict[int, datetime]]:
    if not isinstance(value, list):
        reporter.error(f"{context} must be an array")
        return {}, {}
    if len(value) > round_limit or len(value) > 2:
        reporter.error(f"{context} exceeds the declared or V1 round limit")
    seats_by_id = {
        str(seat.get("seat_id")): seat
        for seat in seats
        if _nonempty_string(seat.get("seat_id"))
    }
    seen_rounds: set[int] = set()
    coverage: dict[int, dict[str, str]] = {}
    response_created_times: dict[str, datetime] = {}
    brief_created_times: dict[int, datetime] = {}
    for index, raw_round in enumerate(value):
        round_context = f"{context}[{index}]"
        if not isinstance(raw_round, dict):
            reporter.error(f"{round_context} must be an object")
            continue
        round_record = raw_round
        _require_exact_keys(round_record, ROUND_FIELDS, round_context, reporter)
        round_number = round_record.get("round")
        expected_round = index + 1
        if round_number not in {1, 2}:
            reporter.error(f"{round_context}.round must be 1 or 2")
            continue
        if round_number != expected_round:
            reporter.error(
                f"{round_context}.round must be contiguous and equal {expected_round}"
            )
        if round_number in seen_rounds:
            reporter.error(f"duplicate consultation round: {round_number}")
        seen_rounds.add(round_number)
        expected_independent = round_number == 1
        if round_record.get("independent") is not expected_independent:
            reporter.error(
                f"{round_context}.independent must be {expected_independent!r}"
            )
        brief_path = _safe_public_path(
            project,
            round_record.get("brief_path"),
            f"{round_context}.brief_path",
            consultation_id,
            reporter,
            required=True,
        )
        digest = round_record.get("brief_digest")
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            reporter.error(
                f"{round_context}.brief_digest must match 'sha256:<64 lowercase hex>'"
            )
        elif brief_path is not None and brief_path.is_file():
            try:
                actual_digest = "sha256:" + hashlib.sha256(brief_path.read_bytes()).hexdigest()
            except OSError as exc:
                reporter.error(f"cannot hash {round_context}.brief_path: {exc}")
            else:
                if digest != actual_digest:
                    reporter.error(
                        f"{round_context}.brief_digest does not match brief_path bytes"
                    )
            brief_created_at = _validate_brief_artifact(
                brief_path,
                str(round_record.get("brief_path")),
                consultation_id,
                topic,
                short_topic,
                round_limit,
                authorization_id,
                reporter,
            )
            if brief_created_at is not None:
                brief_created_times[round_number] = brief_created_at
        trigger_reason = round_record.get("trigger_reason")
        if not _nonempty_string(trigger_reason):
            reporter.error(f"{round_context}.trigger_reason must be a non-empty string")
        elif (
            PUBLIC_PLACEHOLDER_TOKEN_RE.search(str(trigger_reason))
            or re.search(r"[A-Za-z0-9\u4e00-\u9fff]", str(trigger_reason)) is None
        ):
            reporter.error(
                f"{round_context}.trigger_reason must contain substantive, "
                "non-placeholder text"
            )
        responses = round_record.get("responses")
        if not isinstance(responses, list):
            reporter.error(f"{round_context}.responses must be an array")
            continue
        round_coverage: dict[str, str] = {}
        for response_index, response in enumerate(responses):
            result = _validate_response(
                project,
                response,
                round_number,
                seats_by_id,
                consultation_id,
                f"{round_context}.responses[{response_index}]",
                reporter,
            )
            if result is None:
                continue
            seat_id, response_status, artifact_created_at = result
            if seat_id in round_coverage:
                reporter.error(
                    f"{round_context}.responses contains duplicate seat_id {seat_id}"
                )
            round_coverage[seat_id] = response_status
            response_id = response.get("response_id") if isinstance(response, dict) else None
            if isinstance(response_id, str) and artifact_created_at is not None:
                response_created_times[response_id] = artifact_created_at
        coverage[round_number] = round_coverage

    if state in ROUND_ONE_REQUIRED_STATES and 1 not in seen_rounds:
        reporter.error(f"state {state!r} requires a first-round record")
    if state in ROUND_TWO_REQUIRED_STATES and 2 not in seen_rounds:
        reporter.error(f"state {state!r} requires a second-round record")
    if state not in ROUND_TWO_ALLOWED_STATES and 2 in seen_rounds:
        reporter.error(f"state {state!r} must not contain a second-round record")

    expected_seat_ids = set(seats_by_id)
    if state in ROUND_ONE_COMPLETE_STATES and set(coverage.get(1, {})) != expected_seat_ids:
        reporter.error("a completed first round must contain one response fact per seat")
    if state in ROUND_TWO_COMPLETE_STATES and 2 in seen_rounds:
        if set(coverage.get(2, {})) != expected_seat_ids:
            reporter.error("a completed second round must contain one response fact per seat")
    if state == "cancelled" and 2 in seen_rounds:
        if set(coverage.get(1, {})) != expected_seat_ids:
            reporter.error(
                "a cancelled consultation that reached Round 2 must preserve one completed Round 1 response fact per seat"
            )

    latest_facts: dict[str, str] = {}
    for round_number in sorted(coverage):
        latest_facts.update(coverage[round_number])
    for seat_id, seat in seats_by_id.items():
        seat_status = seat.get("status")
        response_status = latest_facts.get(seat_id)
        expected_status = {
            "received": "responded",
            "missing": "missing",
            "blocked": "blocked",
        }.get(response_status)
        if expected_status is not None and seat_status != expected_status:
            reporter.error(
                f"seat {seat_id} status must be {expected_status!r} after its "
                f"latest response fact is {response_status!r}"
            )
        if seen_rounds and seat_status in {"responded", "missing", "blocked"}:
            inverse_status = {
                "responded": "received",
                "missing": "missing",
                "blocked": "blocked",
            }[str(seat_status)]
            if response_status != inverse_status:
                reporter.error(
                    f"seat {seat_id} status {seat_status!r} requires a matching "
                    "fact in its latest recorded round"
                )
        if not seen_rounds and seat_status in {"prompted", "responded", "missing"}:
            reporter.error(
                f"seat {seat_id} status {seat_status!r} requires a round record"
            )
    return response_created_times, brief_created_times


def _validate_confirmation(
    value: Any,
    consultation_id: str,
    outcome_status: Any,
    context: str,
    reporter: Reporter,
) -> str:
    if not isinstance(value, dict):
        reporter.error(f"{context} must be an object")
        return "__invalid__"
    _require_exact_keys(value, CONFIRMATION_FIELDS, context, reporter)
    status = value.get("status")
    if status not in CONFIRMATION_STATUSES:
        reporter.error(
            f"{context}.status must be one of: "
            f"{', '.join(sorted(CONFIRMATION_STATUSES))}"
        )
        status = "__invalid__"
    confirmed_scope = _validate_string_array(
        value.get("confirmed_scope"), f"{context}.confirmed_scope", reporter
    )
    if status in {"confirmed", "rejected", "deferred"}:
        confirmation_id = value.get("confirmation_id")
        match = (
            CONFIRMATION_ID_RE.fullmatch(str(confirmation_id))
            if _nonempty_string(confirmation_id)
            else None
        )
        if (
            match is None
            or match.group(1) != consultation_id
            or PLACEHOLDER_ID_RE.search(str(confirmation_id)) is not None
        ):
            reporter.error(
                f"{context}.confirmation_id must match CC-{consultation_id}-<4-32 uppercase alphanumerics> without placeholders"
            )
        _validate_actor(value.get("confirmed_by"), f"{context}.confirmed_by", reporter)
        if _parse_rfc3339_utc(value.get("recorded_at")) is None:
            reporter.error(
                f"{context}.recorded_at must be a UTC RFC3339 timestamp for status {status!r}"
            )
        if outcome_status != status:
            reporter.error(
                f"outcome_status must equal confirmation.status when status is {status!r}"
            )
        if status == "confirmed" and not confirmed_scope:
            reporter.error(
                f"{context}.confirmed_scope must contain at least one explicitly "
                "confirmed conclusion"
            )
        if status in {"rejected", "deferred"} and confirmed_scope:
            reporter.error(f"{context}.confirmed_scope must be empty for status {status!r}")
    elif status in {"pending", "not_required"}:
        for field in ("confirmation_id", "confirmed_by", "recorded_at"):
            if value.get(field) is not None:
                reporter.error(f"{context}.{field} must be null for status {status!r}")
        if confirmed_scope:
            reporter.error(f"{context}.confirmed_scope must be empty for status {status!r}")
    if outcome_status in {"confirmed", "rejected", "deferred"} and status != outcome_status:
        reporter.error(
            f"{context}.status must equal outcome_status {outcome_status!r}"
        )
    return str(status)


def _validate_derived_refs(
    project: Path,
    value: Any,
    consultation_id: str,
    confirmation_id: Optional[str],
    confirmation_status: str,
    context: str,
    reporter: Reporter,
) -> bool:
    if not isinstance(value, dict):
        reporter.error(f"{context} must be an object")
        return False
    _require_exact_keys(value, DERIVED_REFS_FIELDS, context, reporter)
    decision_ids = _validate_string_array(
        value.get("decision_ids"), f"{context}.decision_ids", reporter
    )
    task_paths = _validate_string_array(
        value.get("task_card_paths"), f"{context}.task_card_paths", reporter
    )
    has_refs = bool(decision_ids or task_paths)
    if has_refs and confirmation_status != "confirmed":
        reporter.error(
            f"{context} must stay empty until Owner confirmation.status is 'confirmed'"
        )
    if decision_ids:
        decision_path = project / DECISIONS_FILE
        if decision_path.is_symlink() or not decision_path.is_file():
            reporter.error(
                f"{context}.decision_ids requires a regular {DECISIONS_FILE.as_posix()}"
            )
        else:
            try:
                decision_text = decision_path.read_text(encoding="utf-8")
            except OSError as exc:
                reporter.error(f"cannot read {DECISIONS_FILE.as_posix()}: {exc}")
            else:
                rows: dict[str, str] = {}
                for line in decision_text.splitlines():
                    if not line.lstrip().startswith("|"):
                        continue
                    cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
                    if not cells or cells[0] in {"ID", "---", ""}:
                        continue
                    rows[cells[0]] = line
                for decision_id in decision_ids:
                    row = rows.get(decision_id)
                    if row is None:
                        reporter.error(
                            f"{context}.decision_ids references missing decision {decision_id!r}"
                        )
                    elif (
                        not _contains_exact_identifier(row, consultation_id)
                        or not confirmation_id
                        or not _contains_exact_identifier(row, confirmation_id)
                    ):
                        reporter.error(
                            f"decision {decision_id!r} must cite {consultation_id} and its confirmation_id"
                        )
    if task_paths is not None:
        for index, raw_path in enumerate(task_paths):
            logical = PurePosixPath(raw_path)
            prefix = PurePosixPath("docs/pm/tasks")
            if (
                logical.is_absolute()
                or "." in logical.parts
                or ".." in logical.parts
                or logical.parts[: len(prefix.parts)] != prefix.parts
            ):
                reporter.error(
                    f"{context}.task_card_paths[{index}] must be under docs/pm/tasks/"
                )
                continue
            candidate = project.joinpath(*logical.parts)
            if not candidate.is_file() or candidate.is_symlink():
                reporter.error(
                    f"{context}.task_card_paths[{index}] does not identify a regular file"
                )
                continue
            result = _read_frontmatter_artifact(
                candidate,
                raw_path,
                None,
                reporter,
            )
            if result is None:
                continue
            frontmatter, _content = result
            if frontmatter.get("schema_version") != "agentdesk.task-card/v2":
                reporter.error(
                    f"{raw_path} frontmatter schema_version must be 'agentdesk.task-card/v2'"
                )
            if frontmatter.get("source_consultation_id") != consultation_id:
                reporter.error(
                    f"{raw_path} frontmatter source_consultation_id must equal {consultation_id!r}"
                )
            if not confirmation_id or frontmatter.get("source_confirmation_id") != confirmation_id:
                reporter.error(
                    f"{raw_path} frontmatter source_confirmation_id must match the confirmed consultation"
                )
    return has_refs


def _runtime_identifier(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    lowered = str(value).strip().lower()
    return (
        not any(fragment in lowered for fragment in PLACEHOLDER_FRAGMENTS)
        and PUBLIC_PLACEHOLDER_TOKEN_RE.search(str(value)) is None
        and re.search(r"[A-Za-z0-9]", str(value)) is not None
    )


def _runtime_receipt_evidence(
    value: Any, success_statuses: frozenset[str]
) -> bool:
    failure_text = re.compile(
        r"\b(?:cancelled|denied|error|failed|failure|in[_ -]?progress|"
        r"not[_ -]?delivered|pending|processing|queued|rejected|retrying|"
        r"timed?[_ -]?out|unavailable|unknown)\b",
        re.IGNORECASE,
    )

    def normalized_key(raw_key: Any) -> str:
        return str(raw_key).strip().lower().replace("-", "_").replace(" ", "_")

    def explicit_false(raw_value: Any) -> bool:
        if raw_value is False or raw_value == 0:
            return True
        return isinstance(raw_value, str) and raw_value.strip().lower() in {
            "false",
            "no",
            "denied",
            "failed",
            "failure",
        }

    def nested_fields_are_successful(
        node: Any, *, require_nonempty: bool = False
    ) -> bool:
        if isinstance(node, dict):
            if require_nonempty and not node:
                return False
            for raw_key, nested in node.items():
                key = normalized_key(raw_key)
                if key in {"success", "ok", "accepted"}:
                    if not isinstance(nested, bool) or explicit_false(nested):
                        return False
                if key in {"error", "errors", "failure", "failed", "denied"}:
                    if nested not in (None, "", False, 0, [], {}):
                        return False
                if key in {"status", "state", "outcome", "result"}:
                    if not isinstance(nested, str) or not nested.strip():
                        return False
                    normalized = (
                        nested.strip().lower().replace("-", "_").replace(" ", "_")
                    )
                    if (
                        normalized in NON_SUCCESS_EVIDENCE_STATUSES
                        or normalized not in success_statuses
                    ):
                        return False
                elif isinstance(nested, (dict, list)):
                    if not nested_fields_are_successful(nested):
                        return False
                elif isinstance(nested, str) and failure_text.search(nested):
                    return False
            return True
        if isinstance(node, list):
            if require_nonempty and not node:
                return False
            return all(nested_fields_are_successful(item) for item in node)
        if isinstance(node, str):
            return _runtime_identifier(node) and failure_text.search(node) is None
        return node not in (None, False)

    if isinstance(value, str):
        return _runtime_identifier(value) and failure_text.search(value) is None
    if isinstance(value, dict):
        return nested_fields_are_successful(value, require_nonempty=True)
    return False


def _validate_runtime_endpoint(
    value: Any,
    context: str,
    reporter: Reporter,
    expected_title: Optional[str] = None,
) -> Optional[str]:
    if not isinstance(value, dict):
        reporter.error(f"{context} must be an object")
        return None
    fields = RUNTIME_SEAT_FIELDS if expected_title is not None else RUNTIME_SOURCE_FIELDS
    _require_exact_keys(value, fields, context, reporter)
    if expected_title is not None:
        if value.get("expected_title") != expected_title:
            reporter.error(f"{context}.expected_title must equal {expected_title!r}")
        if value.get("actual_title") != expected_title:
            reporter.error(
                f"{context}.actual_title must equal the verified title {expected_title!r}"
            )
        if _parse_rfc3339_utc(value.get("created_at")) is None:
            reporter.error(
                f"{context}.created_at must be a UTC RFC3339 timestamp"
            )
        if not _runtime_receipt_evidence(
            value.get("creation_evidence"), CREATION_EVIDENCE_SUCCESS_STATUSES
        ):
            reporter.error(
                f"{context}.creation_evidence must contain terminal provider "
                "creation evidence"
            )
        model_binding = value.get("model_binding")
        if not isinstance(model_binding, dict):
            reporter.error(f"{context}.model_binding must be an object")
        else:
            _require_exact_keys(
                model_binding,
                MODEL_BINDING_FIELDS,
                f"{context}.model_binding",
                reporter,
            )
            requested = model_binding.get("requested_model")
            actual = model_binding.get("actual_model")
            binding_status = model_binding.get("status")
            if requested is not None and not _runtime_identifier(requested):
                reporter.error(
                    f"{context}.model_binding.requested_model must be null or a concrete model name"
                )
            if requested is None and model_binding.get("request_source") is not None:
                reporter.error(
                    f"{context}.model_binding.request_source must be null without an exact request"
                )
            if requested is not None and model_binding.get("request_source") != "user_explicit":
                reporter.error(
                    f"{context}.model_binding.request_source must be 'user_explicit' for an override"
                )
            if binding_status not in {
                "default_unverified",
                "override_unverified",
                "verified",
            }:
                reporter.error(
                    f"{context}.model_binding.status must be default_unverified, override_unverified, or verified"
                )
            if binding_status == "default_unverified" and requested is not None:
                reporter.error(
                    f"{context}.model_binding.default_unverified must not contain requested_model"
                )
            if binding_status == "override_unverified" and requested is None:
                reporter.error(
                    f"{context}.model_binding.override_unverified requires requested_model"
                )
            if binding_status == "override_unverified" and requested is not None:
                reporter.error(
                    f"{context}.model_binding exact user request remains unverified; "
                    "strong runtime closure requires status 'verified'"
                )
            if binding_status == "verified":
                if not _runtime_identifier(requested):
                    reporter.error(
                        f"{context}.model_binding.verified requires an exact "
                        "user-requested model"
                    )
                if not _runtime_identifier(actual):
                    reporter.error(
                        f"{context}.model_binding.actual_model must identify the observed model when verified"
                    )
                if not _runtime_receipt_evidence(
                    model_binding.get("provider_evidence"),
                    MODEL_EVIDENCE_SUCCESS_STATUSES,
                ):
                    reporter.error(
                        f"{context}.model_binding.provider_evidence is required when verified"
                    )
                if actual != requested:
                    reporter.error(
                        f"{context}.model_binding.actual_model must equal the exact user-requested model when verified"
                    )
            else:
                if actual is not None or model_binding.get("provider_evidence") is not None:
                    reporter.error(
                        f"{context}.model_binding must keep actual_model/provider_evidence null while unverified"
                    )
    if value.get("status") != "verified":
        reporter.error(f"{context}.status must be 'verified'")
    if _parse_rfc3339_utc(value.get("verified_at")) is None:
        reporter.error(f"{context}.verified_at must be a UTC RFC3339 timestamp")
    for field in ("thread_id", "host_id"):
        if not _runtime_identifier(value.get(field)):
            reporter.error(f"{context}.{field} must identify a verified live route")
    thread_id = value.get("thread_id")
    return str(thread_id) if _runtime_identifier(thread_id) else None


def _validate_prompt_receipt(
    value: Any,
    key: str,
    seat_id: str,
    round_number: int,
    source_thread_id: Optional[str],
    seat_thread_id: Optional[str],
    expected_brief_digest: Any,
    context: str,
    reporter: Reporter,
) -> Optional[datetime]:
    if not isinstance(value, dict):
        reporter.error(f"{context} must be an object")
        return None
    _require_exact_keys(value, PROMPT_RECEIPT_FIELDS, context, reporter)
    if not _runtime_identifier(value.get("prompt_id")):
        reporter.error(f"{context}.prompt_id must be a non-placeholder identifier")
    if value.get("seat_id") != seat_id:
        reporter.error(f"{context}.seat_id must equal {seat_id!r}")
    if value.get("round") != round_number:
        reporter.error(f"{context}.round must equal {round_number}")
    if source_thread_id is not None and value.get("source_thread_id") != source_thread_id:
        reporter.error(f"{context}.source_thread_id must equal the Leader source route")
    if seat_thread_id is not None and value.get("destination_thread_id") != seat_thread_id:
        reporter.error(f"{context}.destination_thread_id must equal the seat route")
    if (
        not isinstance(expected_brief_digest, str)
        or DIGEST_RE.fullmatch(expected_brief_digest) is None
        or value.get("brief_digest") != expected_brief_digest
    ):
        reporter.error(
            f"{context}.brief_digest must equal the public round brief_digest"
        )
    if value.get("status") not in {"sent", "acknowledged"}:
        reporter.error(f"{context}.status must be 'sent' or 'acknowledged'")
    if not _runtime_receipt_evidence(
        value.get("provider_receipt"), PROMPT_EVIDENCE_SUCCESS_STATUSES
    ):
        reporter.error(f"{context}.provider_receipt must contain provider evidence")
    sent_at = _parse_rfc3339_utc(value.get("sent_at"))
    if sent_at is None:
        reporter.error(f"{context}.sent_at must be a UTC RFC3339 timestamp")
    if key != f"{seat_id}.R{round_number}":
        reporter.error(
            f"{context} key must equal {seat_id}.R{round_number!s}"
        )
    return sent_at


def _validate_response_receipt(
    value: Any,
    key: str,
    response_id: str,
    seat_id: str,
    round_number: int,
    source_thread_id: Optional[str],
    seat_thread_id: Optional[str],
    expected_prompt_id: Optional[str],
    prompt_sent_at: Optional[datetime],
    expected_response_digest: Optional[str],
    context: str,
    reporter: Reporter,
) -> Optional[datetime]:
    if not isinstance(value, dict):
        reporter.error(f"{context} must be an object")
        return None
    _require_exact_keys(value, RESPONSE_RECEIPT_FIELDS, context, reporter)
    callback_id = value.get("callback_id")
    if not _runtime_identifier(callback_id):
        reporter.error(f"{context}.callback_id must be a non-placeholder identifier")
    elif key != callback_id:
        reporter.error(f"{context} key must equal callback_id")
    if value.get("response_id") != response_id:
        reporter.error(f"{context}.response_id must equal {response_id!r}")
    if expected_prompt_id is None or value.get("prompt_id") != expected_prompt_id:
        reporter.error(f"{context}.prompt_id must equal its matching prompt receipt")
    if value.get("seat_id") != seat_id:
        reporter.error(f"{context}.seat_id must equal {seat_id!r}")
    if value.get("round") != round_number:
        reporter.error(f"{context}.round must equal {round_number}")
    if seat_thread_id is not None and value.get("source_thread_id") != seat_thread_id:
        reporter.error(f"{context}.source_thread_id must equal the seat route")
    if source_thread_id is not None and value.get("destination_thread_id") != source_thread_id:
        reporter.error(f"{context}.destination_thread_id must equal the Leader source route")
    if (
        expected_response_digest is None
        or value.get("response_digest") != expected_response_digest
    ):
        reporter.error(
            f"{context}.response_digest must equal the public response artifact digest"
        )
    if value.get("status") != "received":
        reporter.error(f"{context}.status must be 'received'")
    if not _runtime_receipt_evidence(
        value.get("provider_receipt"), RESPONSE_EVIDENCE_SUCCESS_STATUSES
    ):
        reporter.error(f"{context}.provider_receipt must contain provider evidence")
    received_at = _parse_rfc3339_utc(value.get("received_at"))
    if received_at is None:
        reporter.error(f"{context}.received_at must be a UTC RFC3339 timestamp")
    elif prompt_sent_at is not None and received_at < prompt_sent_at:
        reporter.error(f"{context}.received_at must not precede its prompt sent_at")
    return received_at


def _load_execution_route_threads(
    project: Path, reporter: Reporter
) -> tuple[dict[str, str], Optional[str]]:
    """Load enough execution-route identity to prevent seat/Worker reuse."""

    path = project / EXECUTION_ROUTES_FILE
    if not path.exists() and not path.is_symlink():
        return {}, None
    if path.is_symlink() or not path.is_file():
        reporter.error(
            f"{EXECUTION_ROUTES_FILE.as_posix()} must be a regular non-symlink file"
        )
        return {}, None
    value = _load_json_object(path, EXECUTION_ROUTES_FILE.as_posix(), reporter)
    if value is None:
        return {}, None
    routes = value.get("routes")
    if not isinstance(routes, dict):
        reporter.error(f"{EXECUTION_ROUTES_FILE.as_posix()}.routes must be an object")
        return {}, None
    threads: dict[str, str] = {}
    pm_thread: Optional[str] = None
    for role_id, route in routes.items():
        if not isinstance(route, dict):
            continue
        role_no = route.get("role_no")
        if isinstance(role_no, str) and CONSULTATION_ROLE_NO_RE.fullmatch(role_no):
            reporter.error(
                f"execution role {role_id!r} uses reserved consultation seat number {role_no!r}"
            )
        thread_id = route.get("thread_id")
        if not _runtime_identifier(thread_id):
            continue
        thread = str(thread_id)
        threads[thread] = str(role_id)
        if role_id == "PM":
            pm_thread = thread
    return threads, pm_thread


def _validate_runtime_consultation(
    project: Path,
    public_record: dict[str, Any],
    value: Any,
    context: str,
    execution_threads: dict[str, str],
    pm_thread: Optional[str],
    runtime_updated_at: Optional[datetime],
    reporter: Reporter,
) -> None:
    if not isinstance(value, dict):
        reporter.error(f"{context} must be an object")
        return
    _require_exact_keys(value, RUNTIME_CONSULTATION_FIELDS, context, reporter)
    authorization = public_record.get("creation_authorization")
    authorization_granted_at = (
        _parse_rfc3339_utc(authorization.get("granted_at"))
        if isinstance(authorization, dict)
        else None
    )
    confirmation = public_record.get("confirmation")
    confirmation_recorded_at = (
        _parse_rfc3339_utc(confirmation.get("recorded_at"))
        if isinstance(confirmation, dict)
        else None
    )
    public_updated_at = _parse_rfc3339_utc(public_record.get("updated_at"))
    synthesis_created_at = _read_artifact_created_at(
        project, public_record.get("synthesis_path")
    )
    source_thread_id = _validate_runtime_endpoint(
        value.get("source"), f"{context}.source", reporter
    )
    source_value = value.get("source")
    source_verified_at = (
        _parse_rfc3339_utc(source_value.get("verified_at"))
        if isinstance(source_value, dict)
        else None
    )
    if (
        source_verified_at is not None
        and runtime_updated_at is not None
        and source_verified_at > runtime_updated_at
    ):
        reporter.error(f"{context}.source.verified_at must not follow runtime updated_at")
    if (
        source_verified_at is not None
        and authorization_granted_at is not None
        and source_verified_at < authorization_granted_at
    ):
        reporter.error(
            f"{context}.source.verified_at must not precede creation authorization"
        )
    if (
        source_verified_at is not None
        and public_updated_at is not None
        and source_verified_at > public_updated_at
    ):
        reporter.error(
            f"{context}.source.verified_at must not follow public record updated_at"
        )
    if (
        source_thread_id is not None
        and source_thread_id in execution_threads
        and source_thread_id != pm_thread
    ):
        reporter.error(
            f"{context}.source.thread_id reuses execution worker role "
            f"{execution_threads[source_thread_id]!r}; only the PM route may be the Leader source"
        )
    runtime_seats = value.get("seats")
    if not isinstance(runtime_seats, dict):
        reporter.error(f"{context}.seats must be an object")
        runtime_seats = {}
    if public_record.get("state") in {"draft", "awaiting_authorization"}:
        if runtime_seats:
            reporter.error(
                f"{context}.seats must stay empty before consultation creation authorization"
            )
        for field in ("prompt_receipts", "response_receipts"):
            runtime_facts = value.get(field)
            if isinstance(runtime_facts, dict) and runtime_facts:
                reporter.error(f"{context}.{field} must stay empty before authorization")

    public_seats = public_record.get("seats")
    public_seats = public_seats if isinstance(public_seats, list) else []
    public_seats_by_id = {
        str(seat.get("seat_id")): seat
        for seat in public_seats
        if isinstance(seat, dict) and _nonempty_string(seat.get("seat_id"))
    }
    for runtime_seat_id in runtime_seats:
        if not isinstance(runtime_seat_id, str):
            reporter.error(f"{context}.seats keys must be strings")
        elif runtime_seat_id not in public_seats_by_id:
            reporter.error(
                f"{context}.seats contains orphan seat {runtime_seat_id!r}"
            )
    public_rounds = public_record.get("rounds")
    has_public_rounds = isinstance(public_rounds, list) and bool(public_rounds)
    seat_threads: dict[str, Optional[str]] = {}
    seat_created_times: dict[str, Optional[datetime]] = {}
    seat_verified_times: dict[str, Optional[datetime]] = {}
    seen_thread_ids: dict[str, str] = {}
    if source_thread_id is not None:
        seen_thread_ids[source_thread_id] = "Leader source"
    for seat_id, seat in public_seats_by_id.items():
        needs_route = seat.get("status") in {
            "created",
            "prompted",
            "responded",
            "missing",
        } or has_public_rounds
        route = runtime_seats.get(seat_id)
        if not needs_route and route is None:
            continue
        thread_id = _validate_runtime_endpoint(
            route,
            f"{context}.seats[{seat_id}]",
            reporter,
            expected_title=str(seat.get("expected_thread_title")),
        )
        seat_threads[seat_id] = thread_id
        seat_created_times[seat_id] = (
            _parse_rfc3339_utc(route.get("created_at"))
            if isinstance(route, dict)
            else None
        )
        seat_verified_times[seat_id] = (
            _parse_rfc3339_utc(route.get("verified_at"))
            if isinstance(route, dict)
            else None
        )
        if (
            seat_created_times[seat_id] is not None
            and runtime_updated_at is not None
            and seat_created_times[seat_id] > runtime_updated_at
        ):
            reporter.error(
                f"{context}.seats[{seat_id}].created_at must not follow runtime updated_at"
            )
        if (
            seat_created_times[seat_id] is not None
            and public_updated_at is not None
            and seat_created_times[seat_id] > public_updated_at
        ):
            reporter.error(
                f"{context}.seats[{seat_id}].created_at must not follow public record updated_at"
            )
        if (
            seat_created_times[seat_id] is not None
            and authorization_granted_at is not None
            and seat_created_times[seat_id] < authorization_granted_at
        ):
            reporter.error(
                f"{context}.seats[{seat_id}].created_at must not precede "
                "creation authorization"
            )
        if (
            seat_created_times[seat_id] is not None
            and seat_verified_times[seat_id] is not None
            and seat_created_times[seat_id] > seat_verified_times[seat_id]
        ):
            reporter.error(
                f"{context}.seats[{seat_id}].created_at must not follow verified_at"
            )
        if (
            seat_verified_times[seat_id] is not None
            and runtime_updated_at is not None
            and seat_verified_times[seat_id] > runtime_updated_at
        ):
            reporter.error(
                f"{context}.seats[{seat_id}].verified_at must not follow runtime updated_at"
            )
        if (
            seat_verified_times[seat_id] is not None
            and public_updated_at is not None
            and seat_verified_times[seat_id] > public_updated_at
        ):
            reporter.error(
                f"{context}.seats[{seat_id}].verified_at must not follow public record updated_at"
            )
        if (
            seat_verified_times[seat_id] is not None
            and authorization_granted_at is not None
            and seat_verified_times[seat_id] < authorization_granted_at
        ):
            reporter.error(
                f"{context}.seats[{seat_id}].verified_at must not precede "
                "creation authorization"
            )
        if thread_id is not None:
            if thread_id in execution_threads:
                reporter.error(
                    f"{context}.seats[{seat_id}].thread_id reuses execution role "
                    f"{execution_threads[thread_id]!r}"
                )
            if thread_id in seen_thread_ids:
                reporter.error(
                    f"{context}.seats[{seat_id}].thread_id must be distinct from "
                    f"{seen_thread_ids[thread_id]}"
                )
            else:
                seen_thread_ids[thread_id] = seat_id

    prompt_receipts = value.get("prompt_receipts")
    if not isinstance(prompt_receipts, dict):
        reporter.error(f"{context}.prompt_receipts must be an object")
        prompt_receipts = {}
    response_receipts = value.get("response_receipts")
    if not isinstance(response_receipts, dict):
        reporter.error(f"{context}.response_receipts must be an object")
        response_receipts = {}

    rounds = public_rounds if isinstance(public_rounds, list) else []
    brief_created_times = {
        int(round_record["round"]): created_at
        for round_record in rounds
        if isinstance(round_record, dict)
        and round_record.get("round") in {1, 2}
        and (
            created_at := _read_artifact_created_at(
                project, round_record.get("brief_path")
            )
        )
        is not None
    }
    round_brief_digests = {
        int(round_record["round"]): round_record.get("brief_digest")
        for round_record in rounds
        if isinstance(round_record, dict)
        and round_record.get("round") in {1, 2}
    }
    allowed_prompt_keys: set[str] = set()
    required_prompt_keys: set[str] = set()
    public_responses: dict[
        str,
        tuple[
            str,
            int,
            str,
            Optional[str],
            Optional[datetime],
            Optional[str],
        ],
    ] = {}
    valid_round_numbers = [
        int(round_record["round"])
        for round_record in rounds
        if isinstance(round_record, dict) and round_record.get("round") in {1, 2}
    ]
    latest_round_number = max(valid_round_numbers, default=0)
    for round_record in rounds:
        if not isinstance(round_record, dict) or round_record.get("round") not in {1, 2}:
            continue
        round_number = int(round_record["round"])
        responses = round_record.get("responses")
        responses = responses if isinstance(responses, list) else []
        response_seat_ids: set[str] = set()
        for response in responses:
            if not isinstance(response, dict):
                continue
            response_id = str(response.get("response_id"))
            seat_id = str(response.get("seat_id"))
            response_seat_ids.add(seat_id)
            transport_mode: Optional[str] = None
            artifact_created_at: Optional[datetime] = None
            response_digest: Optional[str] = None
            response_path = response.get("path")
            if response.get("status") == "received" and _nonempty_string(response_path):
                logical = PurePosixPath(str(response_path))
                if not logical.is_absolute() and ".." not in logical.parts:
                    candidate = project.joinpath(*logical.parts)
                    if candidate.is_file() and not candidate.is_symlink():
                        try:
                            response_digest = "sha256:" + hashlib.sha256(
                                candidate.read_bytes()
                            ).hexdigest()
                        except OSError:
                            response_digest = None
                        artifact = _read_frontmatter_artifact(
                            candidate,
                            str(response_path),
                            RESPONSE_ARTIFACT_FIELDS,
                            reporter,
                        )
                        if artifact is not None:
                            transport_mode = str(artifact[0].get("transport_mode"))
                            artifact_created_at = _parse_rfc3339_utc(
                                artifact[0].get("created_at")
                            )
            public_responses[response_id] = (
                seat_id,
                round_number,
                str(response.get("status")),
                transport_mode,
                artifact_created_at,
                response_digest,
            )
        round_prompt_keys = {
            f"{seat_id}.R{round_number}" for seat_id in public_seats_by_id
        }
        allowed_prompt_keys.update(round_prompt_keys)
        if (
            public_record.get("state") == "cancelled"
            and round_number == latest_round_number
        ):
            required_prompt_keys.update(
                f"{seat_id}.R{round_number}" for seat_id in response_seat_ids
            )
        else:
            required_prompt_keys.update(round_prompt_keys)

    actual_prompt_keys = {
        str(key) for key in prompt_receipts if isinstance(key, str)
    }
    for key in prompt_receipts:
        if not isinstance(key, str):
            reporter.error(f"{context}.prompt_receipts keys must be strings")
    for key in sorted(actual_prompt_keys - allowed_prompt_keys):
        reporter.error(f"{context}.prompt_receipts contains orphan receipt {key!r}")
    for key in sorted(required_prompt_keys - actual_prompt_keys):
        reporter.error(f"{context}.prompt_receipts is missing required receipt {key!r}")

    sent_times: dict[str, Optional[datetime]] = {}
    prompt_ids: dict[str, Optional[str]] = {}
    seen_prompt_ids: set[str] = set()
    for receipt_key in sorted(allowed_prompt_keys & actual_prompt_keys):
        receipt = prompt_receipts.get(receipt_key)
        seat_id, round_text = receipt_key.rsplit(".R", 1)
        round_number = int(round_text)
        sent_times[receipt_key] = _validate_prompt_receipt(
            receipt,
            receipt_key,
            seat_id,
            round_number,
            source_thread_id,
            seat_threads.get(seat_id),
            round_brief_digests.get(round_number),
            f"{context}.prompt_receipts[{receipt_key}]",
            reporter,
        )
        prompt_ids[receipt_key] = (
            str(receipt.get("prompt_id"))
            if isinstance(receipt, dict)
            and _runtime_identifier(receipt.get("prompt_id"))
            else None
        )
        sent_at = sent_times[receipt_key]
        for verified_at, label in (
            (source_verified_at, "Leader source verified_at"),
            (seat_verified_times.get(seat_id), "seat verified_at"),
        ):
            if sent_at is not None and verified_at is not None and sent_at < verified_at:
                reporter.error(
                    f"{context}.prompt_receipts[{receipt_key}].sent_at must not precede {label}"
                )
        if sent_at is not None and runtime_updated_at is not None and sent_at > runtime_updated_at:
            reporter.error(
                f"{context}.prompt_receipts[{receipt_key}].sent_at must not follow runtime updated_at"
            )
        if (
            sent_at is not None
            and public_updated_at is not None
            and sent_at > public_updated_at
        ):
            reporter.error(
                f"{context}.prompt_receipts[{receipt_key}].sent_at must not follow "
                "public record updated_at"
            )
        if (
            sent_at is not None
            and confirmation_recorded_at is not None
            and sent_at > confirmation_recorded_at
        ):
            reporter.error(
                f"{context}.prompt_receipts[{receipt_key}].sent_at must not follow "
                "Owner confirmation"
            )
        if (
            sent_at is not None
            and synthesis_created_at is not None
            and sent_at > synthesis_created_at
        ):
            reporter.error(
                f"{context}.prompt_receipts[{receipt_key}].sent_at must not follow "
                "synthesis created_at"
            )
        brief_created_at = brief_created_times.get(round_number)
        if (
            sent_at is not None
            and brief_created_at is not None
            and sent_at < brief_created_at
        ):
            reporter.error(
                f"{context}.prompt_receipts[{receipt_key}].sent_at must not "
                "precede its round brief created_at"
            )
        if isinstance(receipt, dict) and _runtime_identifier(receipt.get("prompt_id")):
            prompt_id = str(receipt.get("prompt_id"))
            if prompt_id in seen_prompt_ids:
                reporter.error(f"duplicate runtime prompt_id: {prompt_id}")
            seen_prompt_ids.add(prompt_id)

    receipts_by_response: dict[str, list[tuple[str, Any]]] = {}
    for key, receipt in response_receipts.items():
        if not isinstance(key, str):
            reporter.error(f"{context}.response_receipts keys must be strings")
            continue
        response_id = receipt.get("response_id") if isinstance(receipt, dict) else None
        if not isinstance(response_id, str) or response_id not in public_responses:
            reporter.error(
                f"{context}.response_receipts contains orphan or malformed receipt {key!r}"
            )
            continue
        receipts_by_response.setdefault(response_id, []).append((key, receipt))

    received_times_by_round: dict[int, list[datetime]] = {1: [], 2: []}
    for response_id, (
        seat_id,
        round_number,
        response_status,
        transport_mode,
        artifact_created_at,
        response_digest,
    ) in public_responses.items():
        matches = receipts_by_response.get(response_id, [])
        if response_status == "received":
            if transport_mode != "proactive_callback":
                reporter.error(
                    f"{context} cannot certify proactive callback closure for "
                    f"{response_id!r} because its public transport_mode is {transport_mode!r}"
                )
            if len(matches) != 1:
                reporter.error(
                    f"{context}.response_receipts must contain exactly one received "
                    f"receipt for public response_id {response_id!r}"
                )
                continue
            key, receipt = matches[0]
            received_at = _validate_response_receipt(
                receipt,
                key,
                response_id,
                seat_id,
                round_number,
                source_thread_id,
                seat_threads.get(seat_id),
                prompt_ids.get(f"{seat_id}.R{round_number}"),
                sent_times.get(f"{seat_id}.R{round_number}"),
                response_digest,
                f"{context}.response_receipts[{key}]",
                reporter,
            )
            if received_at is not None:
                received_times_by_round.setdefault(round_number, []).append(received_at)
            prompt_sent_at = sent_times.get(f"{seat_id}.R{round_number}")
            if (
                artifact_created_at is not None
                and prompt_sent_at is not None
                and artifact_created_at < prompt_sent_at
            ):
                reporter.error(
                    f"{context} response artifact {response_id} created_at must not "
                    "precede its prompt sent_at"
                )
            if (
                artifact_created_at is not None
                and received_at is not None
                and artifact_created_at < received_at
            ):
                reporter.error(
                    f"{context} response artifact {response_id} created_at must not "
                    "precede its callback received_at"
                )
            if (
                received_at is not None
                and runtime_updated_at is not None
                and received_at > runtime_updated_at
            ):
                reporter.error(
                    f"{context}.response_receipts[{key}].received_at must not follow runtime updated_at"
                )
            if (
                received_at is not None
                and public_updated_at is not None
                and received_at > public_updated_at
            ):
                reporter.error(
                    f"{context}.response_receipts[{key}].received_at must not follow "
                    "public record updated_at"
                )
            if (
                received_at is not None
                and confirmation_recorded_at is not None
                and received_at > confirmation_recorded_at
            ):
                reporter.error(
                    f"{context}.response_receipts[{key}].received_at must not follow "
                    "Owner confirmation"
                )
            if (
                received_at is not None
                and synthesis_created_at is not None
                and received_at > synthesis_created_at
            ):
                reporter.error(
                    f"{context}.response_receipts[{key}].received_at must not follow "
                    "synthesis created_at"
                )
        elif matches:
            reporter.error(
                f"{context}.response_receipts must not claim receipt for public "
                f"{response_status} response {response_id!r}"
            )

    round_one_received = received_times_by_round.get(1, [])
    round_two_prompt_times = [
        sent_at
        for key, sent_at in sent_times.items()
        if key.endswith(".R2") and sent_at is not None
    ]
    if (
        round_one_received
        and round_two_prompt_times
        and min(round_two_prompt_times) < max(round_one_received)
    ):
        reporter.error(
            f"{context} Round 2 prompts must not precede completion of all "
            "received Round 1 callbacks"
        )
    if 2 in brief_created_times and round_one_received:
        if brief_created_times[2] < max(round_one_received):
            reporter.error(
                f"{context} Round 2 brief created_at must not precede completion "
                "of all received Round 1 callbacks"
            )


def _validate_runtime(
    project: Path, public_records: list[dict[str, Any]], reporter: Reporter
) -> None:
    context = RUNTIME_FILE.as_posix()
    path = project / RUNTIME_FILE
    if path.is_symlink() or not path.is_file():
        reporter.error(f"{context} is required as a regular non-symlink file")
        return
    value = _load_json_object(path, context, reporter)
    if value is None:
        return
    starting_errors = reporter.errors
    _require_exact_keys(value, RUNTIME_FIELDS, context, reporter)
    if value.get("schema_version") != RUNTIME_SCHEMA_VERSION:
        reporter.error(f"{context}.schema_version must be {RUNTIME_SCHEMA_VERSION!r}")
    consultations = value.get("consultations")
    if not isinstance(consultations, dict):
        reporter.error(f"{context}.consultations must be an object")
        return
    if consultations and _parse_rfc3339_utc(value.get("updated_at")) is None:
        reporter.error(
            f"{context}.updated_at must be a UTC RFC3339 timestamp when runtime records exist"
        )
    if not consultations and value.get("updated_at") is not None:
        if _parse_rfc3339_utc(value.get("updated_at")) is None:
            reporter.error(f"{context}.updated_at must be null or a UTC RFC3339 timestamp")
    runtime_updated_at = _parse_rfc3339_utc(value.get("updated_at"))

    execution_threads, pm_thread = _load_execution_route_threads(project, reporter)

    global_sources: dict[str, str] = {}
    global_seat_threads: dict[str, str] = {}
    for runtime_consultation_id, runtime_record in consultations.items():
        if not isinstance(runtime_record, dict):
            continue
        source = runtime_record.get("source")
        if isinstance(source, dict) and _runtime_identifier(source.get("thread_id")):
            global_sources[str(source.get("thread_id"))] = str(runtime_consultation_id)
        runtime_seats = runtime_record.get("seats")
        if not isinstance(runtime_seats, dict):
            continue
        for seat_id, route in runtime_seats.items():
            if not isinstance(route, dict) or not _runtime_identifier(route.get("thread_id")):
                continue
            thread_id = str(route.get("thread_id"))
            identity = f"{runtime_consultation_id}/{seat_id}"
            previous = global_seat_threads.get(thread_id)
            if previous is not None and previous != identity:
                reporter.error(
                    f"consultation seats {previous} and {identity} reuse thread_id {thread_id}"
                )
            global_seat_threads[thread_id] = identity
    for thread_id, identity in global_seat_threads.items():
        if thread_id in global_sources:
            reporter.error(
                f"consultation seat {identity} reuses a Leader source thread for "
                f"consultation {global_sources[thread_id]}"
            )

    public_ids: set[str] = set()
    for record in public_records:
        consultation_id = record.get("consultation_id")
        if not isinstance(consultation_id, str):
            continue
        public_ids.add(consultation_id)
        seats = record.get("seats")
        seats = seats if isinstance(seats, list) else []
        needs_runtime = bool(record.get("rounds")) or any(
            isinstance(seat, dict)
            and seat.get("status") in {
                "created",
                "prompted",
                "responded",
                "missing",
                "blocked",
            }
            for seat in seats
        )
        runtime_record = consultations.get(consultation_id)
        if runtime_record is None:
            if needs_runtime:
                reporter.error(
                    f"{context}.consultations is missing active consultation {consultation_id}"
                )
            continue
        _validate_runtime_consultation(
            project,
            record,
            runtime_record,
            f"{context}.consultations[{consultation_id}]",
            execution_threads,
            pm_thread,
            runtime_updated_at,
            reporter,
        )
    for consultation_id in sorted(set(consultations) - public_ids):
        reporter.error(
            f"runtime consultation {consultation_id} has no matching public record"
        )
    if reporter.errors == starting_errors:
        reporter.passed(f"validated local consultation runtime evidence in {context}")


def _validate_record(
    project: Path,
    directory: Path,
    record: dict[str, Any],
    policy: dict[str, Any],
    seen_ids: set[str],
    reporter: Reporter,
) -> None:
    context = (directory / "consultation.yaml").relative_to(project).as_posix()
    starting_errors = reporter.errors
    _require_exact_keys(record, RECORD_FIELDS, context, reporter)
    _reject_runtime_fields(record, context, reporter)
    _reject_public_sensitive_strings(record, context, reporter)
    if record.get("schema_version") != RECORD_SCHEMA_VERSION:
        reporter.error(f"{context}.schema_version must be {RECORD_SCHEMA_VERSION!r}")

    consultation_id = record.get("consultation_id")
    if not isinstance(consultation_id, str) or CONSULTATION_ID_RE.fullmatch(consultation_id) is None:
        reporter.error(f"{context}.consultation_id must match C followed by exactly three digits")
        consultation_id = directory.name
    if consultation_id != directory.name:
        reporter.error(
            f"{context}.consultation_id must equal directory name {directory.name!r}"
        )
    if consultation_id in seen_ids:
        reporter.error(f"duplicate consultation_id: {consultation_id}")
    seen_ids.add(str(consultation_id))

    topic = record.get("topic")
    if not _nonplaceholder_public_string(topic):
        reporter.error(f"{context}.topic must be a non-placeholder string")
        topic = ""
    short_topic = record.get("short_topic")
    if not _nonplaceholder_public_string(short_topic):
        reporter.error(f"{context}.short_topic must be a non-placeholder string")
        short_topic = ""
    if any(character in str(short_topic) for character in ("\n", "\r", "\uff5c", ".")):
        reporter.error(
            f"{context}.short_topic must be one line and must not contain '.' or '｜'"
        )

    state = record.get("state")
    if state not in CONSULTATION_STATES:
        reporter.error(
            f"{context}.state must be one of: {', '.join(sorted(CONSULTATION_STATES))}"
        )
        state = "__invalid__"
    created_at = _parse_rfc3339_utc(record.get("created_at"))
    updated_at = _parse_rfc3339_utc(record.get("updated_at"))
    if created_at is None:
        reporter.error(f"{context}.created_at must be a UTC RFC3339 timestamp")
    if updated_at is None:
        reporter.error(f"{context}.updated_at must be a UTC RFC3339 timestamp")
    if created_at is not None and updated_at is not None and updated_at < created_at:
        reporter.error(f"{context}.updated_at must not precede created_at")
    cancellation_reason = record.get("cancellation_reason")
    if state == "cancelled":
        if not _nonempty_string(cancellation_reason):
            reporter.error(
                f"{context}.cancellation_reason must explain why the consultation was cancelled"
            )
        elif (
            PUBLIC_PLACEHOLDER_TOKEN_RE.search(str(cancellation_reason))
            or re.search(r"[A-Za-z0-9\u4e00-\u9fff]", str(cancellation_reason)) is None
        ):
            reporter.error(
                f"{context}.cancellation_reason must contain substantive, "
                "non-placeholder text"
            )
    elif cancellation_reason is not None:
        reporter.error(f"{context}.cancellation_reason must be null unless state is 'cancelled'")

    declared_count = record.get("seat_count")
    if not _is_int(declared_count):
        reporter.error(f"{context}.seat_count must be an integer")
    round_limit = record.get("round_limit")
    policy_rounds = policy.get("max_rounds")
    maximum_rounds = policy_rounds if _is_int(policy_rounds) else 2
    if not _is_int(round_limit) or not 1 <= round_limit <= min(2, maximum_rounds):
        reporter.error(
            f"{context}.round_limit must be an integer from 1 through {min(2, maximum_rounds)}"
        )
        safe_round_limit = 2
    else:
        safe_round_limit = round_limit
    policy_seats = policy.get("max_seats")
    maximum_seats = policy_seats if _is_int(policy_seats) else 3
    seats, expected_titles = _validate_seats(
        record.get("seats"),
        str(consultation_id),
        str(short_topic),
        declared_count,
        min(3, maximum_seats),
        f"{context}.seats",
        reporter,
    )

    visible_activity = any(
        seat.get("status") not in {"planned", "cancelled"} for seat in seats
    )
    authorization_required = state in AUTH_REQUIRED_STATES or visible_activity or bool(record.get("rounds"))
    _validate_authorization(
        record.get("creation_authorization"),
        str(consultation_id),
        str(topic),
        expected_titles,
        len(seats),
        safe_round_limit,
        min(3, maximum_seats),
        min(2, maximum_rounds),
        authorization_required,
        f"{context}.creation_authorization",
        reporter,
    )
    if isinstance(record.get("creation_authorization"), dict):
        granted_at = _parse_rfc3339_utc(
            record["creation_authorization"].get("granted_at")
        )
        if granted_at is not None and created_at is not None and granted_at < created_at:
            reporter.error(f"{context}.creation_authorization.granted_at must not precede created_at")
        if granted_at is not None and updated_at is not None and granted_at > updated_at:
            reporter.error(f"{context}.updated_at must not precede authorization granted_at")

    authorization_value = record.get("creation_authorization")
    if state in {"draft", "awaiting_authorization"} and authorization_value is not None:
        reporter.error(
            f"{context}.creation_authorization must remain null while state is {state!r}"
        )

    allowed_seat_statuses_by_state = {
        "draft": {"planned", "cancelled"},
        "awaiting_authorization": {"planned", "cancelled"},
        "authorized": {"planned", "created", "blocked", "cancelled"},
        "round_1_in_progress": {
            "created",
            "prompted",
            "responded",
            "missing",
            "blocked",
            "cancelled",
        },
        "round_1_complete": {"responded", "missing", "blocked", "cancelled"},
        "round_2_in_progress": {
            "prompted",
            "responded",
            "missing",
            "blocked",
            "cancelled",
        },
        "round_2_complete": {"responded", "missing", "blocked", "cancelled"},
        "awaiting_confirmation": {"responded", "missing", "blocked", "cancelled"},
        "closed": {"responded", "missing", "blocked", "cancelled"},
    }
    allowed_seat_statuses = allowed_seat_statuses_by_state.get(str(state))
    if allowed_seat_statuses is not None:
        for seat in seats:
            if seat.get("status") not in allowed_seat_statuses:
                reporter.error(
                    f"{context} seat {seat.get('seat_id')} status {seat.get('status')!r} "
                    f"is incompatible with consultation state {state!r}"
                )

    rounds_value = record.get("rounds")
    if state in {"draft", "awaiting_authorization", "authorized"} and rounds_value != []:
        reporter.error(f"{context}.rounds must be empty while state is {state!r}")

    authorization = record.get("creation_authorization")
    authorization_id = (
        str(authorization.get("authorization_id"))
        if isinstance(authorization, dict)
        and _nonempty_string(authorization.get("authorization_id"))
        else None
    )
    response_created_times, brief_created_times = _validate_rounds(
        project,
        record.get("rounds"),
        str(consultation_id),
        str(topic),
        str(short_topic),
        authorization_id,
        seats,
        safe_round_limit,
        str(state),
        f"{context}.rounds",
        reporter,
    )

    synthesis_required = (
        state in SYNTHESIS_REQUIRED_STATES
        or record.get("outcome_status") == "proposed"
    )
    synthesis_path = record.get("synthesis_path")
    synthesis_created_at: Optional[datetime] = None
    if synthesis_path is not None or synthesis_required:
        artifact_path = _safe_public_path(
            project,
            synthesis_path,
            f"{context}.synthesis_path",
            str(consultation_id),
            reporter,
            required=synthesis_required,
        )
        if artifact_path is not None:
            rounds_for_count = record.get("rounds")
            rounds_for_synthesis = (
                rounds_for_count if isinstance(rounds_for_count, list) else []
            )
            synthesis_created_at = _validate_synthesis_artifact(
                artifact_path,
                str(synthesis_path),
                str(consultation_id),
                str(topic),
                rounds_for_synthesis,
                reporter,
            )
    outcome_status = record.get("outcome_status")
    if outcome_status not in OUTCOME_STATUSES:
        reporter.error(
            f"{context}.outcome_status must be one of: "
            f"{', '.join(sorted(OUTCOME_STATUSES))}"
        )
    if synthesis_path is not None and outcome_status == "none":
        reporter.error(
            f"{context}.outcome_status cannot be 'none' when synthesis_path is present"
        )
    if state == "awaiting_confirmation" and outcome_status != "proposed":
        reporter.error(
            f"{context}.outcome_status must be 'proposed' while awaiting confirmation"
        )
    if state == "closed" and outcome_status not in {
        "confirmed",
        "rejected",
        "deferred",
    }:
        reporter.error(
            f"{context}.outcome_status must be confirmed, rejected, or deferred "
            "when closed"
        )

    confirmation_status = _validate_confirmation(
        record.get("confirmation"),
        str(consultation_id),
        outcome_status,
        f"{context}.confirmation",
        reporter,
    )
    if isinstance(record.get("confirmation"), dict):
        recorded_at = _parse_rfc3339_utc(record["confirmation"].get("recorded_at"))
        if recorded_at is not None and created_at is not None and recorded_at < created_at:
            reporter.error(f"{context}.confirmation.recorded_at must not precede created_at")
        if recorded_at is not None and updated_at is not None and recorded_at > updated_at:
            reporter.error(f"{context}.updated_at must not precede confirmation recorded_at")
        authorization = record.get("creation_authorization")
        granted_at = (
            _parse_rfc3339_utc(authorization.get("granted_at"))
            if isinstance(authorization, dict)
            else None
        )
        if recorded_at is not None and granted_at is not None and recorded_at < granted_at:
            reporter.error(
                f"{context}.confirmation.recorded_at must not precede authorization granted_at"
            )
    authorization_for_timeline = record.get("creation_authorization")
    authorization_granted_at = (
        _parse_rfc3339_utc(authorization_for_timeline.get("granted_at"))
        if isinstance(authorization_for_timeline, dict)
        else None
    )
    confirmation_for_timeline = record.get("confirmation")
    confirmation_recorded_at = (
        _parse_rfc3339_utc(confirmation_for_timeline.get("recorded_at"))
        if isinstance(confirmation_for_timeline, dict)
        else None
    )
    for response_id, response_created_at in response_created_times.items():
        response_round_match = re.search(r"\.R([12])$", response_id)
        response_round = (
            int(response_round_match.group(1))
            if response_round_match is not None
            else None
        )
        brief_created_at = brief_created_times.get(response_round or 0)
        if brief_created_at is not None and response_created_at < brief_created_at:
            reporter.error(
                f"{context} response artifact {response_id} created_at must not "
                "precede its round brief created_at"
            )
        if (
            authorization_granted_at is not None
            and response_created_at < authorization_granted_at
        ):
            reporter.error(
                f"{context} response artifact {response_id} created_at must not "
                "precede creation authorization"
            )
        if synthesis_created_at is not None and response_created_at > synthesis_created_at:
            reporter.error(
                f"{context} response artifact {response_id} created_at must not "
                "follow synthesis created_at"
            )
        if (
            confirmation_recorded_at is not None
            and response_created_at > confirmation_recorded_at
        ):
            reporter.error(
                f"{context} response artifact {response_id} created_at must not "
                "follow Owner confirmation"
            )
        if updated_at is not None and response_created_at > updated_at:
            reporter.error(
                f"{context} response artifact {response_id} created_at must not "
                "follow record updated_at"
            )
    first_round_times = [
        value for key, value in response_created_times.items() if key.endswith(".R1")
    ]
    second_round_times = [
        value for key, value in response_created_times.items() if key.endswith(".R2")
    ]
    if first_round_times and second_round_times and min(second_round_times) < max(first_round_times):
        reporter.error(
            f"{context} second-round response artifacts must not precede all "
            "first-round response artifacts"
        )
    if 2 in brief_created_times and first_round_times:
        if brief_created_times[2] < max(first_round_times):
            reporter.error(
                f"{context} second-round brief created_at must not precede all "
                "first-round response artifacts"
            )
    for round_number, brief_created_at in brief_created_times.items():
        if created_at is not None and brief_created_at < created_at:
            reporter.error(
                f"{context} Round {round_number} brief created_at must not "
                "precede record created_at"
            )
        if synthesis_created_at is not None and brief_created_at > synthesis_created_at:
            reporter.error(
                f"{context} Round {round_number} brief created_at must not "
                "follow synthesis created_at"
            )
        if (
            confirmation_recorded_at is not None
            and brief_created_at > confirmation_recorded_at
        ):
            reporter.error(
                f"{context} Round {round_number} brief created_at must not "
                "follow Owner confirmation"
            )
        if updated_at is not None and brief_created_at > updated_at:
            reporter.error(
                f"{context} Round {round_number} brief created_at must not "
                "follow record updated_at"
            )
    if synthesis_created_at is not None:
        if (
            authorization_granted_at is not None
            and synthesis_created_at < authorization_granted_at
        ):
            reporter.error(
                f"{context} synthesis created_at must not precede creation authorization"
            )
        if (
            confirmation_recorded_at is not None
            and synthesis_created_at > confirmation_recorded_at
        ):
            reporter.error(
                f"{context} synthesis created_at must not follow Owner confirmation"
            )
        if updated_at is not None and synthesis_created_at > updated_at:
            reporter.error(f"{context} synthesis created_at must not follow record updated_at")
    confirmation_value = record.get("confirmation")
    confirmation_id = (
        str(confirmation_value.get("confirmation_id"))
        if isinstance(confirmation_value, dict)
        and _nonempty_string(confirmation_value.get("confirmation_id"))
        else None
    )
    has_derived_refs = _validate_derived_refs(
        project,
        record.get("derived_refs"),
        str(consultation_id),
        confirmation_id,
        confirmation_status,
        f"{context}.derived_refs",
        reporter,
    )
    if state == "awaiting_confirmation" and confirmation_status != "pending":
        reporter.error(
            f"{context}.confirmation.status must be 'pending' while awaiting confirmation"
        )
    if state == "closed" and confirmation_status == "pending":
        reporter.error(f"{context}.confirmation.status must not be 'pending' when closed")
    if state == "closed":
        expected_confirmation = {
            "confirmed": "confirmed",
            "rejected": "rejected",
            "deferred": "deferred",
        }.get(str(outcome_status))
        if expected_confirmation is not None and confirmation_status != expected_confirmation:
            reporter.error(
                f"{context}.confirmation.status must be {expected_confirmation!r} "
                f"when a closed consultation outcome is {outcome_status!r}"
            )
    if state == "cancelled":
        if outcome_status not in {"none", "proposed"}:
            reporter.error(
                f"{context}.outcome_status must be 'none' or 'proposed' when cancelled"
            )
        if confirmation_status not in {"pending", "not_required"}:
            reporter.error(
                f"{context}.confirmation.status must be pending or not_required when cancelled"
            )
    if state not in {"closed", "cancelled"} and confirmation_status != "pending":
        reporter.error(
            f"{context}.confirmation.status must remain 'pending' until the "
            "consultation is closed"
        )
    if state in {
        "draft",
        "awaiting_authorization",
        "authorized",
        "round_1_in_progress",
        "round_1_complete",
        "round_2_in_progress",
        "round_2_complete",
    } and outcome_status != "none":
        reporter.error(
            f"{context}.outcome_status must remain 'none' before synthesis enters "
            "awaiting_confirmation"
        )
    if has_derived_refs and state != "closed":
        reporter.error(
            f"{context}.derived_refs must stay empty until the consultation is closed"
        )
    if has_derived_refs and policy.get("require_owner_confirmation_for_derivation") is True:
        if confirmation_status != "confirmed":
            reporter.error(f"{context} derived refs require confirmed Owner scope")
        confirmation = record.get("confirmation")
        confirmed_scope = (
            confirmation.get("confirmed_scope")
            if isinstance(confirmation, dict)
            else None
        )
        if not isinstance(confirmed_scope, list) or not confirmed_scope:
            reporter.error(
                f"{context} derived refs require a non-empty confirmed_scope"
            )

    if policy.get("enabled") is False and state not in {"closed", "cancelled"}:
        reporter.error(
            f"{context} is active while consultation policy enabled=false"
        )
    if reporter.errors == starting_errors:
        reporter.passed(f"validated consultation {consultation_id} ({state})")


def _scan_record_paths(project: Path, reporter: Reporter) -> list[tuple[Path, Path]]:
    root = project / CONSULTATION_ROOT
    if not root.exists() and not root.is_symlink():
        return []
    if root.is_symlink() or not root.is_dir():
        reporter.error(
            f"{CONSULTATION_ROOT.as_posix()} must be a regular non-symlink directory"
        )
        return []
    records: list[tuple[Path, Path]] = []
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        reporter.error(f"cannot scan {CONSULTATION_ROOT.as_posix()}: {exc}")
        return []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            reporter.error(
                f"consultation path must not be a symlink: {entry.relative_to(project)}"
            )
            continue
        if not entry.is_dir():
            continue
        record_path = entry / "consultation.yaml"
        if not record_path.exists() and not record_path.is_symlink():
            continue
        if CONSULTATION_ID_RE.fullmatch(entry.name) is None:
            reporter.error(
                "consultation record directory must match C followed by exactly "
                f"three digits: {entry.name}"
            )
        if record_path.is_symlink() or not record_path.is_file():
            reporter.error(
                "consultation record must be a regular non-symlink file: "
                f"{record_path.relative_to(project)}"
            )
            continue
        records.append((entry, record_path))
    return records


def _validate_task_card_backrefs(
    project: Path,
    public_records: list[dict[str, Any]],
    reporter: Reporter,
) -> None:
    task_root = project / "docs/pm/tasks"
    if not task_root.exists():
        return
    if task_root.is_symlink() or not task_root.is_dir():
        reporter.error("docs/pm/tasks must be a regular non-symlink directory")
        return
    records_by_id = {
        str(record.get("consultation_id")): record
        for record in public_records
        if _nonempty_string(record.get("consultation_id"))
    }
    try:
        candidates = sorted(task_root.rglob("*.md"), key=lambda path: path.as_posix())
    except OSError as exc:
        reporter.error(f"cannot scan docs/pm/tasks for consultation lineage: {exc}")
        return
    for path in candidates:
        relative = path.relative_to(project).as_posix()
        if path.is_symlink() or not path.is_file():
            reporter.error(f"task card lineage path must be a regular file: {relative}")
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            reporter.error(f"cannot read {relative}: {exc}")
            continue
        referenced_consultations = set(CONSULTATION_TOKEN_RE.findall(raw))
        referenced_consultations.update(
            consultation_id
            for _confirmation_id, consultation_id in CONFIRMATION_TOKEN_RE.findall(raw)
        )
        referenced_consultations &= set(records_by_id)
        has_lineage_fields = (
            "source_consultation_id:" in raw
            or "source_confirmation_id:" in raw
        )
        if not has_lineage_fields and not referenced_consultations:
            continue
        result = _read_frontmatter_artifact(path, relative, None, reporter)
        if result is None:
            continue
        frontmatter, _content = result
        consultation_id = frontmatter.get("source_consultation_id")
        confirmation_id = frontmatter.get("source_confirmation_id")
        if consultation_id is None and confirmation_id is None:
            if referenced_consultations:
                reporter.error(
                    f"{relative} references consultation(s) "
                    f"{', '.join(sorted(referenced_consultations))} but leaves "
                    "source_consultation_id/source_confirmation_id null or absent"
                )
            continue
        if not _nonempty_string(consultation_id) or not _nonempty_string(confirmation_id):
            reporter.error(
                f"{relative} must set source_consultation_id and source_confirmation_id together"
            )
            continue
        record = records_by_id.get(str(consultation_id))
        if record is None:
            reporter.error(
                f"{relative} references unknown consultation {consultation_id!r}"
            )
            continue
        confirmation = record.get("confirmation")
        derived = record.get("derived_refs")
        task_paths = derived.get("task_card_paths") if isinstance(derived, dict) else None
        if record.get("state") != "closed" or not isinstance(confirmation, dict):
            reporter.error(
                f"{relative} references a consultation that is not closed and confirmed"
            )
            continue
        if (
            confirmation.get("status") != "confirmed"
            or confirmation.get("confirmation_id") != confirmation_id
        ):
            reporter.error(
                f"{relative} source_confirmation_id does not match a confirmed Owner scope"
            )
        if not isinstance(task_paths, list) or relative not in task_paths:
            reporter.error(
                f"{relative} must appear in {consultation_id}.derived_refs.task_card_paths"
            )


def _validate_decision_backrefs(
    project: Path,
    public_records: list[dict[str, Any]],
    reporter: Reporter,
) -> None:
    path = project / DECISIONS_FILE
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        reporter.error(f"{DECISIONS_FILE.as_posix()} must be a regular non-symlink file")
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        reporter.error(f"cannot read {DECISIONS_FILE.as_posix()}: {exc}")
        return
    records_by_id = {
        str(record.get("consultation_id")): record
        for record in public_records
        if _nonempty_string(record.get("consultation_id"))
    }
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0] in {"ID", "---", ""}:
            continue
        referenced = set(CONSULTATION_TOKEN_RE.findall(line))
        referenced.update(
            consultation_id
            for _confirmation_id, consultation_id in CONFIRMATION_TOKEN_RE.findall(line)
        )
        for consultation_id in sorted(referenced):
            record = records_by_id.get(consultation_id)
            if record is None:
                reporter.error(
                    f"decision row {cells[0]!r} references unknown consultation {consultation_id}"
                )
                continue
            confirmation = record.get("confirmation")
            derived = record.get("derived_refs")
            decision_ids = derived.get("decision_ids") if isinstance(derived, dict) else None
            confirmation_id = (
                confirmation.get("confirmation_id")
                if isinstance(confirmation, dict)
                else None
            )
            if (
                record.get("state") != "closed"
                or not isinstance(confirmation, dict)
                or confirmation.get("status") != "confirmed"
                or not _nonempty_string(confirmation_id)
                or not _contains_exact_identifier(line, str(confirmation_id))
                or not _contains_exact_identifier(line, consultation_id)
            ):
                reporter.error(
                    f"decision row {cells[0]!r} does not cite a closed confirmed scope for {consultation_id}"
                )
            if not isinstance(decision_ids, list) or cells[0] not in decision_ids:
                reporter.error(
                    f"decision row {cells[0]!r} must appear in {consultation_id}.derived_refs.decision_ids"
                )


def validate_public_snapshot(
    project: Path,
    policy: Optional[dict[str, Any]],
    records: Iterable[tuple[str, dict[str, Any]]],
) -> Reporter:
    """Validate one already-loaded public consultation snapshot.

    This entry point exists for renderers that must validate the exact objects
    they project, rather than re-reading policy and record files after loading
    them.  Related public artifacts and lineage files are still resolved below
    ``project`` by the existing validation rules.
    """

    reporter = Reporter()
    if not project.exists():
        reporter.error(f"project path does not exist: {project}")
        return reporter
    if not project.is_dir():
        reporter.error(f"project path is not a directory: {project}")
        return reporter
    if project.is_symlink():
        reporter.error(f"project path must not be a symlink: {project}")
        return reporter
    project = project.resolve()
    snapshot_records = list(records)

    if policy is None and not snapshot_records:
        optional_starting_errors = reporter.errors
        _validate_task_card_backrefs(project, [], reporter)
        _validate_decision_backrefs(project, [], reporter)
        stale_view = project / "docs/pm/CONSULTATIONS.md"
        if stale_view.is_file() and not stale_view.is_symlink():
            try:
                stale_text = stale_view.read_text(encoding="utf-8")
            except OSError as exc:
                reporter.error(f"cannot read docs/pm/CONSULTATIONS.md: {exc}")
            else:
                if (
                    "<!-- GENERATED FILE: "
                    "source=docs/pm/consultations/*/consultation.yaml -->"
                    in stale_text
                ):
                    reporter.error(
                        "generated docs/pm/CONSULTATIONS.md is orphaned because "
                        "its policy and source records are absent"
                    )
                    return reporter
        if reporter.errors == optional_starting_errors:
            reporter.passed("optional consultation control plane is not configured")
        return reporter

    if policy is None:
        reporter.error(
            f"{POLICY_FILE.as_posix()} is required when consultation records exist"
        )
        return reporter
    _validate_policy(policy, reporter)

    seen_ids: set[str] = set()
    public_records: list[dict[str, Any]] = []
    for index, (directory_name, record) in enumerate(snapshot_records):
        if (
            not isinstance(directory_name, str)
            or CONSULTATION_ID_RE.fullmatch(directory_name) is None
        ):
            reporter.error(
                "consultation record directory must match C followed by exactly "
                f"three digits: {directory_name!r}"
            )
            directory_name = f"__invalid_snapshot_{index}"
        if not isinstance(record, dict):
            reporter.error(
                f"snapshot consultation record {directory_name!r} must be an object"
            )
            continue
        public_records.append(record)
        _validate_record(
            project,
            project / CONSULTATION_ROOT / directory_name,
            record,
            policy,
            seen_ids,
            reporter,
        )
    if not snapshot_records:
        reporter.passed("consultation record set is empty")
    _validate_task_card_backrefs(project, public_records, reporter)
    _validate_decision_backrefs(project, public_records, reporter)
    return reporter


def validate(project: Path, require_runtime: bool = False) -> Reporter:
    reporter = Reporter()
    if not project.exists():
        reporter.error(f"project path does not exist: {project}")
        return reporter
    if not project.is_dir():
        reporter.error(f"project path is not a directory: {project}")
        return reporter
    if project.is_symlink():
        reporter.error(f"project path must not be a symlink: {project}")
        return reporter
    project = project.resolve()

    record_paths = _scan_record_paths(project, reporter)
    policy_path = project / POLICY_FILE
    if not policy_path.exists() and not policy_path.is_symlink() and not record_paths:
        optional_starting_errors = reporter.errors
        _validate_task_card_backrefs(project, [], reporter)
        _validate_decision_backrefs(project, [], reporter)
        stale_view = project / "docs/pm/CONSULTATIONS.md"
        if stale_view.is_file() and not stale_view.is_symlink():
            try:
                stale_text = stale_view.read_text(encoding="utf-8")
            except OSError as exc:
                reporter.error(f"cannot read docs/pm/CONSULTATIONS.md: {exc}")
            else:
                if "<!-- GENERATED FILE: source=docs/pm/consultations/*/consultation.yaml -->" in stale_text:
                    reporter.error(
                        "generated docs/pm/CONSULTATIONS.md is orphaned because its policy and source records are absent"
                    )
                    return reporter
        runtime_path = project / RUNTIME_FILE
        if require_runtime and (runtime_path.exists() or runtime_path.is_symlink()):
            _validate_runtime(project, [], reporter)
        if reporter.errors == optional_starting_errors:
            reporter.passed("optional consultation control plane is not configured")
        return reporter
    if policy_path.is_symlink() or (policy_path.exists() and not policy_path.is_file()):
        reporter.error(f"{POLICY_FILE.as_posix()} must be a regular non-symlink file")
        return reporter
    if not policy_path.is_file():
        reporter.error(
            f"{POLICY_FILE.as_posix()} is required when consultation records exist"
        )
        return reporter
    policy = _load_json_object(policy_path, POLICY_FILE.as_posix(), reporter)
    if policy is None:
        return reporter
    _validate_policy(policy, reporter)

    seen_ids: set[str] = set()
    public_records: list[dict[str, Any]] = []
    for directory, record_path in record_paths:
        context = record_path.relative_to(project).as_posix()
        record = _load_json_object(record_path, context, reporter)
        if record is None:
            continue
        public_records.append(record)
        _validate_record(project, directory, record, policy, seen_ids, reporter)
    if not record_paths:
        reporter.passed("consultation record set is empty")
    _validate_task_card_backrefs(project, public_records, reporter)
    _validate_decision_backrefs(project, public_records, reporter)
    if require_runtime:
        _validate_runtime(project, public_records, reporter)
    return reporter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate AgentDesk's optional consultation policy and public "
            "per-CNNN consultation records."
        )
    )
    parser.add_argument(
        "--project",
        default=".",
        help="project root (default: current directory)",
    )
    parser.add_argument(
        "--require-runtime",
        action="store_true",
        help=(
            "also require locally recorded, mutually distinct Leader/seat routes "
            "and matching prompt/response transport receipts"
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    reporter = validate(
        Path(args.project).expanduser(),
        require_runtime=args.require_runtime,
    )
    reporter.summary()
    return 1 if reporter.errors else 0


if __name__ == "__main__":
    sys.exit(main())
