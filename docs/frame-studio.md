# Frame Studio — shared UI 0.2.0, contract 2

The catalog owns this portable renderer, widget registry and template editor. The same generated
renderer runs in Home Assistant and the Android app. It has no HA globals or network access.

The hierarchy is **template → Controls / Ambient / Music → screens → widgets**. Arrange widgets
on the preview by dragging and resizing; use arrows to move and Shift+arrows to resize. Escape
cancels a pointer gesture. Placement snaps to a 12×12 landscape grid and rejects collisions.
Portrait uses a generated 12×24 layout; direct portrait edits create independent overrides.
Adding a widget preserves those overrides and is rejected if portrait has no room. Reset portrait
explicitly to return to automatic layout. Undo/redo and in-session drafts are local to each template.

**Save** creates a revision in the library. **Apply** assigns that saved revision to the selected
endpoint. Later edits do not change an applied snapshot. Built-ins are read-only; Save creates a
copy. Save as new forks the config and undo history. Templates in use cannot be deleted. A
concurrent save retains the draft and requires a library reload; Save as new can preserve the work.

Classic includes climate/humidity, configured air assessment, histories, grouped status, plants,
photos, media/artwork/mains, lights/covers/media controls and an agenda. Optional screens depend on
mapped roles. Missing or stale sources are shown as unavailable, never filled with invented data.
The editor is always read-only with respect to actuators. The language setting controls frame
content (English, German, Polish); Studio administration currently uses English.

## Build and integration

`node tools/build_frame_studio.mjs [output-directory]` produces the versioned HA assets and a
three-file renderer artifact with SHA-256 manifest. The bundle includes the registry and contract;
Android pins the manifest and checks every byte. The old `packages/frame-ui` 0.1.0 artifact is retained.

For an installation, export mappings with `tools/frame_profile.py --display-preview --studio`
and the existing required input/dashboard arguments. Mappings belong in the home repository.
Templates contain generic roles, not entity IDs, paths, credentials or arbitrary service names.
Photo directories must be inside HA's `www`; the bridge decodes and bounds raster images before
sending data URLs. No external image origins are allowed in the packaged renderer.

The admin Studio API stores a versioned library separately from device grants and v1 layouts.
The first migration retains an exact v1 backup and creates an imported user template. Overflowing
v1 widget rows become ordered continuation screens. If a legacy configuration exceeds Studio
limits, v1 remains available and Studio is not advertised for that endpoint. The installed legacy
script's numeric screen delays produce a separate **Ramka Classic · installed** template.

Applying requires a recently connected paired contract-2 client (Android 0.3.0). Explicit Apply
transfers only the configured legacy ambient loop/start/watchdog to the server-side Studio selector;
presence, approach, hold timers and stable helper options remain HA-owned. A failed persistent write
restores prior scheduler ownership. Every screen duration is explicit: zero holds the screen.
Calendar navigation locates a calendar widget, including on a renamed screen. Tap/swipe gestures
use existing helper scripts, with a 700 ms shield when Controls becomes visible. Android has no
playlist timer. Returning to a legacy-only installation retains the bundled v1 renderer.

## Validation and current limits

`npm test` covers contract/geometry, draft isolation, renderer and lifecycle behavior. Python tests
cover server validation, migration, storage conflicts/failures, scheduling, mappings and action
bounds. `tools/frame_studio_browser_check.py` exercises actual Chromium pointer/touch drag, resize,
keyboard, Save/Apply, conflict recovery and responsive layout. It is included in `tools/check-all.sh`.

History/forecast/calendar reads are bounded and cached. Graphs depend on HA recorder statistics;
new installations can legitimately have no history yet. Native media artwork requires a registered
HA media player; a state-only test fixture cannot supply that entity's artwork method. Photos scan
at most 5,000 entries / 1,000 images and choose a fresh bounded sample for each exposure. Long Doze,
process death and app-store distribution are separate from this alpha acceptance.

## Installation-aware sources (SPEC-019)

Widget library groups **Ready** and **Needs mapping**, retaining categories and search; the readiness
filter never prevents an unavailable widget from being added to a template. **Map sources** opens
an endpoint-specific editor. Select room temperature/humidity, air metrics, weather, calendars,
media, lights, covers, status or mains sources. Several candidates remain separate choices; nothing
is selected automatically. Marked component sources appear first as suggestions, not trusted grants.
Photos and plant metadata remain advanced YAML mappings with explanatory requirements.

Readiness describes structural bindings and media capabilities. A missing entity needs attention;
an existing entity reporting `unknown`, `unavailable` or stale data keeps its binding. History is
specific to the widget's selected metric and still requires recorder statistics. Mapping an air
sensor adds measurements; it never creates thresholds or an assessment policy. Unsupported media
controls are disabled when the client negotiates the capability extension, and rejected by the bridge
for every client. Existing source-specific skip policies still apply.

Source saves use a separate optimistic revision. They change live display and voice sources without
changing or applying a template. The mapping store preserves YAML fields outside the edited group,
including runtime helpers, photo directories, plant limits, air policies and media metadata. A conflict
retains the source selections and template draft; reload source mappings explicitly before retrying.
Clients should periodically refresh `profile` to refresh room labels used by local voice routing.

A vanilla installation needs the bridge and an explicit endpoint, with an existing HA dashboard path:

```yaml
domosenso_frame:
  endpoints:
    - id: panel
      name: Wall panel
      dashboard: /lovelace
      display:
        locale: en
```

No component marker or DomoSenso script is required. Pair the display, open Studio as an administrator,
select sources and apply a template. Navigation without a configured legacy script uses the bridge's
per-endpoint state; calendar navigation locates an actual calendar widget. This transient navigation
state resets on HA restart. Existing helper/script mappings retain their behavior and legacy scheduler
transfer rules. Every template state retains an unconditional screen.

Admin APIs (all require active administrator access):

- `studio_mappings` with `version: 1`, `endpoint` returns `version`, `endpoint`, `revision`, editable
  `mappings`, bounded metadata `candidates`, and `widgets` with `ready` / `reasons`. History includes
  metric availability, navigation includes target availability, and media controls include supported
  actions. These discovery details never enter portable templates or ordinary runtime snapshots.
- `studio_mappings_update` adds `revision` and `patch`. Lists replace the selected group. Media accepts
  only `entity`; mains accepts `power` / `voltage`, with `null` removing one field. Empty object clears
  media or mains, `null` clears weather, and empty lists clear list groups. Only existing eligible
  entities may be newly bound; unchanged missing bindings can be retained. Storage is
  `.storage/domosenso_frame_mappings`, separate from YAML and the template library. Saves serialize,
  verify the disk envelope, restore prior state after failure and recheck authorization after awaits.
- An endpoint advertises `capabilitiesVersion: 1` in `profile`. Updated clients send
  `capabilitiesVersion: 1` to `studio_display` / `studio_dashboard` to receive optional `media.controls`
  booleans for `play_pause`, `previous`, `next`, `power`, `volume`, and `source`. Omit the request field
  on old bridges; responses without opt-in retain the previous contract-2 shape. Old clients still
  receive `canSkip`, and server-side action restrictions always apply.

Discovery currently returns at most 256 candidates per role (component-marked first, then entity ID),
32 rows per mapping group and 16 calendars. Sensor candidates require the appropriate HA
`device_class`; entity-name guesses and automatic custom-sensor conversion are deliberately absent.
New custom assessment scores require advanced explicit configuration. The source editor currently
uses English; frame content remains English, German or Polish.
