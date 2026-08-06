---
title: Custom Deep API Base URL - Plan
type: feat
date: 2026-08-06
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Custom Deep API Base URL - Plan

## Goal Capsule

- **Objective:** Let `scan --deep` use a configured self-hosted or OpenAI-compatible LLM endpoint.
- **Authority:** The approved config-only design and the repository's existing deep-mode contracts govern implementation.
- **Execution:** Implement, verify against the local Codex OpenAI Proxy, and open an upstream pull request only after all gates pass.
- **Stop condition:** Stop before publication if the full suite or live proxy proof fails.

---

## Product Contract

### Summary

Add an optional base URL beside the existing deep model setting and apply it to every deep judge and rewriter request without changing default provider behavior.

### Problem Frame

Deep mode currently accepts any LiteLLM model ID but always uses the provider's default endpoint. Users cannot route the same deep workflow to a self-hosted OpenAI-compatible API.

### Requirements

- R1. `[deep].base_url` is an optional string setting beside `[deep].model`.
- R2. When configured, the base URL applies to both conflict judging and description rewriting.
- R3. When unset, deep mode keeps its current provider endpoint and credential behavior.
- R4. The configured URL passes through unchanged; drskill does not normalize, probe, or append path components.
- R5. Ordinary scans remain LLM-free and do not import or construct the deep client.
- R6. Documentation explains OpenAI-compatible model naming, the endpoint setting, and the existing API-key requirement.
- R7. A successful live test against the local Codex OpenAI Proxy is required before an upstream pull request is opened.

### Scope Boundaries

- No CLI base-URL flag or CLI-over-config precedence.
- No credential storage, authentication bypass, URL validation, dependency change, or proxy-service modification.
- No verdict-cache key or invalidation changes.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Use `[deep].base_url` as the public key.** (session-settled: user-approved — chosen over a CLI-only or CLI-override surface: endpoint choice should remain durable beside the model and cached team verdicts.)
- KTD2. **Translate `base_url` to DSPy/LiteLLM's `api_base` argument.** DSPy 3.2.1 documents `api_base` as the per-LM custom-endpoint knob and forwards it to LiteLLM.
- KTD3. **Omit `api_base` when configuration is absent.** Passing no keyword preserves the exact existing endpoint resolution path.
- KTD4. **Preserve credential preflight.** OpenAI-compatible endpoints still require a non-empty `OPENAI_API_KEY`; endpoints that ignore authentication can use a non-secret placeholder such as `not-used`.
- KTD5. **Keep cache identity unchanged.** Existing verdict keys exclude model identity, so endpoint identity must not create a new invalidation rule in this change.

### Sequencing

Implement the configuration contract first, then the shared LLM boundary, CLI wiring, documentation, offline verification, and finally the live proxy proof.

---

## Implementation Units

### U1. Add and propagate the optional endpoint

- **Goal:** Carry the optional base URL from the ledger through both deep program builders to the shared DSPy LM construction.
- **Requirements:** R1-R5; KTD1-KTD5.
- **Files:** `src/drskill/ledger.py`, `src/drskill/cli.py`, `src/drskill/deep_llm.py`.
- **Approach:** Add a nullable config field with a `None` default, retain backward-compatible builder defaults, and conditionally pass the configured value to `dspy.LM` as `api_base`.
- **Test scenarios:** Parse absent and configured values; verify both builders receive the value; verify configured LM construction includes `api_base`; verify unset construction omits it; retain the no-key and plain-scan contracts.

### U2. Document and prove the integration

- **Goal:** Make the endpoint contract discoverable and prove it against an actual OpenAI-compatible service.
- **Requirements:** R6-R7.
- **Files:** `README.md`, existing ledger/deep/CLI test modules.
- **Approach:** Document `[deep].base_url`, `openai/<model>`, unchanged key handling, and a proxy example that includes `/v1` because that service requires it.
- **Test scenarios:** Run the full suite with deep dependencies; use a fresh temporary scan root and cache with `openai/gpt-5.6-luna`, `OPENAI_API_KEY=not-used`, and `http://127.0.0.1:9208/v1`; require a newly persisted verdict and no model-call failure.

---

## Verification Contract

- Run the targeted ledger, deep-client, and deep-CLI tests during implementation.
- Run `uv run --frozen --extra deep pytest` as the full repository gate.
- Run the live proxy proof against a fresh temporary fixture so an existing cache cannot produce a false success.
- Prove rewriter routing deterministically in unit tests because a live model may classify the fixture as `distinct` and never invoke rewriting.
- Confirm `git diff --check` and a scope-only working tree before commit.

---

## Definition of Done

- The optional config is backward compatible and reaches both deep programs.
- Default endpoint and credential behavior are unchanged.
- Documentation matches the implemented contract.
- Targeted tests, the full suite, and the fresh-cache live proxy proof pass.
- No generated dependency, credential, cache, or proxy-service changes remain in the diff.
- A focused commit is pushed to Alex's fork and a ready-for-review PR targets `dbreunig/drskill:main`.
