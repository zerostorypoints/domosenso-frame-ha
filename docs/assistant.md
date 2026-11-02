# Assistant bridge preview

This worktree contains the **unreleased version 1 assistant contract implementation**. Supported adapters cover the catalog scopes described below; availability still depends on metadata, mappings, permissions and source evidence. The existing protocol and legacy routes remain available. Do not deploy this preview as a complete assistant.

When the integration is installed and successfully initialized, its profile includes `assistantVersion: 1`. Paired devices use `domosenso_frame/assistant_capabilities` with the paired endpoint and supported query/answer pairs, then `domosenso_frame/assistant_query`. Both enforce the paired endpoint and the authenticated HA user's read permissions on each request. Do not send entity IDs as query arguments. A capability is listed only when its current adapter and endpoint mappings can serve it. The currently implemented paths are temperature, legacy mapped air measurements and static legacy air policy, reported lights and media, bounded calendar events, reported awning state, current weather, explicitly enabled presence observations, explicitly mapped audio levels, backup status, frame status, plant watering bands, and explicitly exposed component explanations. These adapters are a preview; the isolated Home Assistant handler smoke below covers only a small subset, and handler/fixture evidence does not replace a physical Android test.

Frame Studio's **Assistant sources** editor saves explicit per-frame read mappings separately from YAML. Select a subject kind, label and stable key, add the documented role and choose an existing entity. Role names currently used by the manual preview include `temperature` for rooms, `state` for lights, players, weather, awnings and presence, `level` for audio, `archive_count` and optionally `newest_age_hours` or `size` for backup, and `mode`, `page`, `theme`, or `radar_wake_enabled` for the frame. For plant watering configure the `moisture` role and both `min_moisture` and `max_moisture`; no band is invented. For presence choose the observation source type and enable its explicit exposure; it defaults off. For audio set the integration window, unweighted basis, and calibration only when actually known. Some values remain unavailable if adapter-specific evidence is missing. The preview recognizes ESPHome and direct `xiaomi_ble`, `ruuvitag_ble` and `tplink` sensor entities as source-report platforms; other measurement platforms return unknown freshness pending reviewed adapters. In Home Assistant 2026.9.2, Xiaomi Bluetooth sensor values come from the passive Bluetooth processor entity data updated by the Bluetooth sensor update path. Ruuvi uses the same passive Bluetooth processor update path; TP-Link sensor entities write device feature values following coordinator updates. The Ruuvi provider declares `voc_index`, `nox_index` and `iaqs` without dimensional units: only these registry-identified Ruuvi keys are normalized to `index` or the 100-point `%` scale in the response, without mutating HA state. Other unlabeled readings remain invalid. HA `last_reported` records a source report, not a guaranteed physical sample: restored, unknown, missing-timestamp and over-age readings remain unusable. Current weather is a qualified provider-reported state, with unknown provider observation freshness and synchronization; numeric weather attributes have the same qualification. Endpoint mapping does not bypass HA permissions. A light group is a reported group, and its children are never expanded or granted access.

The integration reads YAML artifacts only from `<HA config>/domosenso/assistant/*.yaml` at integration startup and on the administrator-only `domosenso_frame/assistant_reload` command. The command reports load errors without exposing artifact content. Artifacts are parsed and validated against the bundled compiler profile schema. Their component explanation is available only when the administrator explicitly lists that component in the endpoint mapping and the live inventory has one matching installed identity, version, commit and dev flag. Artifact fields are intersected with explicitly selected endpoint entities before use, and policies are joined by exact metric, entity and unit. Matching inventory alone does not grant access. Updating managed artifacts therefore requires integration reload or the explicit admin reload before diagnostics see them. The framework installer is responsible for placing managed artifacts; do not expose them under `www`.

Before release, review the supported source adapters and exact tested revisions, run the isolated HA smoke and app fixture validation below, and test the release on physical devices. The unit tests do not establish physical-device, speech, or provider synchronization behavior.

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

The generator refuses to overwrite an existing assistant artifact directory. Fixture mode checks that all three generated artifacts load, sets matching fictional installed inventory, explicitly exposes the floorplan explanation, and queries its installed metadata without claiming a live assessment. Without the flag, the smoke also works with an empty config and requires no framework checkout.

