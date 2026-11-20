# Changes

## 0.6.0 — pending release

- Add synced photo albums: a second config-entry kind, "Photo albums", copies Synology Photos
  share links into a photo folder for Ramka. Photos are converted to screen-sized JPEG files
  (HEIC supported through `pillow-heif`), GPS location is always removed, and the link password
  stays in `.storage`. See [photo albums](docs/albums.md).
- Add the `album_add`, `album_remove` and `album_sync` actions (administrators only, with
  responses) and `sensor.domosenso_frame_albums`.
- Hold deletions that would remove more than half of an album and ask for confirmation.
- Add the `pillow-heif` requirement; Home Assistant installs it on the first start with network.

## 0.5.0 — pending release

- Offer optional HiFiSync pages in the existing Studio layout, with the Studio 0.2.7 renderer.
- Give opted-in, paired Ramka 0.21.0 dashboards the scoped System and Room drawings for the mapped playing source.

## 0.4.1 — pending release

- Add opt-in HiFiSync Studio system and room views through the separately installed
  HiFiSync Home Assistant integration. The integration token stays in HiFiSync;
  Frame receives only the permitted player's bounded presentation snapshot.
- Support optional equipment drawings and a music playlist with manual navigation
  and an optional read-only pause helper. Older clients keep a compatible layout.
- Bundle the matching Frame Studio renderer 0.2.6. Existing templates and source
  mappings are preserved; apply a HiFiSync template explicitly to use the views.


## 0.4.0 — pending release

- Add versioned read-only assistant capabilities and bounded typed answers for the catalog's
  supported domains. Real availability depends on explicit mappings, source permissions,
  freshness and assessment policy.
- Load component definitions and private source bindings only when their provenance matches
  the installed DomoSenso inventory. No automatic grants or actuator commands.
- Add Polish/English **Assistant sources** to select verified component sources and descriptions;
  retain explicit manual adapters for vanilla HA. Preserve pairing and existing mappings.
- Separate exact selected source identities from labels and thresholds, so compatible metadata
  updates propagate without an APK, while reassigned/new sources require renewed selection.
- Include direct TP-Link/Ruuvi source evidence and qualified unavailable/stale answers.

### Update and rollback

Back up the integration and its configuration before updating. Replace integration code and
restart HA; existing mappings and grants remain valid. Install/render matching assistant
component releases using compatible DomoSenso tooling before selecting component sources.
Calendar and presence retain explicit sensitive opt-ins. No HA profile, dashboard preference,
helper or automation setting needs to be changed to enable this bridge.

Older bridge versions cannot read `assistant.component_sources`. If reverting after using that
field, restore the compatible pre-upgrade integration backup and mapping state together. An
empty selection is still explicit; deleting selections is not a downgrade migration. Do not
edit `.storage` or silently discard mappings.

### Evidence and limits

109 Python tests, nine DOM tests, fresh HA 2026.9.2 bootstrap/registered-handler acceptance with
compiled fictional component metadata, and Polish/English Android parser/formatter fixtures.
These are not full WebSocket reconnect, physical voice, or whole-catalog hardware acceptance.
The Android app and AI models are distributed separately.
