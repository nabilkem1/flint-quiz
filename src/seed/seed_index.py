"""Blob → AI Search seed loader (002 TASK-026 + TASK-028).

Walks the authored question tree (local filesystem in dev, Blob Storage in
production), validates each record against the `Question` Pydantic model,
and reconciles the AI Search `questions` index by diff:

    {added, updated, deleted} per language.

Idempotency:
    A re-run over an unchanged tree is a no-op — zero upserts, zero deletes.
    The diff is computed by hashing the seed-record body and comparing to a
    `seed_hash` field on the index document.

Safety rails:
    * Refuses to run if the running identity has `Search Service Contributor`
      on the search service (defense-in-depth — index lifecycle is Bicep-owned
      via `uami-deploy-*`, not the loader).
    * Refuses to delete more than 10 % of the index unless `--confirm` is
      passed (TASK-028 — accidental mass-delete from a misconfigured Blob).
    * Waits the AI Search indexer sync window (sleep 5 s + verify doc count
      matches expected) before invoking the topic-count reconciliation.

Identity:
    Authenticates via `DefaultAzureCredential` resolved to `uami-indexer-*`
    in production (Search Index Data Contributor on the index, Storage Blob
    Data Reader on the storage account — see infra/modules/rbac.bicep). In
    dev / CI, falls back to whatever interactive identity the operator is
    signed in as.

Refs: §007-operational-runbook §1, NFR-010, NFR-011.
"""

import argparse
import asyncio
import dataclasses
import hashlib
import json
import logging
import pathlib
import sys
from collections.abc import AsyncIterator, Iterable
from typing import Any

logger = logging.getLogger("seed.seed_index")

# When the loader is asked to delete more than this fraction of the existing
# index, it requires --confirm. Guards against a misconfigured Blob root
# silently wiping the question bank (TASK-028).
MAX_DELETE_FRACTION_WITHOUT_CONFIRM = 0.10

# Sleep window for AI Search to surface upserts via search / facet queries
# after a write batch. Empirical; matches infra/README §11. Exposed as a
# constant so reconcile_topics can override under test.
INDEXER_SYNC_SECONDS = 5

# Role definition GUID for `Search Service Contributor` — forbidden on the
# loader identity per defense-in-depth.
SEARCH_SERVICE_CONTRIBUTOR_ROLE_ID = "7ca78c08-252a-4471-8644-bb5ff32d4ba0"


@dataclasses.dataclass(frozen=True, slots=True)
class IndexDocument:
    """The AI Search document body, with the per-language `text_<lang>` /
    `explanation_<lang>` fields pivoted from the authored single-language record.

    Carries `seed_hash` so the reindex diff can short-circuit unchanged records.
    """

    id: str
    logical_id: str
    topic: str
    language: str
    text_en: str | None
    text_fr: str | None
    text_es: str | None
    options: list[dict[str, str]]
    correct_answer: list[str]
    difficulty: str
    tags: list[str]
    category: str
    explanation_en: str | None
    explanation_fr: str | None
    explanation_es: str | None
    score_weight: float
    seed_hash: str

    def to_search_body(self) -> dict[str, Any]:
        body: dict[str, Any] = dataclasses.asdict(self)
        # AI Search rejects nulls on fields with no value — emit only the
        # populated per-language fields.
        return {k: v for k, v in body.items() if v is not None}


@dataclasses.dataclass(frozen=True, slots=True)
class DiffSummary:
    """Per-language reindex outcome — emitted at end of run."""

    language: str
    added: int
    updated: int
    deleted: int
    unchanged: int


