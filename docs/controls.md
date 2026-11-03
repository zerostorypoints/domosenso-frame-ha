# Assistant room controls (unreleased)

This extension is negotiated as `controlsVersion: 1` in the existing Frame
profile, `control-action.v1` / `control-result.v1`. It requires the bridge change
that implements these contracts and a compatible native client. It is not yet a
published release or a claim of physical hardware verification.

## Setup and administrator review

Controls default to disabled. Opening the integration panel, selecting read
sources, future read scopes, pairing a tablet, or updating the app grants no
actuator access. In the existing **Assistant sources** panel, review **Assistant
controls**, then select exact current sources/actions and explicitly enable them.
For vanilla HA, select the light/cover in the existing manual source editor first.
Use HA entity/device area assignments; an explicit HA area override resolves a
conflicting component `roomKey`. Labels and aliases help recognition but never
create room membership. No Android room settings or YAML edit is required.

A source with missing/ambiguous cover class needs an explicit mapping to blind,
curtain, shade, shutter or awning. Verified awnings cannot be renamed into another
class. Garage, gate, door and damper are excluded. STOP is available only when
advertised by the entity. Unknown registry identities and opaque template lights
without a declared group binding are unavailable for controls.

For an awning without component wind policy, select a wind binary sensor, a
last-heard input_datetime or timestamp sensor, and maximum age (positive, at most
604800 seconds). Save mapping changes, then review and grant the updated binding.
Installed component wind policy remains authoritative. Extension requires wind
`off`, READ permission on both sources, and a nonfuture report no older than the
policy. Timestamp sensors supply timezone-aware ISO timestamps. Normal dated HA
input_datetime helpers supply their HA-local epoch `timestamp` attribute; their
naive state strings are never treated as UTC. Missing, unknown, alarm, invalid or
stale evidence blocks extension. Retraction and supported STOP need no safe-wind
assertion. A fresh safe report is not a weather guarantee.

The stored setting is `assistant.controls`:

```json
{
  "enabled": false,
  "grants": [],
  "mappings": []
}
```

The admin-only `studio_mappings` response includes `controlCatalog.sources` and
`controlCatalog.areas`. A reviewable source includes its entity, canonical
members, label, aliases, room and proposed `grant` (or an unavailable reason).
Grants have `source`, `entity`, `bindingRevision` and a `capabilities` object whose
keys are fixed capability IDs and values are integer revision 1. Administrators
may select a subset. Mappings contain `source`, `entity`, optional `area`,
`coverClass`, and `guard` with the compiler's `wind`, `last_heard` and
`max_age_seconds` shape. Controls-only updates preserve every existing read grant;
legacy read-only saves preserve controls. Saves use the existing revision-checked,
admin-only transaction and revalidate bindings after persistence.

A grant hashes the exact source, registry identity, installed provenance,
upstream group, current recursive member graph, member registrations/areas,
room and cover classes/features. Replaced or expanded actuator sources require
review/regrant. Awning grants separately store an optional `safetyRevision` for
the exact extension wind policy and provider registry identities. Missing, disabled
or replaced wind providers block extension with `wind_block`; already granted
retraction and STOP keep their unchanged actuator binding. A replacement provider
never inherits extension consent. Review its current wind binding in the panel
and save before extending. A grant made without a valid wind binding cannot
automatically enable extension when that provider later appears.
Missing or unknown membership never becomes an individual-light grant. A room
request deduplicates a separately mapped member under its granted proxy, and
sends to the original proxy entity so component indirection remains intact.
Incomparable overlapping sets are not offered as room targets. The same rules
apply to HA cover groups: every effective node needs READ+CONTROL, a verified
eligible class, current availability, and the requested feature. Allowed actions
are the intersection of members' features. Window coverings and awnings cannot
be mixed in one group. A group's explicit class mapping does not relabel unknown
or excluded children. Groups with missing/empty/opaque membership fail closed.
A missing aggregate HA group class can be mapped explicitly after its members
have eligible HA classes.

## Native protocol

Canonical generated examples: `tests/fixtures/shared-controls.json`.
Closed schema: `custom_components/domosenso_frame/control-wire.schema.json`.
Regenerate with `.venv/bin/python tests/generate_control_wire.py`; the generator
calls the production handlers and executor. Examples include positive light,
cover and awning negotiation/execution, ambiguity, partial room scope with and
without choice, partial dispatch, stale revisions, wind blocking and uncertainty.
Source sidecars and hashes are in `tests/fixtures/assistant-actions/provenance.json`.
All identifiers and states in these fixtures are fictional.

Use the existing HA authenticated WebSocket and P-256 `begin` / `finish` proof,
with administrator `approve`. Every new connection proves its key again. Both
routes require `endpoint` and `deviceId` matching that connection's current
approved device proof. No token in the control payload replaces this proof.

Negotiation request (`id` and `type` are the HA WebSocket envelope):

```json
{"id":1,"type":"domosenso_frame/control_targets","version":1,
 "endpoint":"frame","deviceId":"approved-device-id",
 "supported":[{"actionType":"control-action.v1","resultType":"control-result.v1"}]}
```

