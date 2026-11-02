# Shared Frame timers

This development change adds opt-in shared countdowns backed by Home Assistant's
core Timer helpers. Existing v1 assistant reads and old clients remain compatible.
The bridge never creates an independent countdown scheduler or exposes a generic
service gateway. HA remains authoritative when its normal UI or automations act.

## Setup without YAML

Requires Home Assistant 2026.9.2 or newer and this integration version. In HA,
open **Settings → Devices & services → Helpers → Create helper → Timer** and
turn on **Restore**. Existing helpers, including helpers installed by the DomoSenso
timers component, can be used unchanged.

Open **DomoSenso Frame → Timers / Timer / Minutniki**, choose the Frame endpoint,
and add up to 16 helpers. Select **Allow timer start, pause, resume and cancel**
only if that endpoint should be writable. This grant defaults off. Select one
approved paired tablet as the alarm target, or leave it empty for foreground
sound only. Save explicitly. No YAML, terminal or catalog metadata is required.
The UI is translated into English, German and Polish. Shared helpers must use the
same alarm target on every endpoint; changing targets requires clearing/reselecting
the other mappings as needed. Helpers with the reserved `_controls` or `_rotation`
suffix, a `component: ramka` marker, or an endpoint runtime binding are excluded.

Native commands require pairing and the HA user's READ and CONTROL permissions.
Classic browser snapshots, delivery claims and acknowledgements require an active
HA user with READ permission; pairing and the write grant are unnecessary. Metadata
cannot grant access. A revoked device is no longer returned as a native alarm target;
its explicit assignment still blocks browser delivery until an administrator clears it.
The Android client must independently have notification/alarm permission; mapping a
target does not grant OS permission or prove background delivery.

## Protocol

The native routes below use the existing authenticated endpoint/device-proof checks.

| Route | Purpose |
| --- | --- |
| `assistant_capabilities` / `assistant_query` | Existing v1 envelopes; `timers.status` (`timer-query.v1` / `timer-status.v1`) and `ramka.current_time` (`time-query.v1` / `time.v1`) |
| `assistant_actions` | Negotiate only `timer-action.v1` / `timer-result.v1`, returning explicitly writable, READ+CONTROL authorized mapped subjects |
| `assistant_action` | Fixed `timers.start`, `timers.pause`, `timers.resume`, `timers.cancel` |
| `timer_acknowledge` | Persist a known run receipt and return `{version,asOf,timer}`; an old run cannot acknowledge a replacement |
| `subscribe` | `{kind:"timers"}` invalidations; fetch an authoritative status snapshot before further writes |

`tests/fixtures/shared-timers.json` contains fictional request/response examples
from the actual adapters. Regenerate with `python tests/generate_timer_fixture.py`.

Actions require `version:1`, `endpoint`, `capability`, `revision:1`, `subjectId`,
`requestId`, `expectedStateRevision`, `expectedRunId` (null for no run), and
`arguments`. Start accepts integer `durationSeconds` from 1 to 86400 and optional
boolean `confirmReplace`. Other actions accept an empty object. Replacement of an
active or paused timer requires a new request ID with current run/revision and
`confirmReplace:true`. Invalid/denied requests fail with a WebSocket error.

Results are `applied`, `conflict`, `confirmation_required` or `outcome_unknown`.
The result includes the authoritative observed timer fact and server `asOf`.
Opaque timer subjects are stable across endpoints in the same HA installation;
entity IDs never appear in native client facts. Opaque revisions are equality tokens,
not sortable counters: clients serialize snapshots and discard superseded fetches.
A stale request returns a conflict rather than a service call.

Timer facts distinguish state from outcome (`none`, `completed`, `cancelled`,
`inferred_completed`, `unknown`). They contain duration/remaining seconds, deadline,
run ID, state revision, designated alarm device and acknowledged state. A completed
idle run retains its last deadline so clients can apply the 60-second missed-alert
cutoff. Pausing clears the deadline. A completion of a paused timer uses HA's
finished timestamp. Idle alone never establishes confirmed completion.

Time answers use current HA UTC and HA's configured IANA timezone. `ha_clock` is a
closed runtime source accepted only for `ramka.current_time`, with no entity facts,
free-form templates or HA profile changes. Timezone changes invalidate the timer/time
snapshot context through the subscription. Optional compiler text loads only from
`domosenso/assistant-runtime/ramka.yaml`, separately from legacy read artifacts,
with fixed-directory, no-symlink, bounded schema and installed identity checks.
Only the clock capability is accepted; bindings may retain frame labels but have
no entity fields or evaluation policies. Missing metadata uses built-in text.
Localized speech/display belongs to the client and the authenticated user's language
preferences.

## Classic HA browser interface

These fixed WebSocket routes use HA's authenticated connection directly. Each request
requires an active user, READ permission, an existing core timer registry entry,
all helper exclusions above, and an explicit timer mapping in at least one Frame
endpoint. Multiple endpoints must agree on the assigned alarm target. No browser
route negotiates timer controls or invokes a core service.