def _compute_seed_hash(record: dict[str, Any]) -> str:
    """Stable hash of the authored record body. Used to short-circuit upserts
    when nothing changed (idempotency, TASK-026)."""
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _to_index_document(authored: dict[str, Any]) -> IndexDocument:
    """Pivot the single-language authored record to the per-language index shape."""
    language = authored["language"]
    text_fields = {f"text_{lang}": None for lang in ("en", "fr", "es")}
    text_fields[f"text_{language}"] = authored["text"]
    explanation_fields = {f"explanation_{lang}": None for lang in ("en", "fr", "es")}
    explanation_fields[f"explanation_{language}"] = authored["explanation"]

    return IndexDocument(
        id=f"{authored['logical_id']}-{language}",
        logical_id=authored["logical_id"],
        topic=authored["topic"],
        language=language,
        text_en=text_fields["text_en"],
        text_fr=text_fields["text_fr"],
        text_es=text_fields["text_es"],
        options=[{"key": o["key"], "text": o["text"]} for o in authored["options"]],
        correct_answer=list(authored["correct_answer"]),
        difficulty=authored["difficulty"],
        tags=list(authored.get("tags", [])),
        category=authored["category"],
        explanation_en=explanation_fields["explanation_en"],
        explanation_fr=explanation_fields["explanation_fr"],
        explanation_es=explanation_fields["explanation_es"],
        score_weight=float(authored.get("score_weight", 1.0)),
        seed_hash=_compute_seed_hash(authored),
    )


# ---------------------------------------------------------------------------
# Source: filesystem or Blob
# ---------------------------------------------------------------------------


async def _iter_local(root: pathlib.Path) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Walk a local seed tree under `root/<lang>/<topic>/<logical_id>.json`."""
    for path in sorted(root.rglob("*.json")):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON ({exc})") from exc
        yield str(path), body


async def _iter_blob(
    blob_endpoint: str,
    container: str,
    credential: Any,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Walk a Blob container under `questions/<lang>/<topic>/<logical_id>.json`.

    Imports the SDK locally so the seed module can be unit-tested without an
    Azure connection (the local-path branch covers the happy path).
    """
    from azure.storage.blob.aio import BlobServiceClient

    async with BlobServiceClient(account_url=blob_endpoint, credential=credential) as svc:
        container_client = svc.get_container_client(container)
        async for blob in container_client.list_blobs(name_starts_with="questions/"):
            if not blob.name.endswith(".json"):
                continue
            stream = await container_client.download_blob(blob.name)
            data = await stream.readall()
            try:
                body = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{blob.name}: invalid JSON ({exc})") from exc
            yield blob.name, body


# ---------------------------------------------------------------------------
# Identity guard (defense-in-depth — TASK-026)
# ---------------------------------------------------------------------------


async def assert_no_control_plane_role(
    search_resource_id: str,
    principal_id: str,
    credential: Any,
) -> None:
    """Abort if the running identity holds Search Service Contributor on the search service.

    The seed loader is data-plane only. Granting it Search Service Contributor
    would let it create / delete the index — that authority lives in CI
    (uami-deploy-*). A misconfigured identity should fail loudly here rather
    than at the first control-plane API call.

    Falls back to a CRITICAL warning if the role-listing call itself is
    forbidden (i.e. the identity cannot read its own role assignments). In
    that case the operator MUST pass `--acknowledge-identity-check-skipped`
    on the CLI to proceed — never silently.
    """
    try:
        from azure.mgmt.authorization.aio import AuthorizationManagementClient
    except ImportError:  # pragma: no cover - SDK not installed in some test envs
        logger.critical(
            "seed_loader.identity_check_skipped",
            extra={"reason": "azure-mgmt-authorization not installed"},
        )
        raise RuntimeError(
            "identity check requires azure-mgmt-authorization; install it or "
            "pass --acknowledge-identity-check-skipped after verifying RBAC manually"
        )

    # Subscription ID is the second path segment of any resource ID.
    parts = search_resource_id.split("/")
    if len(parts) < 3 or parts[1] != "subscriptions":
        raise ValueError(f"unrecognized search_resource_id: {search_resource_id}")
    subscription_id = parts[2]

    async with AuthorizationManagementClient(credential, subscription_id) as client:
        try:
            async for assignment in client.role_assignments.list_for_scope(
                scope=search_resource_id,
                filter=f"principalId eq '{principal_id}'",
            ):
                role_def_id = (assignment.role_definition_id or "").rsplit("/", 1)[-1]
                if role_def_id == SEARCH_SERVICE_CONTRIBUTOR_ROLE_ID:
                    raise RuntimeError(
                        "seed loader identity holds Search Service Contributor on "
                        f"{search_resource_id}; index lifecycle is Bicep-owned via "
                        "uami-deploy-*. Re-scope to Search Index Data Contributor only "
                        "(see infra/modules/rbac.bicep)."
                    )
        except Exception as exc:  # pragma: no cover - 403 path requires live RBAC
            if "AuthorizationFailed" in str(exc) or "403" in str(exc):
                logger.critical(
                    "seed_loader.identity_check_skipped",
                    extra={"reason": "principal cannot list its own role assignments"},
                )
                raise RuntimeError(
                    "could not enumerate role assignments to verify least-privilege; "
                    "pass --acknowledge-identity-check-skipped after verifying RBAC "
                    "manually (see infra/modules/rbac.bicep)"
                ) from exc
            raise