The result contains `version`, `actionType`, `resultType`, `requestIdPrefix`,
`expiresAt` and `targets`. Each target has opaque `id`, `revision`, `kind`
(light/cover/awning), `scope` (subject/room), localized-or-string `label`, `aliases`,
nullable `room` (`id`, `label`, `aliases`), `capabilities` (`id`, `revision`),
`memberIds`, `partialScope` and `limitations`. No HA entity/service names appear
on the native wire. `memberIds` identify the canonical subject targets in the
same response, so a client can display exactly which subjects will receive calls.

Fixed revision-1 capabilities are `lights.turn_on`, `lights.turn_off`,
`covers.open`, `covers.close`, `covers.stop`, `awnings.extend`, `awnings.retract`,
and `awnings.stop`. Awning extend/retract map to open_cover/close_cover, never
toggle, index, arbitrary service, percentage, scene or schedule.

Action request:

```json
{"id":2,"type":"domosenso_frame/control_action","version":1,
 "endpoint":"frame","deviceId":"approved-device-id",
 "capability":"lights.turn_on","revision":1,
 "targetId":"negotiated-opaque-id","targetRevision":"negotiated-revision",
 "requestId":"issued-prefix:client-unique-suffix"}
```

Append a unique `[A-Za-z0-9_-]{1,128}` suffix to the **exact** `requestIdPrefix`
(which already ends in `:`). An unchanged retry uses the same complete request.
Never replay a request after expiry or substitute a new prefix to retry an
uncertain old command. Negotiate afresh for a new user command. Altering a stored
request ID's payload returns `request_conflict` without another dispatch.

For ambiguous labels, show distinct negotiated candidates and wait for a user
selection tied to the original action and current candidate revision. Do not
execute all matches. If `partialScope` is true, show the configured member labels
from `memberIds`, explain that other known room sources are outside this scope,
and offer **only these configured subjects** or cancel. Only after that explicit
choice add `"scopeChoice":"configured"` to this action. Without it the bridge
returns `partial_scope` and dispatches nothing. Missing/old choice, expired epoch,
or changed revision cannot authorize a room action. Do not cache this choice as
a general preference or silently resubmit a rejected action.

The result has `version`, `resultType`, the original request/action/target fields,
`status`, `reason` and `outcomes`. Each outcome has opaque `targetId`, `status`,
`reason`, and `assumedState`. Statuses are:

- `accepted` / `command_sent`: the service returned; say “Sent the command”
  (“Wysłano polecenie”), never “finished”.
- `moving` / `motion_observed`: a fresh, same-context non-assumed motion state.
- `confirmed` / `state_observed`: a fresh, same-context non-assumed HA target state.
  Groups require matching evidence for every effective leaf; an aggregate group
  state alone cannot confirm completion. Any member's assumed state marks the
  group outcome as assumed and keeps it at accepted.
  This is HA-reported evidence, not independently measured physical movement.
- `failed`: dispatch was denied or a remaining target failed preflight.
- `unknown`: the provider timed out/raised, persistence became uncertain, or a
  pending request was recovered. Do not automatically retry.

Reasons include `stale_revision`, `access_denied`, `unavailable`, `unsupported`,
`wind_block`, `partial_scope`, `partial_failure`, `service_error`,
`outcome_unknown`, `request_conflict`, `ledger_full`, and `storage_failed`.
Malformed or unproven WebSocket requests return `frame_denied`. Display every
per-target outcome for partial results; never present these as whole-room success.

## Execution, durability and bounds

All effective members require current HA READ and CONTROL. Safety inputs require
READ. Every target is preflighted before the first service call. Current device
proof, endpoint, mapping/grant revisions, membership, permissions, service
availability, feature support, target state and wind freshness are checked again
after awaits and before each dispatch. Calls use the authenticated HA user's
context. No protection/automation is disabled and no target state is simulated.

The private `.storage/domosenso_frame_controls` store is verified before dispatch.
A global execution lock coalesces concurrent duplicates. At most 1024 requests are
retained in a ten-minute negotiation epoch; a full epoch returns `ledger_full`.
After expiry a new random epoch invalidates every old target revision/request
prefix before pruning records. The new epoch and pending record must be durable
before dispatch. Old IDs never become new requests after pruning or restart.
Pending records retain an unknown outcome for each target and are never replayed.
Source counts are limited to 100, a group graph to 128 nodes and 512 edges, and each service
await to ten seconds. Graph traversal memoizes completed nodes only within the
current resolution and checks active cycles, bounding shared-DAG work without
stale global caches. No background/offline action queue exists.

## Verification and limits

Run `python -m pytest -q tests` and `npm test`. The ordinary Python environment
intentionally skips the HA-only lifecycle test; this does not replace runtime
acceptance. Run the separate fresh runtime harness documented in
`tests/controls_ha_smoke.py`. It boots HA **2026.9.2** with a temporary fictional
installation, authenticates real WebSockets, uses real input_datetime services,
and intercepts every light/cover service. It checks exact allowed calls and
zero-call denials, including mid-dispatch revocation and disk-ledger reload. It also creates real
HA CoverGroup entities with fictional leaves to check group deduplication,
excluded/expanded members and overlapping sets, and removes/replaces a wind
registry entry to verify safe directions and explicit extension regrant.
The offline container can log optional network/zeroconf discovery errors because
it has no network interface; no production endpoint is contacted.

Physical cover movement, radio reliability and speech/native UI acceptance remain
outside this bridge harness. Assumed states are explicitly marked and never
reported as completion. The pre-existing happy-dom development advisory is not
changed by this feature.
