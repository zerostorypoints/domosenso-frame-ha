# Assistant bridge preview

This worktree contains a **partial, unreleased implementation** of the version 1 assistant bridge. It is not whole-catalog SPEC-029 coverage. The existing protocol and legacy routes remain available. Do not deploy this preview as a complete assistant.

When the integration is installed and successfully initialized, its profile includes `assistantVersion: 1`. Paired devices use `domosenso_frame/assistant_capabilities` with the paired endpoint and supported query/answer pairs, then `domosenso_frame/assistant_query`. Both enforce the paired endpoint and the authenticated HA user's read permissions on each request. Do not send entity IDs as query arguments. A capability is listed only when its current adapter and endpoint mappings can serve it. The currently implemented paths are temperature, legacy mapped air measurements and static legacy air policy, reported lights and media, bounded calendar events, reported awning state, current weather, explicitly enabled presence observations, explicitly mapped audio levels, backup status, frame status, plant watering bands, and explicitly exposed component explanations. These adapters are a preview; the isolated Home Assistant handler smoke below covers only a small subset, and the Android client has not been verified end to end.

Frame Studio's **Assistant sources** editor saves explicit per-frame read mappings separately from YAML. Select a subject kind, label and stable key, add the documented role and choose an existing entity. Role names currently used by the manual preview include `temperature` for rooms, `state` for lights, players, weather, awnings and presence, `level` for audio, `archive_count` and optionally `newest_age_hours` or `size` for backup, and `mode`, `page`, `theme`, or `radar_wake_enabled` for the frame. For plant watering configure the `moisture` role and both `min_moisture` and `max_moisture`; no band is invented. For presence choose the observation source type and enable its explicit exposure; it defaults off. For audio set the integration window, unweighted basis, and calibration only when actually known. Some values remain unavailable if adapter-specific evidence is missing. The preview recognizes ESPHome and direct `xiaomi_ble` sensor entities as source-report platforms; other measurement platforms return unknown freshness pending reviewed adapters. In Home Assistant 2026.9.2, Xiaomi Bluetooth sensor values come from the passive Bluetooth processor entity data updated by the Bluetooth sensor update path. HA `last_reported` records a source report, not a guaranteed physical sample: restored, unknown, missing-timestamp and over-age readings remain unusable. Current weather is a qualified provider-reported state, with unknown provider observation freshness and synchronization; numeric weather attributes have the same qualification. Endpoint mapping does not bypass HA permissions. A light group is a reported group, and its children are never expanded or granted access.

The integration reads YAML artifacts only from `<HA config>/domosenso/assistant/*.yaml` at integration startup and on the administrator-only `domosenso_frame/assistant_reload` command. The command reports load errors without exposing artifact content. Artifacts are parsed and validated against the bundled compiler profile schema. Their component explanation is available only when the administrator explicitly lists that component in the endpoint mapping and the live inventory has one matching installed identity, version, commit and dev flag. Artifact fields are intersected with explicitly selected endpoint entities before use, and policies are joined by exact metric, entity and unit. Matching inventory alone does not grant access. Updating managed artifacts therefore requires integration reload or the explicit admin reload before diagnostics see them. The framework installer is responsible for placing managed artifacts; do not expose them under `www`.

Outstanding before release: complete and verify remaining SPEC-029 behavior, including structured evidence and manual mapping semantics; verify source freshness for each supported integration, registry identities, source authorization on every path, and HA lifecycle/reload in an isolated HA instance; validate shared fixtures with the app. The unit tests do not establish physical-device, speech, or provider synchronization behavior.

## Isolated Home Assistant smoke

With Docker and the `ghcr.io/home-assistant/home-assistant:2026.9.2` image already available locally, run from the repository root. This creates a caller-owned temporary HA configuration, mounts the component and script read-only, and disables external networking. The configuration contains only fictional test data and generated ephemeral pairing material; remove it when finished. No real credentials are needed.

```sh
HA_SMOKE_CONFIG="$(mktemp -d)"
docker run --rm --pull never --network none \
  --mount "type=bind,src=$HA_SMOKE_CONFIG,dst=/config" \
  --mount "type=bind,src=$PWD/custom_components/domosenso_frame,dst=/config/custom_components/domosenso_frame,readonly" \
  --mount "type=bind,src=$PWD/tests/ha_smoke.py,dst=/ha_smoke.py,readonly" \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2026.9.2 /ha_smoke.py
rm -rf "$HA_SMOKE_CONFIG"
```

`HA_CONFIG_DIR` selects the config directory inside the environment and defaults to `/config`. The script bootstraps HA and invokes registered handler functions directly: it checks Studio and profile setup, signed P-256 pairing and approval, capability discovery, a qualified unknown-freshness query, and denial after revocation. It does not exercise a full WebSocket transport, HTTP, browser, Android client, or physical device. Its success output contains status labels only.

To additionally check framework compiler artifacts, use a checkout of the DomoSenso framework that contains `tests/generate_assistant_profiles.py` and has its Python environment set up (including `uv`). Set `FRAMEWORK_DIR` to that checkout and generate the fictional bundles into a fresh caller-owned HA config before running the smoke. For example, from this repository's root:

```sh
FRAMEWORK_DIR="/path/to/domosenso"
HA_SMOKE_CONFIG="$(mktemp -d)"
(cd "$FRAMEWORK_DIR" && uv run python tests/generate_assistant_profiles.py "$HA_SMOKE_CONFIG")
docker run --rm --pull never --network none \
  -e ASSISTANT_COMPILER_FIXTURES=1 \
  --mount "type=bind,src=$HA_SMOKE_CONFIG,dst=/config" \
  --mount "type=bind,src=$PWD/custom_components/domosenso_frame,dst=/config/custom_components/domosenso_frame,readonly" \
  --mount "type=bind,src=$PWD/tests/ha_smoke.py,dst=/ha_smoke.py,readonly" \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2026.9.2 /ha_smoke.py
rm -rf "$HA_SMOKE_CONFIG"
```

The generator refuses to overwrite an existing assistant artifact directory. Fixture mode checks that both generated artifacts load, sets matching fictional installed inventory, explicitly exposes the floorplan explanation, and queries its installed metadata without claiming a live assessment. Without the flag, the smoke also works with an empty config and requires no framework checkout.

Localized subject and room labels may be strings or language maps with English required and German and Polish optional. Room labels describe a separately configured stable `roomKey`; they never establish association. Legacy plants without a room key remain unassociated until an administrator assigns one. Calendar invalid events and forecast gaps make source coverage incomplete; valid subsets remain visible with limitations.