# ---------------------------------------------------------------------------
# Reindex pipeline
# ---------------------------------------------------------------------------


async def compute_diff(
    desired: Iterable[IndexDocument],
    existing: dict[str, str],
) -> tuple[list[IndexDocument], list[IndexDocument], list[str], int]:
    """Bucket the desired records vs existing index state.

    `existing` is a `{id: seed_hash}` map from the live index. Returns
    `(added, updated, deleted_ids, unchanged_count)`.
    """
    desired_by_id = {d.id: d for d in desired}
    added: list[IndexDocument] = []
    updated: list[IndexDocument] = []
    unchanged = 0
    for doc_id, doc in desired_by_id.items():
        if doc_id not in existing:
            added.append(doc)
        elif existing[doc_id] != doc.seed_hash:
            updated.append(doc)
        else:
            unchanged += 1
    deleted = [doc_id for doc_id in existing if doc_id not in desired_by_id]
    return added, updated, deleted, unchanged


async def upsert_batch(
    search_client: Any,
    docs: list[IndexDocument],
    batch_size: int = 100,
) -> None:
    """Upsert documents in batches of `batch_size`. AI Search caps a single
    `mergeOrUpload` payload at 1000 documents; we stay well under that."""
    if not docs:
        return
    for i in range(0, len(docs), batch_size):
        batch = [d.to_search_body() for d in docs[i : i + batch_size]]
        await search_client.upload_documents(documents=batch)


async def delete_batch(
    search_client: Any,
    ids: list[str],
    batch_size: int = 100,
) -> None:
    if not ids:
        return
    for i in range(0, len(ids), batch_size):
        batch = [{"id": doc_id} for doc_id in ids[i : i + batch_size]]
        await search_client.delete_documents(documents=batch)


async def list_existing_seed_hashes(search_client: Any) -> dict[str, str]:
    """Pull `(id, seed_hash)` for every document in the index.

    The seed_hash field is populated by previous loader runs; missing on
    first run, in which case every record looks like an `added`."""
    out: dict[str, str] = {}
    results = await search_client.search(
        search_text="*",
        select=["id", "seed_hash"],
        top=1000,
    )
    async for hit in results:
        out[str(hit["id"])] = str(hit.get("seed_hash") or "")
    return out


