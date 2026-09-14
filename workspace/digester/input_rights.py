"""Terminal rights authority for source-derived hosted model input."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from digester.record_parser import parse_record


HOSTED_STATUSES = frozenset({"public_domain", "open_licence"})
KNOWN_STATUSES = HOSTED_STATUSES | {
    "publicly_accessible",
    "licensed",
    "restricted",
}
ORDINARY_EXTRACTION_USE = "digest-extraction"
EVALUATION_USE = "hosted-model-inference"
_CONTENT_HASH = re.compile(r"^sha256:([0-9a-f]{64})$")
_SEAL = object()


class HostedInputRightsError(ValueError):
    """Hosted input cannot be authorised against an exact live record."""


@dataclass(frozen=True)
class HostedInputAuthority:
    record_path: Path
    record_content_hash: str
    record_sha256: str
    body_sha256: str
    provider: str
    route: str
    use: str
    _seal: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class HostedRoute:
    provider: str
    route: str


def hosted_route(model: str, use_api: bool = False) -> HostedRoute:
    """Resolve the provider boundary used by every current Digester model route."""
    from anomalica_common.llm import (
        is_openai_subscription_model,
        is_opencode_model,
        is_openrouter_model,
    )

    if is_opencode_model(model):
        return HostedRoute("opencode", "opencode")
    if is_openai_subscription_model(model):
        return HostedRoute("openai", "openai-subscription")
    if is_openrouter_model(model):
        return HostedRoute("openrouter", "openrouter")
    return HostedRoute("anthropic", "api" if use_api else "cli")


def _strict_record(path: Path) -> tuple[Path, str, bytes, dict, str]:
    try:
        resolved = path.resolve(strict=True)
        raw = resolved.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HostedInputRightsError("hosted input record cannot be read") from exc

    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise HostedInputRightsError("hosted input record has malformed frontmatter")
    try:
        end = lines.index("---", 1)
        metadata = yaml.safe_load("\n".join(lines[1:end]))
    except (ValueError, yaml.YAMLError) as exc:
        raise HostedInputRightsError(
            "hosted input record has malformed frontmatter"
        ) from exc
    if not isinstance(metadata, dict):
        raise HostedInputRightsError("hosted input record has malformed frontmatter")

    content_hash = metadata.get("content_hash")
    match = (
        _CONTENT_HASH.fullmatch(content_hash) if isinstance(content_hash, str) else None
    )
    if match is None:
        raise HostedInputRightsError("hosted input record has malformed content_hash")
    bare_hash = match.group(1)

    if resolved.parent.name == "store":
        store = resolved.parent
    elif path.parent.name == "by-name":
        store = path.parent.parent / "store"
    else:
        raise HostedInputRightsError(
            "hosted input record is not in a live record store"
        )

    candidates = [
        candidate
        for candidate in (store / f"{bare_hash}.md", store / f"{bare_hash}.v2.md")
        if candidate.is_file()
    ]
    if len(candidates) != 1:
        raise HostedInputRightsError(
            "hosted input content_hash does not resolve to exactly one live record"
        )
    canonical = candidates[0]
    try:
        canonical_raw = canonical.read_bytes()
    except OSError as exc:
        raise HostedInputRightsError("hosted input live record cannot be read") from exc
    if canonical_raw != raw:
        raise HostedInputRightsError(
            "hosted input bytes do not match the content_hash-resolved live record"
        )
    canonical_stem = canonical.name.removesuffix(".md").removesuffix(".v2")
    if canonical_stem != bare_hash:
        raise HostedInputRightsError(
            "hosted input record filename and content_hash mismatch"
        )

    parsed = parse_record(text)
    return canonical.resolve(), content_hash, raw, metadata, parsed.body


def _bind(
    path: str | Path,
    *,
    provider: str,
    route: str,
    use: str,
    require_open_status: bool,
) -> HostedInputAuthority:
    canonical, content_hash, raw, metadata, body = _strict_record(Path(path))
    copyright_block = metadata.get("copyright")
    status = (
        copyright_block.get("status") if isinstance(copyright_block, dict) else None
    )
    if require_open_status and (
        not isinstance(status, str) or status not in HOSTED_STATUSES
    ):
        if status is None:
            reason = "absent"
        elif not isinstance(status, str) or status not in KNOWN_STATUSES:
            reason = "unrecognised or malformed"
        else:
            reason = status
        raise HostedInputRightsError(
            f"copyright.status {reason} does not permit hosted model input"
        )
    return HostedInputAuthority(
        record_path=canonical,
        record_content_hash=content_hash,
        record_sha256=hashlib.sha256(raw).hexdigest(),
        body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        provider=provider,
        route=route,
        use=use,
        _seal=_SEAL,
    )


def authorise_ordinary_extraction(
    path: str | Path, model: str, use_api: bool = False
) -> HostedInputAuthority:
    route = hosted_route(model, use_api)
    return _bind(
        path,
        provider=route.provider,
        route=route.route,
        use=ORDINARY_EXTRACTION_USE,
        require_open_status=True,
    )


def _bind_verified_evaluation(
    path: str | Path, *, provider: str, route: str, use: str
) -> HostedInputAuthority:
    """Bind evaluation routing without widening ordinary hosted-input rights."""
    return _bind(
        path,
        provider=provider,
        route=route,
        use=use,
        require_open_status=True,
    )


def assert_authority(
    authority: HostedInputAuthority | None,
    body: str,
    model: str,
    use_api: bool = False,
) -> None:
    route = hosted_route(model, use_api)
    current = None
    if isinstance(authority, HostedInputAuthority) and authority._seal is _SEAL:
        try:
            current = _strict_record(authority.record_path)
        except HostedInputRightsError:
            current = None
    if (
        not isinstance(authority, HostedInputAuthority)
        or authority._seal is not _SEAL
        or authority.provider != route.provider
        or authority.route != route.route
        or authority.use not in {ORDINARY_EXTRACTION_USE, EVALUATION_USE}
        or authority.body_sha256 != hashlib.sha256(body.encode("utf-8")).hexdigest()
        or current is None
        or current[1] != authority.record_content_hash
        or hashlib.sha256(current[2]).hexdigest() != authority.record_sha256
    ):
        raise HostedInputRightsError(
            "hosted provider call lacks exact record/provider/route input authority"
        )
