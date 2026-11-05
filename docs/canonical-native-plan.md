# Canonical native metadata integration

Scope approved in the coordinating conversation, 2026-11-05. Framework SPEC-037 and
PR #237 define the canonical authoring/projection boundary. This slice consumes the
reviewed framework schema and fixtures at the commit in the vendored provenance file.
Production, physical tablets, release publishing and merging prerequisite PR #4 are excluded.

## Plan and ledger

1. Validate canonical private schema-2 input and normalize reviewed generic measurement
   primitives without an ID list. Preserve exact inventory/registry/grant/permission
   checks and current v1 behavior. RED→GREEN observed for two successive capabilities,
   alias propagation, malformed data and changed source identities.
2. Reuse authorized specialized v1 evaluators, with canonical vocabulary decorating the
   already authorized descriptor. Metadata must not add selectors or relax dispatch.
   Adopt the shared assessment/operability fixtures and canonical fair/poor helper keys,
   preserving raised/high v1 output. RED→GREEN observed for live helpers/conflicts and
   the portable fixtures.
3. Verify an isolated HA 2026.9.2 instance over paired WebSocket, then export fictional
   wire data for unchanged native parser/matcher/session tests in PL/EN. Test metadata
   reload with unchanged component version and code, explicit new-source approval,
   wrong-unit/restored readings, stale revisions, source moves and revocation.
4. Run ordinary Python/JS suites, isolated transport and independent review. Record
   exact revisions and hashes before publishing task branches/PRs. No default v2
   activation: F2 voice/hardware and version-combination acceptance remain separate.

Ruling: preserve the existing experimental list-shaped private input through its own
strict historical schema during the unreleased transition. The canonical dictionary
shape is detected and validated independently; invalid canonical input cannot fall
back to that reader. Only the framework canonical form is new authoring. This is an
explicit migration adapter, not another current source of component semantics.

Ruling: canonical descriptions retain up to 300 characters in the bounded wire to
preserve existing component explanations. Matching anchors/titles remain bounded at
120. Frame validation changes together in its own reviewed slice; old protocol-1
clients are unaffected. The historical unpublished v2 prototype is not a supported
released-client combination.

Ruling: generic measurement source attributes remain unsupported rather than being
silently read as entity state. Per-source maximum age may tighten the capability's
freshness limit but cannot extend it. No metadata can invent an evaluator or service.

Verification so far: 413 Python tests passed; one optional real-core timer lifecycle
test skipped outside a Home Assistant Python environment. All 20 JS tests passed.
The pinned HA 2026.9.2 container, with no external network or host-published ports,
passed canonical paired-WebSocket additions/reload/grants/PL-EN wire/area/revocation,
and the existing experimental metadata/plant-assessment smoke. Integration code
hashes before/after the data-only addition are identical. No tablet was contacted.