Localized subject and room labels may be strings or language maps with English required and German and Polish optional. Room labels describe a separately configured stable `roomKey`; they never establish association. Legacy plants without a room key remain unassociated until an administrator assigns one. Calendar invalid events and forecast gaps make source coverage incomplete; valid subsets remain visible with limitations.

## Component activation (current development branch)

The integration-owned **Assistant sources** tab in the DomoSenso Frame panel now displays
verified component bindings and missing/mismatched metadata. This is separate from the catalog's
HA settings and the pinned Template Studio renderer. The tab follows the HA session's Polish or
English language. Vanilla HA retains **Manual sources / vanilla HA**.

1. Install component releases carrying assistant definitions with current DomoSenso tooling,
   render and deploy through the normal home workflow. On staging only, developers can use a
   catalog worktree. Metadata lives at `domosenso/assistant/<component>.yaml`, never under `www`.
2. Reload the integration/metadata after deployment. Inventory and bundle provenance must agree.
3. Open **Assistant sources**, choose the frame, review the proposed role/entity sources and
   select the intended subjects. Opening this screen never grants access. Component explanations
   are a separate opt-in; presence and newly mapped calendars retain their explicit sensitive flags.
4. Save the draft. The app fetches current capabilities for its next question, so selecting an
   existing supported capability does not require another APK. HA user permissions still filter
   every request. Full answers and concise speech use the same typed facts.

The optional `assistant.component_sources` mapping stores `{component, subject, fields}` references.
`fields` records exact selected entity/attribute identities, not copied labels or policies. Only
currently verified component bundles resolve them. A new role or changed source is never granted
implicitly; removed/mismatched metadata disables its roles without converting them into manual
adapters. Labels and policy values can follow a compatible component update. Manual mappings take
precedence, but replacing a source cannot inherit the replaced source's policy. Unavailable saved
selections can be removed. Once `component_sources` is present (including an empty list),
component bindings come only from that explicit selection; the legacy entity-level enrichment
path cannot reactivate removed or reassigned roles. Existing manual/direct adapters still work.
Saves remain revision checked and roll back if metadata changes while
persisting a new selection.

This optional mapping field requires this compatible bridge build. Before downgrading to a bridge
without component-source support, retain a backup made before enabling this field and restore
that compatible integration backup as part of the downgrade. Removing all selections leaves an
empty explicit list and is not a downgrade migration. There is no automatic downgrade migration;
do not edit `.storage` or silently discard mappings. Older paired app protocols remain unchanged.
The new bridge still accepts existing mappings that do not contain this field.

Acceptance evidence: the fresh HA smoke compiles plant metadata from fictional framework inputs,
uses a non-default moisture band, selects sources through admin handlers, queries through a signed
paired session and disables capabilities after metadata removal. It exports only fictional wire
answers to `/config/assistant-acceptance.json` for the actual Android parser/PL–EN formatter tests.
This is real HA handler/bootstrap evidence, not WebSocket transport, microphone or physical-tablet
acceptance. JS tests exercise the activation view's draft/save behavior, language and stale loads.

Local repeatable checks (Python 3.13 and Node 22 or newer):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m pytest -q tests
npm ci --ignore-scripts
npm test
```

These pure Python and DOM checks also run in the `assistant-tests` CI job. The isolated HA smoke
remains an explicit runtime check and additionally requires Docker and the framework compiler.

Clock localization is an optional extension loaded from the separate fixed directory
`<HA config>/domosenso/assistant-runtime/ramka.yaml` at the same startup/admin reload
boundaries. It has the same profile envelope but only the `ramka.current_time`
capability with `runtime_source: ha_clock`; frame labels may remain, with no entity
fields or policies. The bridge checks the installed identity and keeps these profiles
in `assistant_runtime_artifacts`, separate from legacy read artifacts. Missing or
mismatched text falls back to built-in localization and never grants access. The
compiler preserves legacy read definitions/translations, so older strict read loaders
ignore the new directory and do not require a bridge-first rollout for existing reads.