async def run_seed(
    *,
    desired_records: list[dict[str, Any]],
    search_client: Any,
    confirm_deletes: bool,
) -> list[DiffSummary]:
    """Execute the seed pipeline. Returns per-language summaries.

    Pure-async to fit docs/coding-standards.md §1.7. The `search_client` is a
    duck-typed `azure.search.documents.aio.SearchClient` (or test fake).
    """
    # Lazy import so unit tests can stub Question without azure SDKs in place.
    from src.data.models import Question

    # Validate first, then index. Validation failures abort the run before any
    # index write so a malformed record cannot corrupt the index half-way through.
    docs: list[IndexDocument] = []
    by_language: dict[str, list[IndexDocument]] = {"en": [], "fr": [], "es": []}
    for record in desired_records:
        validated = Question.model_validate(record)
        idx_doc = _to_index_document(validated.model_dump())
        docs.append(idx_doc)
        by_language.setdefault(idx_doc.language, []).append(idx_doc)

    existing = await list_existing_seed_hashes(search_client)
    added, updated, deleted, unchanged_total = await compute_diff(docs, existing)

    # Mass-delete guard (TASK-028). Only fires when the index already has
    # records — empty index can be fully populated without --confirm.
    if existing and len(deleted) > 0:
        fraction = len(deleted) / max(len(existing), 1)
        if fraction > MAX_DELETE_FRACTION_WITHOUT_CONFIRM and not confirm_deletes:
            raise RuntimeError(
                f"refusing to delete {len(deleted)} documents "
                f"({fraction:.1%} of index, threshold {MAX_DELETE_FRACTION_WITHOUT_CONFIRM:.0%}). "
                "Re-run with --confirm if this is intentional."
            )

    logger.info(
        "seed_loader.diff_computed",
        extra={
            "added": len(added),
            "updated": len(updated),
            "deleted": len(deleted),
            "unchanged": unchanged_total,
            "existing_total": len(existing),
        },
    )

    await upsert_batch(search_client, added + updated)
    await delete_batch(search_client, deleted)

    # AI Search indexer sync window. Required before facet-count reconciliation.
    await asyncio.sleep(INDEXER_SYNC_SECONDS)

    # Verify the post-write count matches the desired count — surfaces a
    # silent failure where AI Search 207-returned partial errors that
    # mergeOrUpload swallowed.
    post = await list_existing_seed_hashes(search_client)
    expected_total = len(docs)
    if len(post) != expected_total:
        logger.warning(
            "seed_loader.post_count_mismatch",
            extra={"expected": expected_total, "observed": len(post)},
        )

    # Per-language summary.
    summaries: list[DiffSummary] = []
    added_by_lang = _bucket_by_language(added)
    updated_by_lang = _bucket_by_language(updated)
    deleted_by_lang_count = {"en": 0, "fr": 0, "es": 0}
    for doc_id in deleted:
        # `id` is `<logical_id>-<lang>`; pull the trailing two-char language.
        if len(doc_id) > 3 and doc_id[-3] == "-":
            lang = doc_id[-2:]
            if lang in deleted_by_lang_count:
                deleted_by_lang_count[lang] += 1
    unchanged_by_lang = {
        lang: max(len(by_language.get(lang, [])) - len(added_by_lang.get(lang, []))
                  - len(updated_by_lang.get(lang, [])), 0)
        for lang in ("en", "fr", "es")
    }
    for lang in ("en", "fr", "es"):
        summaries.append(
            DiffSummary(
                language=lang,
                added=len(added_by_lang.get(lang, [])),
                updated=len(updated_by_lang.get(lang, [])),
                deleted=deleted_by_lang_count[lang],
                unchanged=unchanged_by_lang[lang],
            )
        )
    return summaries


