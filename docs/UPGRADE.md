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
