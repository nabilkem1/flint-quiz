# Secrets Inventory — Flint Quiz

**Purpose**: The authoritative list of every secret the runtime consumes, where it lives, who reads it, and how it is rotated. The default for v1 is **zero secrets in code** — Managed Identity to every Azure service.

**Owner**: Platform + Security. **Review cadence**: per release; whenever a new dependency is added.

**Cross-references**: [`specs/005-security-model.md`](../specs/005-security-model.md) (SEC-004, SEC-013), [`infra/README.md §14`](../infra/README.md), [`tasks/007-security.md` TASK-122](../tasks/007-security.md), [`tasks/001-infrastructure.md` TASK-007](../tasks/001-infrastructure.md).

---

## 1. Default Posture — Zero Secrets

v1 uses Managed Identity (UAMI) for every Azure-service access:

| Azure service | Access path | Identity | Auth |
|---------------|-------------|----------|------|
| Cosmos DB | `azure-cosmos` SDK | `uami-agent-*` | Entra (data-plane RBAC, `disableLocalAuth=true`) |
| AI Search | `azure-search-documents` SDK | `uami-agent-*` (read), `uami-indexer-*` (write) | Entra (`disableLocalAuth=true`) |
| Key Vault | `azure-keyvault-secrets` SDK | `uami-agent-*` | Entra RBAC (`Key Vault Secrets User`) |
| App Configuration | `azure-appconfiguration` SDK | `uami-agent-*` | Entra (`disableLocalAuth=true`) |
| App Insights | `azure-monitor-opentelemetry` SDK | `uami-agent-*` | Entra (`Monitoring Metrics Publisher`) — connection string from env is accepted (not a secret per Microsoft guidance) |
| Storage (Blob) | `azure-storage-blob` SDK | `uami-indexer-*` | Entra (`Storage Blob Data Reader`, `allowSharedKeyAccess=false`) |
| Foundry project | `azure-ai-projects` SDK | `uami-agent-*` | Entra |

**No connection strings. No keys. No SAS tokens for runtime paths.** CI grep step (`tasks/007 TASK-120`) blocks any of `AccountKey=`, `AccountEndpoint=...;AccountKey=`, `SharedAccessSignature`, `ApiKey=` from entering the repo.

---

## 2. Runtime Secrets Currently in Use (v1)

**None.** The default posture in §1 covers all v1 dependencies.

If a future dependency requires an API key (e.g., a third-party speech model bypassing Foundry Realtime), it goes here.

---

## 3. Secret Slots Reserved (placeholder; unused in v1)

| Slot | Purpose | Where it would live | Who would read | Rotation |
|------|---------|---------------------|----------------|----------|
| _(none in v1)_ | — | Key Vault `kv-flint-<env>-<region>` | `uami-agent-*` via `Key Vault Secrets User` | Automated rotation via Key Vault rotation policy where supported |

---

## 4. Non-Secret Environment Values

These are read from environment variables / AppConfig and are explicitly **not** considered secret:

| Value | Source | Why not secret |
|-------|--------|----------------|
| App Insights connection string | env var (CI-injected from Bicep output) | Per Microsoft guidance: not sensitive (it identifies the workspace, not authenticates writes — auth is via MI) |
| `WORKLOAD=flint` | env var | Identity/tag |
| `ENV=prod\|qa\|dev` | env var | Topology |
| `AZURE_CLIENT_ID` (UAMI client ID) | env var | UAMI identity is public; auth is via Entra federation |
| AppConfig endpoint URL | env var (Bicep output) | Endpoint, not auth |
| Search endpoint URL | AppConfig | Endpoint, not auth |
| Model deployment name | AppConfig | Configuration, not auth |
| Supported-languages allowlist | AppConfig | Configuration |
| `voice:*` config keys (idle timeout, max session, etc.) | AppConfig | Configuration |
| `retention:*` config keys | AppConfig | Configuration |

---

## 5. Key Vault Wiring

`kv-flint-<env>-<region>` is provisioned with:

- **RBAC authorization** (`enableRbacAuthorization=true`) — no access policies.
- **Soft-delete** enabled (90-day window).
- **Purge protection** enabled (prod + qa).
- **Local auth disabled** for the runtime path — MI only.

Access wrapper: `src/data/keyvault_client.py` (`tasks/007 TASK-122`):

- `DefaultAzureCredential` resolves to the runtime UAMI.
- Fetched secrets cached in-process with 10-minute TTL.
- Secrets are never written to disk.
- Secret values never appear in log messages (CI lint: `tasks/007 TASK-120` greps for `os.environ["...KEY..."]`-style patterns).

---

## 6. Rotation

- **Key Vault secret history**: 90 days soft-delete retention.
- **Automatic rotation** is configured on any Key Vault secret backed by an Azure resource that supports it (Storage SAS tokens, etc. — none in v1).
- **Manual rotation cadence** for any future API-key slot: 90 days, calendar-driven, owner = Security.

Rotation does not require code change — the in-process TTL cache picks up the new value within 10 minutes (cache TTL).

---

## 7. CI Enforcement

CI gate (`tasks/007 TASK-120`):

```bash
# Block secret-shaped strings from entering the repo
PATTERNS="AccountKey=|AccountEndpoint=.*AccountKey=|SharedAccessSignature|ApiKey=|api_key=|API_KEY=|client_secret"
git diff --cached | grep -E "$PATTERNS" && exit 1 || exit 0
```

Exceptions: `*.md` files in `docs/` may reference these patterns descriptively (this file is an example). The grep excludes `*.md`.

---

## 8. Adding a New Secret (Process)

If a new dependency in any future PR requires a secret:

1. Open an ADR explaining why MI is not viable for the dependency.
2. Reserve a slot in §3 above with the rotation policy.
3. Provision the Key Vault entry via Bicep (not portal).
4. Grant the runtime UAMI `Key Vault Secrets User` on the specific secret URI (least privilege; never vault-wide if avoidable).
5. Read via `src/data/keyvault_client.py`.
6. Update this document (move the row from §3 to §2 on the day the secret is in use).
7. Notify Security.

**Do not** add a secret to env vars, AppConfig, or `.env` files. AppConfig is not a secret store.
