"""Support tooling CLI for the GDPR right-to-erasure cascade (TASK-134).

Wraps :func:`src.data.erasure.erase_user` with a tiny argparse surface.
The CLI is intended to run from an operator's workstation under a
support engineer's own Entra credentials — the cascade rejects any
caller not in ``group:flint-support-erasure`` (the membership is
resolved server-side from the Entra OID claim, **never** trusted from
CLI args).

The CLI is deliberately small. It does NOT:

  * Construct production wiring (the Bicep + composition wiring does).
  * Hold any secrets. The KV wrapper does.
  * Expose a `--force` flag. The cascade is idempotent — repeats are
    safe and emit a dedup'd `audit.user_erased.repeat` event.
  * Re-implement auth. It calls into ``ErasureService.erase_user``,
    which calls into the injected :class:`GroupResolver`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Awaitable, Callable

from src.data.erasure import ErasureReceipt, ErasureService

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flint-erase-user",
        description=(
            "Run the GDPR right-to-erasure cascade for a user. Requires "
            "the caller to be a member of `group:flint-support-erasure` "
            "(verified from the Entra OID claim)."
        ),
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="The `user_id` (Entra OID of the data subject) to erase.",
    )
    parser.add_argument(
        "--requested-by-oid",
        required=True,
        help=(
            "The Entra Object ID of the support engineer running this. "
            "Logged in the audit-of-audit envelope. Group membership is "
            "verified server-side against this value."
        ),
    )
    parser.add_argument(
        "--ticket-ref",
        required=True,
        help="Support ticket reference (audit-of-audit requirement).",
    )
    parser.add_argument(
        "--format",
        choices=["json", "human"],
        default="human",
        help="Output format for the erasure receipt.",
    )
    return parser


def render_receipt(receipt: ErasureReceipt, fmt: str) -> str:
    """Render an :class:`ErasureReceipt` for the chosen output format."""

    if fmt == "json":
        return json.dumps(
            {
                "user_id_pseudonym": receipt.user_id_pseudonym,
                "salt_version": receipt.salt_version,
                "requested_by": receipt.requested_by,
                "ticket_ref": receipt.ticket_ref,
                "timestamp": receipt.timestamp.isoformat(),
                "users_deleted": receipt.users_deleted,
                "sessions_deleted": receipt.sessions_deleted,
                "audit_pseudonymized": receipt.audit_pseudonymized,
                "archive_locked_snapshots": list(receipt.archive_locked_snapshots),
                "repeat": receipt.repeat,
            },
            indent=2,
            sort_keys=True,
        )
    # Human-readable summary — terse on purpose.
    lines = [
        f"Erasure receipt — {'repeat' if receipt.repeat else 'fresh'}",
        f"  pseudonym             {receipt.user_id_pseudonym} (salt v{receipt.salt_version})",
        f"  ticket                {receipt.ticket_ref}",
        f"  requested by (OID …)  {receipt.requested_by[:8]}…",
        f"  timestamp             {receipt.timestamp.isoformat()}",
        f"  users deleted         {receipt.users_deleted}",
        f"  sessions deleted      {receipt.sessions_deleted}",
        f"  audit pseudonymized   {receipt.audit_pseudonymized}",
        f"  archive locked        {len(receipt.archive_locked_snapshots)} snapshots",
    ]
    return "\n".join(lines)


async def _main_async(
    argv: list[str],
    service_factory: Callable[[], Awaitable[ErasureService]],
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    service = await service_factory()
    try:
        receipt = await service.erase_user(
            user_id=args.user_id,
            requested_by_oid=args.requested_by_oid,
            ticket_ref=args.ticket_ref,
        )
    except PermissionError as exc:
        # The cascade surfaces a clear permission error when the caller
        # is not in the support group. The CLI exits 3 so wrapping
        # scripts can distinguish "auth denied" from "argument error" (2).
        print(f"erasure denied: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"invalid arguments: {exc}", file=sys.stderr)
        return 2

    print(render_receipt(receipt, fmt=args.format))
    return 0


def main(
    argv: list[str] | None = None,
    *,
    service_factory: Callable[[], Awaitable[ErasureService]] | None = None,
) -> int:
    """Sync entry point — wraps the async runner.

    Production wiring constructs an :class:`ErasureService` once at
    startup; the CLI receives it via `service_factory`. Tests pass a
    light async factory returning an in-memory fake service.
    """

    if service_factory is None:
        raise SystemExit(
            "erasure_cli: service_factory is required — production wiring "
            "must construct ErasureService with the live KV + Cosmos + "
            "Entra group resolver before invoking the CLI."
        )

    return asyncio.run(_main_async(argv or sys.argv[1:], service_factory))


__all__ = ["main", "render_receipt"]