| Route (prefix `domosenso_frame/`) | Request fields | Result |
| --- | --- | --- |
| `timer_browser_snapshot` | `version:1, entities:[...]` (0–16 unique timer IDs) | `{version:1,asOf,installationId,timers:[{entity,label,timer}]}` |
| `timer_browser_claim` | `version:1, subjectId, runId` | `{version:1,asOf,claimed,timer}` |
| `timer_browser_acknowledge` | `version:1, subjectId, runId` | `{version:1,asOf,timer}` |

Normal WebSocket `id` and `type` fields apply. Unknown fields/types are rejected.
Snapshots omit unmapped or unauthorized helpers and return only requested permitted
entities, in request order. Their `timer` is the native fact shape; entity IDs occur
only in the classic wrapper. An empty selection still provides server time. `asOf`
is sampled once per snapshot and used for every fact's remaining-time calculation.
Clients anchor `deadline - asOf` to a monotonic clock on receipt, avoiding the browser
wall clock and rounded `remainingSeconds`. Refresh after reconnect and discard
superseded responses. Response transit time remains a client synchronization limit.

Only `claimed:true` authorizes opt-in foreground sound. It requires the current idle
run to be `completed` or `inferred_completed`, unacknowledged, with a retained deadline
between zero and 60 seconds before server time, and **no assigned native alarm target**
(including a revoked target). Stale runs, duplicates and ineligible outcomes return
`claimed:false` with current permitted state. Access/validation/storage failures return
`frame_denied` (HA may reject malformed route schemas earlier with `invalid_format`);
none authorize sound. Mapping or target changes during a save fail
closed, as do permission/source revocation and bridge unload.

A separate per-run `browserDeliveries` tombstone is persisted and verified before
returning true. Claims serialize across tabs and users, survive restarts, and never
count as dismissal. An uncertain save or lost response may suppress delivery; clients
must not retry sound without a new true claim. Acknowledgement uses the shared native
run receipt, returns current state, and cannot dismiss a replacement run. The bridge
plays no sound; classic clients own opt-in, visibility, a maximum 30-second tone,
and stopping on dismissal or invalidation. Native wire remains unchanged.

## Durability, concurrency and alarms

The private `.storage/domosenso_frame_timers` envelope holds observations, run
identities, shared acknowledgement receipts, browser delivery tombstones, and pending/result request records. Never publish this file.
Each pending record is verified on disk before a service invocation; results are
verified before an acknowledgement is returned. A retry with the same endpoint and
request ID returns its durable result. Different content with that ID is rejected.
An interrupted pending request returns `outcome_unknown` after restart and **never
replays the service**. The caller must refresh status and explicitly decide whether
a new request is appropriate. A lost connection never proves cancellation.

Bridge commands serialize per helper and recheck mappings, pairing and permissions
around awaits. Direct HA actions bypass this lock. State changes and core lifecycle
events are observed, read back and reconciled; an observed intervening change returns
a conflict. This is not global compare-and-swap against arbitrary HA automations.
There is necessarily a boundary between returning a result and a later HA action.

Startup reconciliation preserves restored active/paused runs. An idle helper with
a previously active elapsed deadline yields `inferred_completed` unless a matching
HA finish event was observed. Missing/unavailable state during restoration does not
erase that evidence. Clients must not sound for cancelled or unknown outcomes.
Recent unacknowledged completion may alert once on the designated tablet; older
than 60 seconds must stay silent. Clients bound sound to 30 seconds, share native/
WebView receipts, disable writes offline and reconcile obsolete alarms on reconnect.
An offline tablet cannot know about a later remote cancellation.

Request IDs are retained rather than silently expiring deduplication. At 100,000
requests the executor refuses new commands (reads and existing retries continue).
There is deliberately no automatic ledger reset: deleting it while clients may
retry destroys the no-replay guarantee. Plan an installation/client identity reset
before administratively archiving a full ledger. Observation/run/receipt history
is private integration state and must be included in backups.

## Verification and limits

Run `python -m pip install -r requirements-test.txt`, `python -m pytest -q tests`,
`npm ci --ignore-scripts`, and `npm test` for the standalone tests. The suite covers
revocation during lock/save/service waits, stale revisions, conflicting/duplicate
requests, persistence failure, pending recovery, direct HA races, same-second
replacement, downtime inference, old receipts, source validation and translated UI.

With an independent HA 2026.9.2 Python environment and the test dependencies,
run `python tests/timers_ha_smoke.py`. It creates a temporary fictional installation,
a YAML core helper and a UI-created helper, pairs an ordinary HA user, and exercises
actual registered WebSocket handlers and core services. It also exercises an unpaired
READ-only HA user through the browser snapshot/claim/ack routes, concurrent claims,
server timestamps and persisted delivery suppression after executor reload. It deletes only its own
temporary directory. `--no-listener` omits the HTTP TCP listener for restricted CI;
that mode does not verify TCP/WebSocket transport. No existing HA configuration is
read. An existing HA runtime may lack unrelated auto-loaded component dependencies.

The implementation was exercised in that in-process mode on HA 2026.9.2. Android,
physical tablets, Doze, reboot sound reliability, full process restart, browser visual
QA and production deployment are separate acceptance work; unit tests do not prove
hardware alarm delivery. A fresh technical review is required before release.
