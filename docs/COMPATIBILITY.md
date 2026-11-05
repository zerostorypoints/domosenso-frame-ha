# Compatibility

The existing assistant read contract remains version 1. Shared timers add distinct
negotiated actions, optional endpoint `timers:{writeEnabled,items}` mappings, and a
closed `ha_clock` read source. They do not broaden SPEC-029 read authorization.
Read-only sessions may inspect explicitly mapped timers but cannot negotiate writes.
No timer mapping means no timer command capability. HA clock reads need no helper.

Core helper and in-process handler verification targets HA 2026.9.2. The bridge
requires a core `timer` entity registry provider for mapped sources; internal Frame
control/rotation helpers are excluded. Private compiler action profiles are bounded,
non-executable, identity-verified descriptions. Manual vanilla mappings use the same
executor and permission checks without requiring compiler metadata or YAML.

Old clients and old read profile files remain supported for at least one minor
release. New clients must hide/disable unsupported command revisions and refresh
authoritative status on reconnect. Network transport and physical Android alarm
acceptance remain separate from this repository's unit/in-process tests.

Runtime clock text is isolated in `domosenso/assistant-runtime/ramka.yaml`; legacy
`assistant/ramka.yaml` retains only old definitions/translations. Old strict loaders
can therefore continue reading it without a bridge-first rollout requirement.
The new runtime loader accepts only `ramka.current_time` with `ha_clock` and checks
the same installed identity. Metadata remains optional and cannot grant access.

Classic clients use three fixed `timer_browser_*` WebSocket routes under normal HA
user authentication, without native pairing. READ-only users can snapshot mapped
core helpers, claim one recent unacknowledged run with no assigned native target,
and acknowledge known runs. Browser routes do not require CONTROL or invoke core
services. Native facts remain opaque; only the classic snapshot wrapper includes
requested permitted entity IDs. An older bridge has no browser delivery support.


The optional `assistant.source_policy` extension is version 1. Absent means legacy/explicit
semantics. Scope tuples (`component`, `kind`, `roles`, read `capabilities`, `exclude`) are validated
server-side and cannot grant actions. Omitted policy fields on older editor saves are preserved.
Dynamic subject identity follows the component key, not its translated label. Existing display v2
and assistant v1 response formats remain unchanged; metadata refresh does not require new pairing.

## Unreleased assistant controls protocol 1

The bridge may advertise `controlsVersion: 1` and negotiate
`control-action.v1` / `control-result.v1`; use these capability signals rather
than inferring support from an app update. See [controls](controls.md) for exact
proof, scope-choice and epoch rules. Older bridges/clients continue read and timer
APIs unchanged. Unsupported control sidecars are ignored by timer consumers;
timer validation remains strict. Controls need the reviewed framework action
sidecar schema (fictional source revision and hashes are vendored in
`tests/fixtures/assistant-actions/provenance.json`) or explicit vanilla mappings.
No released bridge/native minimum version or hardware certification is claimed.

## Unreleased canonical native metadata

Framework SPEC-037 / PR #237's canonical private artifact is pinned with SHA-256
checks in `tests/fixtures/assistant-metadata-canonical/provenance.json`. Its dictionary
`definitions.capabilities` and selected translations normalize into the existing
runtime resolver; the old experimental list shape retains its separate strict
`frame-assistant-metadata-experimental.schema.json` migration reader. Invalid canonical
input cannot fall back to that reader. Only the canonical form is current authoring.

Generic `measurement.v1` needs no capability-ID table, but every source still requires
verified inventory, exact capability/role/revision/entity/registry grant, HA READ and
current area association. New metadata is a proposal, not permission. Unsupported
attribute-backed generic measurements are omitted. Source age overrides can tighten,
never extend, the declared maximum. Missing/restored/wrong-unit data never become a
current reading. The private canonical file is never served to Android.

Existing authorized specialized v1 evaluators supply typed records. Canonical client
vocabulary can decorate their authorized descriptor but cannot add a selector or
relax execution. Native descriptions allow up to 300 characters; title/alias/anchor
limits remain 120. Candidate Frame supports this bound. Existing deployed v1 shapes
and raised/high air result keys are unchanged. Canonical fair/poor helper references
are read live; contradictory aliases fail as a policy conflict rather than choosing
one silently. Shared semantic fixtures cover good/fair/poor and operability separately
from permissions and action support.

Metadata profile advertising remains behind the existing explicit parity gates;
normal startup does not enable it. This integration candidate is not F2 language,
all-version or physical-device release acceptance. The prerequisite v1 branch is
still a separate review/merge decision. No production/tablet configuration is changed.
