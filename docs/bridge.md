# DomoSenso Frame bridge (alpha)

Free HA-side source; the Android/AI application is maintained separately. This experimental
integration is not yet installed or versioned by the catalog CLI. Tested baseline: HA Container
2026.9.2. No AI models, licence checks, network cloud service or actuator API are included.

## Setup without YAML (0.3.0)

After installing the integration files and restarting Home Assistant, open **Settings →
Devices & services → Add integration → DomoSenso Frame**. Enter a name and display language,
then select optional temperature, air, weather, calendar and media sources. Empty selections
are valid. Source types are checked against live HA entities; no measurements imply an air
assessment policy. Component markers are discovery hints only.

Open **DomoSenso Frame** in the sidebar for the getting-started guide, source mappings,
templates and device pairing. The endpoint uses `/lovelace` as its authentication host;
the Android app supplies the display, so vanilla HA needs no custom dashboard. Installation
identity is persisted immediately. Config entries retain initial selections; Studio retains
mapping overrides separately. Disabling the integration invalidates live session proofs;
enabling it reloads the saved installation identity and device grants.

Only one UI installation is supported. Existing YAML configurations keep ownership of their
endpoints: adding a UI entry is refused with instructions to use the existing sidebar.
No automatic YAML migration or deletion of saved grants is performed. Advanced plant/photo
bindings and existing scheduler scripts remain optional YAML features.

## Existing YAML installations

Copy `custom_components/domosenso_frame` into HA's config directory and configure endpoints:

```yaml
domosenso_frame:
  stale_seconds: 900
  endpoints:
    - id: wall_panel
      name: Wall panel
      dashboard: /dashboard-frame/0
      temperature:
        - entity: sensor.demo_room_temperature
          label: Demo room
      air:
        - entity: sensor.demo_co2
          label: CO₂
        - entity: sensor.demo_pm25
          label: PM2.5
      calendars:
        - calendar.demo
```

Restart HA. Log into HA in the Android app, select the endpoint and compare its six-digit
code with the **DomoSenso Frame** administrator sidebar. Approve only a matching request.
The request expires in five minutes; signature challenges expire in sixty seconds and
are single use. Device P-256 keys bind to one HA user and endpoint. Revocation takes effect
on existing sessions; an administrator migration/reprovision flow is not implemented yet.

The `/api/websocket` command namespace is `domosenso_frame/`:
- `profile`: protocol 1, installation UUID, timezone and endpoint capabilities.
- `begin(publicKey, endpoint, name)`, `finish(signature)`, `status`: device proof and approval.
- `data(topic)`: current mapped temperature/air readings or today's calendar in HA timezone.
- `subscribe`: scoped screen/revocation events. On integration unload, sends
  `{kind: "bridge_unavailable", reason: "integration_unloaded"}` before clearing proofs.
  Keep the HA connection; retry the profile and repeat device proof once the bridge returns.
  Previously approved device grants remain saved.
- Administrator only: `devices`, `approve(deviceId, code)`, `revoke(deviceId)`,
  `screen(endpoint, action)` where action is `wake`, `dim` or `release`.

Entities are selected by configuration and checked against the authenticated user's read
permissions for every request. Caller-supplied entity IDs and service calls are rejected.
The dashboard itself uses normal HA permissions; bridge grants do not restrict HA's existing
frontend/API. Removing a grant disables the assistant bridge, not the user's HA account.
Use a dedicated least-privilege HA user for the tablet. HTTP is only a local alpha option;
HTTPS is recommended for normal installation.

`last_reported` determines sensor freshness. A missing calendar service/entity fails explicitly.
Calendar results are limited to 100 events per response. No health-quality judgement is
inferred from raw air readings. No background wake reliability is promised by the bridge.

Tests: `uv run --with cryptography --with pytest pytest -q integrations/domosenso_frame/tests`.
Physical acceptance and real WebSocket tests are recorded privately in the home repository;
product source and fixtures contain no household configuration.

## Optional component semantics (alpha v1)

A semantic endpoint keeps numeric readings, but also returns a configured air assessment.
Export from the **dashboard actually installed** and its matching home inputs:

```sh
uv run tools/frame_profile.py --dashboard /path/to/home/dashboard.pl.yaml \
  --inputs /path/to/home/inputs/ramka.yaml \
  --climate-inputs /path/to/home/inputs/climate-air.yaml \
  --dashboard-path /dashboard-frame/0 --out /path/to/home/frame-endpoint.yaml
```

Put the resulting mapping under `domosenso_frame.endpoints` and restart HA. It contains
private instance mappings: keep it in the home repository. Re-export after changing the
installed dashboard or input mapping; there is no automatic synchronisation yet.
Conflicting policies for the same entity and unmapped inputs fail export.

`semantic_version: 1` enables the additive `assessment` field on air `data` responses;
profile endpoints advertise `semanticVersion`. Sources optionally carry `metric`, `indoor`
and a `policy` (`higher_is_better`, exactly three ordered `levels`, `source`).
`metadata_source` records component/revision provenance, not installer or licence proof.
The card's first/second/third bands mean good/raised/high, with the same boundary operators.
The worst evaluated source determines the summary; a good score cannot mask a high pollutant.
The response includes coverage, source indexes, configured labels, thresholds and provenance.
Missing/stale readings, negative air values, unknown units or absent policy are not assessed.
These are **configured dashboard ranges**, not health or legal standards. No trend or humidity
alert is inferred from a single sample. Unit vocabulary is intentionally conservative; an
unsupported sensor needs an explicit adapter rather than guessed conversion.

Climate scope comes from the owning climate-air input (`indoor` defaults to true there).
Without that input, only an explicit Ramka `indoor` value is exported. Android excludes
explicit outdoor sources from whole-home temperature summaries but permits a named query.
Legacy endpoints still return raw readings. Vanilla HA can supply the same explicit source
metadata and policies manually; it does not need a DomoSenso installation marker.

## Shared UI preview

Add `--display-preview --locale pl` to the endpoint exporter to include current weather and
configured status sources. Or opt in manually:

```yaml
      display:
        locale: en
        weather: weather.demo
        status:
          - entity: binary_sensor.demo_window
            label: Demo window
```

`display.config` optionally takes the shared UI v1 schema. Without it the bridge supplies Home,
Temperatures and Air screens for configured sources. `display.mode_entity` / `page_entity` may
bind existing HA state selectors; matching values override server-selected rotation. Unknown
selectors fall back to a valid configured view. These reads require HA entity permissions.
Source mappings and helper bindings still load from YAML on restart. Administrators can now edit
layouts in **DomoSenso Frame → Frame layout**. Select an endpoint, edit a draft and Save; the same
configuration reaches the HA card and existing Android 0.2.0 preview on its next poll.

Screens contain ordered widgets; each mode has an ordered screen playlist and rotation period.
Use arrows to reorder, checkboxes to share screens across modes, sizes to adapt the grid, and
capability conditions to hide irrelevant screens. Keep one unconditional screen per mode. The
preview offers tablet dimensions, portrait/landscape and current endpoint readings. It does not
activate the selected mode on the running frame; existing helper bindings still control that.

Save writes an override to HA storage; YAML remains the default and owns the source mappings.
Defaults prepares a draft, not an immediate reset. Discard reloads current saved configuration.
Conflicting edits are rejected without replacing the draft; reload before retrying. Drafts survive
HA route changes in the same browser session, scoped to the installation and HA user. Preview
readings refresh independently of the draft; a successful save is verified against the storage file. Layouts
follow endpoint IDs: retain IDs across upgrades, and do not reuse one for a different panel.
Back up `.storage/domosenso_frame_layouts` with HA. There is no arbitrary-entity picker, drag-and-drop
canvas, per-screen duration or new widget family in this editor.

Paired `display` returns a bounded display snapshot. Normal HA frontend `dashboard(endpoint)`
returns the same view after checking the authenticated HA user's source permissions; it is
intentionally not an Android device grant API. Neither endpoint can call actuators. Profile
advertises `displayVersion: 1` only for opted-in endpoints. The new static resource is
`/domosenso_frame/ui/0.1.0/ha-card.js` (module); card type `custom:domosenso-frame-card` with the
endpoint id. Keep existing dashboard and resource mappings during the preview migration.

