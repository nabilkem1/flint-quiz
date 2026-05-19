# Flint Quiz — operational Makefile (010-deployment TASK-203 / TASK-205 /
# TASK-209 / TASK-210).
#
# The Makefile is the load-bearing operator surface for deploy + smoke
# + rollback + pre-public-check. Each target invokes the same
# scripts the CI release pipeline does, so a local "is this ready?"
# run mirrors the gate that fires on the tag.
#
# Targets (idempotent unless noted):
#
#   make pre-deploy        — run the pre-deploy checklist (TASK-202).
#   make deploy            — `azd up`. Pre-/post-provision + post-deploy
#                            hooks run automatically.
#   make smoke             — re-run the post-deploy smoke matrix only
#                            (TASK-205).
#   make rollback TAG=v1.2.3
#                          — agent + index rollback (Cosmos NOT in scope).
#   make pre-public-check  — parses docs/pre-public-gate.md and refuses
#                            to tag `public-ready` unless every item is
#                            green (TASK-209).
#   make test              — full local test suite (PR + merge tier only;
#                            release tier runs on tag).
#   make eval-per-lang     — TEST-011 per-language gate.
#   make clean             — strip pycache / .pytest_cache (idempotent).

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

ENV ?= dev
PYTHON ?= python3

.PHONY: help
help:
	@awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z_-]+:.*## / { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ----------------------------------------------------------------------------
# Pre-deploy / deploy / smoke
# ----------------------------------------------------------------------------

.PHONY: pre-deploy
pre-deploy: ## Pre-deploy checklist (TASK-202).
	@bash infra/hooks/pre-deploy.sh

.PHONY: deploy
deploy: pre-deploy ## azd up with all hooks (TASK-203 / TEST-001).
	@command -v azd >/dev/null 2>&1 || { echo "azd not installed"; exit 1; }
	@azd env select $(ENV) || azd env new $(ENV) --no-prompt
	@azd up --no-prompt

.PHONY: smoke
smoke: ## Re-run the post-deploy smoke matrix (TASK-205).
	@bash infra/hooks/post-deploy-smoke.sh

.PHONY: rbac
rbac: ## Post-provision RBAC verification (TASK-121).
	@bash infra/hooks/post-provision-rbac.sh

# ----------------------------------------------------------------------------
# Rollback (TASK-210 / docs/rollback.md)
# ----------------------------------------------------------------------------

.PHONY: rollback
rollback: ## Roll back to the agent + index at TAG (Cosmos NOT rolled back).
	@if [[ -z "$(TAG)" ]]; then echo "usage: make rollback TAG=v1.2.3"; exit 1; fi
	@echo ">>> rollback: agent + index at $(TAG); Cosmos UNCHANGED (ADR-003)."
	@echo ">>> step 1/2 — agent: forward-deploy of $(TAG)"
	@git checkout "$(TAG)" -- src/ infra/ azure.yaml
	@azd deploy quiz-agent --no-prompt
	@echo ">>> step 2/2 — index: rebuild from authored Blob at $(TAG)"
	@$(PYTHON) -m src.seed.reconcile_topics --env $(ENV)
	@$(PYTHON) -m src.seed.seed_index --env $(ENV) --report
	@echo ">>> rollback complete. Verify with: make smoke"

# ----------------------------------------------------------------------------
# Pre-public gate (TASK-209)
# ----------------------------------------------------------------------------

.PHONY: pre-public-check
pre-public-check: ## Pre-public exposure gate (TASK-209).
	@$(PYTHON) tools/pre_public_gate.py --checklist docs/pre-public-gate.md --env $(ENV)

# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------

.PHONY: test
test: ## Full local pytest run (skips real-Cosmos tests without emulator).
	@$(PYTHON) -m pytest tests/ -q --tb=short

.PHONY: test-pr
test-pr: ## PR-tier subset (TASK-175 PR pipeline).
	@$(PYTHON) -m pytest tests/unit tests/test_no_answer_leakage.py tests/test_prompt_redaction.py \
	  tests/test_tool_allowlist.py tests/test_explanation_provenance.py \
	  tests/test_refusal_localization.py tests/test_tts_invariants.py \
	  tests/test_grading.py tests/test_language_resolution.py \
	  tests/test_multilingual_matrix.py tests/test_voice_normalization.py \
	  tests/test_negative_scenarios.py tests/smoke -q

.PHONY: eval-per-lang
eval-per-lang: ## Per-language correctness gate (TEST-011 / TASK-167).
	@$(PYTHON) -m pytest tests/eval -v

# ----------------------------------------------------------------------------
# Housekeeping
# ----------------------------------------------------------------------------

.PHONY: clean
clean: ## Drop __pycache__ + .pytest_cache.
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .mypy_cache
