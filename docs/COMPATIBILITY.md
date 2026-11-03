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
