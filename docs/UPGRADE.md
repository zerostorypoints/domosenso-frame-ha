# Upgrade notes

## Shared timers (development)

Existing assistant v1 read metadata and routes remain supported. The compiler keeps
legacy definitions and translations in `domosenso/assistant/ramka.yaml`, and emits
optional `ramka.current_time` / `runtime_source: ha_clock` metadata separately in
`domosenso/assistant-runtime/ramka.yaml`. Older strict bridges ignore that directory,
so preserving old reads no longer requires upgrading the bridge first. The new
bridge loads runtime metadata separately and validates the installed component
identity; missing metadata uses built-in clock text. Runtime metadata never grants
access. Timer command metadata remains in the separate private
`domosenso/assistant-actions/<component>.yaml` directory. Unknown definitions or
mismatched installed identities are not activated. No metadata enables writes.

Classic foreground delivery and server-time badges require the new authenticated
`timer_browser_*` routes. Unsupported bridges must leave these features unavailable;
clients must not infer sound permission from helper state. Pairing is unnecessary
for these routes: an active HA user needs READ permission and an explicit Frame timer
mapping. Browser claims persist separately from dismissals; preserve the existing
`.storage/domosenso_frame_timers` file when upgrading or restarting.

Timer mappings default to empty and `writeEnabled:false`. Existing HA helpers,
layouts, radar settings and user configuration are preserved. Select helpers and
an optional approved alarm target in the admin Timer panel after upgrading. Old
clients continue their existing read-only behavior; unsupported clients must not
fall back to generic service calls. Back up `.storage` before upgrades and preserve
the timer request/run ledger across restarts. See [timer setup and limits](timers.md).


## Future-source selection (unreleased)

Existing mappings remain explicit until an administrator opts into a future scope in Assistant
sources. No pairing, HA helper or dashboard setting needs to be reset. The first policy save
retains `.storage/domosenso_frame_mappings.pre-source-policy-v1` with mode 0600. Keep this private.
Before downgrading to a bridge without source-policy support, stop HA, back up current mappings,
and restore this pre-policy envelope to `.storage/domosenso_frame_mappings`; then start HA.
The backup is never overwritten by later saves. It cannot preserve mapping edits made after
migration, so review those before a downgrade. Metadata errors remain visible in the source panel.

## Unreleased assistant room controls

1. Keep the existing read metadata and timers installed. For Domosenso lights or
   awnings, install matching private action sidecars and component provenance;
   do not copy sidecars into `www` or enable controls during installation.
2. Update/reload a bridge implementing `controlsVersion: 1`, then a native client
   negotiating `control-action.v1` / `control-result.v1`. Unsupported clients
   must show unsupported without falling back to Studio toggle/index commands.
3. In the existing integration panel, review exact current sources/actions and
   explicitly enable them. Read grants are not actuator consent. Vanilla sources
   use the existing manual mappings plus HA areas, class and wind overrides.
4. After membership, registry identity, installed provenance or safety binding
   changes, review/regrant the new binding. Never migrate a grant by name alone.

Before downgrading, disable assistant controls and retain backups of the private
mapping and controls-ledger storage. An older bridge may reject the new controls
mapping group; retain its preceding read-only mapping backup or remove only
`assistant.controls` through the new bridge before downgrade. Do not delete a
live controls ledger to “retry” an uncertain command. The old read bundle and
existing timer API remain unchanged; no read-only bundle migration is required.
See [controls](controls.md) for bounded request epochs and physical-state limits.

Unreleased review correction: cover grants now bind effective group members and
member classes/features. Awning grants separate the actuator binding from an
optional extension `safetyRevision`. Grants saved by the earlier unreleased
prototype may require explicit review/regrant after this correction; they are
never automatically broadened. Subsequent wind-provider removal/replacement
blocks extension without removing separately granted retraction/STOP. The native
request/result schema remains version 1 and unchanged.
