# Photo albums

## Isolated HA smoke

`tests/albums_ha_smoke.py` boots Home Assistant with the YAML bridge, creates the albums entry
through the config flow and syncs against a fake Synology Photos share server on `127.0.0.1`
(host map `http://photos.invalid -> http://127.0.0.1:<port>`). It covers the config and options
flows, the album services with their responses and admin check, the sensor, repair issues, HEIC
conversion, safety holds, error states, startup sync, unload and entry removal. All data is
fictional.

Run it from the repository root with Docker and the `ghcr.io/home-assistant/home-assistant:2026.9.2`
image. It needs network access because Home Assistant installs `pillow-heif` from the manifest;
the share server itself is local.

```bash
HA_SMOKE_CONFIG="$(mktemp -d)"
docker run --rm \
  --mount "type=bind,src=$HA_SMOKE_CONFIG,dst=/config" \
  --mount "type=bind,src=$PWD/custom_components,dst=/config/custom_components,readonly" \
  --mount "type=bind,src=$PWD/tests/albums_ha_smoke.py,dst=/albums_ha_smoke.py,readonly" \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2026.9.2 /albums_ha_smoke.py
rm -rf "$HA_SMOKE_CONFIG"
```

The last line is `albums smoke ok` with exit code 0; a failed check prints
`albums smoke failed: <reason>` and exits 1. Set `ALBUMS_SMOKE_DEBUG=1` (`-e` on `docker run`)
to keep Home Assistant's log output.