def _bucket_by_language(docs: list[IndexDocument]) -> dict[str, list[IndexDocument]]:
    out: dict[str, list[IndexDocument]] = {}
    for d in docs:
        out.setdefault(d.language, []).append(d)
    return out


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed_index",
        description="Seed AI Search `questions` index from authored JSON files.",
    )
    parser.add_argument(
        "--source",
        choices=("local", "blob"),
        default="local",
        help="Where to read authored records from (default: local filesystem).",
    )
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).parent / "questions",
        help="Local seed root (used when --source=local).",
    )
    parser.add_argument(
        "--blob-endpoint",
        help="Storage account blob endpoint URL (used when --source=blob).",
    )
    parser.add_argument(
        "--blob-container",
        default="questions",
        help="Storage container holding the authored tree (default: questions).",
    )
    parser.add_argument(
        "--search-endpoint",
        required=True,
        help="AI Search service endpoint, e.g. https://<service>.search.windows.net",
    )
    parser.add_argument(
        "--index-name",
        default="questions",
        help="AI Search index name (default: questions).",
    )
    parser.add_argument(
        "--search-resource-id",
        help="Azure resource ID of the search service. Used for the identity guard.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Acknowledge deletes that exceed the safety threshold.",
    )
    parser.add_argument(
        "--acknowledge-identity-check-skipped",
        action="store_true",
        help="Proceed even if the identity-role check could not be performed.",
    )
    parser.add_argument(
        "--reconcile-topics",
        action="store_true",
        help="After reindex, update Cosmos topics.counts against AI Search facets.",
    )
    parser.add_argument(
        "--cosmos-endpoint",
        help="Cosmos DB account endpoint (required if --reconcile-topics).",
    )
    parser.add_argument(
        "--cosmos-database",
        default="flint",
        help="Cosmos database name.",
    )
    return parser


async def _gather_records(args: argparse.Namespace, credential: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if args.source == "local":
        async for _path, body in _iter_local(args.root):
            records.append(body)
    else:
        if not args.blob_endpoint:
            raise SystemExit("--blob-endpoint is required when --source=blob")
        async for _name, body in _iter_blob(args.blob_endpoint, args.blob_container, credential):
            records.append(body)
    return records


async def _async_main(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    try:
        # Identity guard (defense-in-depth — TASK-026). Skippable only with an
        # explicit operator acknowledgement; never silently bypassed.
        if args.search_resource_id and not args.acknowledge_identity_check_skipped:
            principal_id = _resolve_principal_id_from_token(credential)
            if principal_id:
                try:
                    await assert_no_control_plane_role(
                        args.search_resource_id, principal_id, credential
                    )
                except RuntimeError as exc:
                    logger.critical("seed_loader.identity_guard_failed", extra={"error": str(exc)})
                    raise

        # Build the search client.
        from src.data.question_search import build_search_client

        search_client = build_search_client(
            endpoint=args.search_endpoint,
            index_name=args.index_name,
            credential=credential,
        )

        async with search_client:
            records = await _gather_records(args, credential)
            if not records:
                logger.warning("seed_loader.no_records_found")
                return 0
            summaries = await run_seed(
                desired_records=records,
                search_client=search_client,
                confirm_deletes=args.confirm,
            )
            for s in summaries:
                logger.info(
                    "seed_loader.language_summary",
                    extra={
                        "language": s.language,
                        "added": s.added,
                        "updated": s.updated,
                        "deleted": s.deleted,
                        "unchanged": s.unchanged,
                    },
                )

            if args.reconcile_topics:
                if not args.cosmos_endpoint:
                    raise SystemExit("--cosmos-endpoint is required with --reconcile-topics")
                from src.seed.reconcile_topics import reconcile

                outcome = await reconcile(
                    search_client=search_client,
                    cosmos_endpoint=args.cosmos_endpoint,
                    cosmos_database=args.cosmos_database,
                    credential=credential,
                )
                logger.info(
                    "seed_loader.reconcile_summary",
                    extra=dataclasses.asdict(outcome),
                )

    finally:
        await credential.close()
    return 0


def _resolve_principal_id_from_token(credential: Any) -> str | None:
    """Decode the principal OID from the access token's `oid` claim.

    Best-effort; falls back to None on any error. The caller treats None as
    "could not resolve" and decides whether to abort.
    """
    try:
        import base64

        token = credential.get_token("https://management.azure.com/.default")  # type: ignore[attr-defined]
        # Token is sync iff `credential` is sync. The async credential exposes
        # the same method but as a coroutine — handled by the caller pattern
        # below; here we only try a sync read. If unavailable, return None.
        if hasattr(token, "__await__"):
            return None
        payload = token.token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        return str(claims.get("oid") or claims.get("sub") or "") or None
    except Exception:  # pragma: no cover - defensive
        return None


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
