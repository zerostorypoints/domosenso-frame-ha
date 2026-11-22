# Changes

## 0.6.0 — pending release

- Add opt-in photo album management for paired frames through `domosenso_photos` 0.2.0 or newer:
  list, add (with optional password), remove and re-sync albums, and save Ramka's album choice.
  Opt in per panel with the `photo_albums` endpoint key or the options flow of UI-configured panels;
  failures return fixed WebSocket error codes. The album password goes only to `domosenso_photos`.
- Known limit: Home Assistant's `homeassistant.components.websocket_api` DEBUG logging writes every
  received WebSocket command unredacted, so keep that logger at its default level.

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