## Template Studio (contract 2)

The admin panel now separates **Template Studio** from **Devices and pairing**. Studio supports
state-owned screens, a categorized widget library, grid drag/resize, portrait overrides and
independent Save / Apply revisions. See [`packages/frame-studio`](frame-studio.md)
for editor behavior, mappings, migration and scheduler ownership.

Android 0.3.0 uses `studioVersion: 2` and `studio_display` / `studio_action`; the existing display v1
and layout commands remain available. Studio never grants arbitrary service/entity access. Source
reads and bounded controls are permission checked, including after awaited data operations.
Installation mappings can be exported using `tools/frame_profile.py --display-preview --studio`.
The persistent library uses verified disk writes and optimistic revisions; an unavailable Studio
migration does not prevent legacy API startup.

### Installation source editor

SPEC-019 adds active-admin-only `studio_mappings` / `studio_mappings_update` (version 1), independent
of templates and device grants. The Studio library shows Ready / Needs mapping, metadata candidates
and explicit per-endpoint selection. Overrides persist in `.storage/domosenso_frame_mappings` and
update display/voice without restarting HA. Existing YAML is the authoritative baseline; advanced
policies and runtime mappings remain intact. A vanilla endpoint can start with just `id`, `name`, an
existing `dashboard` path such as `/lovelace`, and `display: {locale: en}`. No legacy helper is required.

See [Frame Studio source mapping documentation](frame-studio.md#installation-aware-sources-spec-019)
for patch semantics, candidate bounds, navigation fallback and negotiated media controls. Ordinary
runtime requests continue to check device grants and per-user entity permissions, including after
awaits and cache reads. Discovery is admin-only and cannot grant a display access to an entity.

## Optional radar mapping (0.3.1)

The setup wizard accepts an explicit presence `binary_sensor`, an optional distance `sensor`,
a near threshold (default 1.5 m), and an absence delay (default 300 seconds). In the sidebar,
**Get started → Map radar** edits the same mapping for both UI and YAML endpoints. Overrides are
saved separately in `domosenso_frame_mappings`; household YAML, templates, grants, scripts and
scheduler helpers are not rewritten. **Disable radar**, then **Save sources** removes the binding.
Presence source selection is explicit; metadata never selects a source automatically. Distance
supports m, cm, mm, in and ft, converted to metres. The editor also exposes hysteresis (default
0.25 m): near begins at the threshold, far begins above threshold plus hysteresis.

Admin WebSocket reads use `domosenso_frame/studio_mappings` with `version: 1` and `endpoint`.
Update with `domosenso_frame/studio_mappings_update`, those fields, the returned `revision`, and:

```json
{"patch":{"radar":{"presence":"binary_sensor.radar_presence","distance":"sensor.radar_distance","near_m":1.5,"off_delay_s":300,"hysteresis_m":0.25}}}
```

Send `{"patch":{"radar":{}}}` to disable. Presence is required when enabled; distance is optional.
Threshold range is 0.1–20 m, delay 1–3600 seconds and hysteresis 0.05–2 m. Configuration validates
source existence/type/units; reload retains valid persisted selections while devices are offline.

Granted subscribers receive only `{kind:"screen",action:"wake"|"dim"|"release",mode?:"controls"|"ambient"}`.
Presence wakes the screen; near distance requests controls, far (or unavailable distance) requests
ambient. Absence emits no screen action before its configured delay, then dims the screen.
Unknown/unavailable presence cancels the delay and emits `release` without a mode. This clears
a deferred dim intent and returns screen ownership to Android without forcing dim or choosing
a display mode. Repeated unknown states do not repeat the event. Every delivery checks
the current device grant, endpoint and the HA user's read permissions for **both** selected sources.
A new subscription evaluates current states. Delayed events recheck current states at delivery;
no raw source identifiers or measurements are sent to the tablet. Unload cancels listeners/timers.

Existing HA Companion launcher automations remain owned by the installation and can reopen
Companion over the DomoSenso app. Review these conflicts explicitly before a pilot; this mapping
never disables automations. Screen control remains best effort while the app and connection are
alive, with no long-Doze or reboot guarantee. Radar changes neither an HA display template nor
scheduler mode/page helpers.
