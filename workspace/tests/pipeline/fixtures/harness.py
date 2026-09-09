"""The spend guard and the determinism pins, shared by both pipeline suites.

THE PIPELINE TESTS MUST NEVER REACH A MODEL. Both routes cost: the metered API
spends dollars, and the `claude -p` subscription path spends a finite weekly
allowance on a personal plan. So the guard is layered, and every layer is
independent of the others:

1. A test replaces `digester.extract.call_with_document`, the only model call
   either live extraction pass makes.
2. Both transport dispatch points and all six of its dispatchers raise.
3. The transport's view of `subprocess` is a shim whose `run` raises, closing
   the subscription CLI path.
4. `socket.socket.connect`, `connect_ex` and `socket.create_connection` raise,
   so a path that reaches a provider outside the transport - a vendor SDK over
   httpx, say - fails rather than dials.
5. Every `*_API_KEY` is removed from the environment by each suite at import.

Layers 2, 3 and 4 were each shown to catch a real extraction with the layers
above it removed, so none of them is decoration.

This module lives in the digester's fixture package and is loaded by path from
the assimilator's end-to-end module, which already loads the fixture corpus the
same way. Two copies of a spend guard drift, and the copy that drifts is the one
that lets a call through.
"""

from __future__ import annotations

import socket
import subprocess as _real_subprocess
import uuid as _real_uuid
from datetime import datetime, timezone


class ProviderCallAttempted(AssertionError):
    """A model provider was about to be called. Nothing in these suites may."""


def refuse(what: str):
    def _raise(*args, **kwargs):
        raise ProviderCallAttempted(
            f"{what} was called - the pipeline tests must never reach a model "
            "provider, on the metered API or the subscription CLI"
        )

    return _raise


class SubprocessShim:
    """Stands in for the `subprocess` module inside the transport only.

    Replacing the module attribute rather than patching `subprocess.run`
    globally keeps the blast radius to the transport: everything the module
    reads from `subprocess` (TimeoutExpired, PIPE) still resolves, but every way
    of starting a process raises.
    """

    run = staticmethod(refuse("subprocess.run (the claude -p subscription path)"))
    Popen = staticmethod(refuse("subprocess.Popen"))
    call = staticmethod(refuse("subprocess.call"))
    check_call = staticmethod(refuse("subprocess.check_call"))
    check_output = staticmethod(refuse("subprocess.check_output"))

    def __getattr__(self, name):
        return getattr(_real_subprocess, name)


# Every function in the transport that can reach a provider. Named individually
# rather than guarded at one choke point, because a new dispatcher added beside
# these would slip past a single patch and spend real money before anyone read
# the diff.
GUARDED_TRANSPORT_CALLS = (
    "_dispatch_document",
    "_dispatch_call",
    "_call_cli_doc",
    "_call_api_doc",
    "_call_cli",
    "_call_api",
    "_call_opencode",
    "_call_openrouter",
    "_call_openrouter_pages",
    "call_with_research",
)


def install_spend_guard(mp) -> None:
    """Make a real model call impossible, by every route the code has.

    `mp` is anything with pytest's `MonkeyPatch.setattr`, so a function-scoped
    `monkeypatch` and a `MonkeyPatch.context()` both work.
    """
    from anomalica_common.llm import transport

    mp.setattr(transport, "subprocess", SubprocessShim())
    for name in GUARDED_TRANSPORT_CALLS:
        if hasattr(transport, name):
            mp.setattr(transport, name, refuse(f"transport.{name}"))

    mp.setattr(socket.socket, "connect", refuse("socket.socket.connect"))
    mp.setattr(socket.socket, "connect_ex", refuse("socket.socket.connect_ex"))
    mp.setattr(socket, "create_connection", refuse("socket.create_connection"))

    # The real one downloads a ~600MB sentence-transformers model on first call.
    # Absent when the digester's container runs this without the assimilator.
    try:
        from assimilator import embeddings
    except ImportError:
        pass
    else:
        mp.setattr(
            embeddings, "embed_text", refuse("assimilator.embeddings.embed_text")
        )


FROZEN_INSTANT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class CountingUuid:
    """Stands in for the `uuid` module: `uuid4()` walks a counter.

    `rebase` is for the re-import test, which needs the second emission of the
    same record to mint a DIFFERENT record id - what production does on every
    re-digest, and the only way to put the importer's content-hash fallback
    under test rather than behind a primary-key hit. `band` marks which minter
    an id came from, so an id the importer made for itself is visible on sight.
    """

    def __init__(self, start: int = 0, band: int = 0) -> None:
        self._n = start
        self._band = band

    def uuid4(self) -> _real_uuid.UUID:
        self._n += 1
        return _real_uuid.UUID(f"00000000-0000-4000-8000-{self._band:04d}{self._n:08d}")

    def rebase(self, start: int) -> None:
        self._n = start

    def __getattr__(self, name):
        return getattr(_real_uuid, name)


class FrozenDatetime(datetime):
    """Stands in for `datetime` where the digest stamps `extracted_at`."""

    @classmethod
    def now(cls, tz=None):
        return FROZEN_INSTANT if tz else FROZEN_INSTANT.replace(tzinfo=None)
