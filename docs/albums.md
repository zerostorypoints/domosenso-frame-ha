# Photo albums

Version 0.6.0 can keep photo folders for Ramka in step with shared Synology Photos albums. Each
album is added by its share link, copied into a folder under the photo folder and updated on a
timer. The share link is the only access: no Synology account or token is stored.

## What a synced album is

- A folder `<photo_dir>/<YYYY>/<MM>/<album name>`. Year and month come from the album's earliest
  photo at the first sync and then stay fixed. Folder names are cleaned of path separators and
  control characters, lose leading spaces and dots and are cut to 80 characters and 200 bytes. If another folder already holds the name, the album gets a `-2` suffix
  (`-3`, ...) and Home Assistant shows a repair issue. When the album is renamed in Synology
  Photos, only the last part of the folder path is renamed.
- Every photo is converted on import to a JPEG of at most `max_edge` pixels on the longest edge
  (default 2560, quality 85). Originals are not kept. Videos are ignored.
- EXIF is kept, with these exceptions: the orientation is applied to the pixels and reset, the
  GPS location is always removed (files under `/config/www` are served without login), and an
  EXIF block that does not fit a JPEG is reduced, first by dropping MakerNote and the embedded
  thumbnail, then to date, camera, lens and exposure tags.
- HEIC/HEIF photos are converted with `pillow-heif`, which Home Assistant installs from the
  manifest. If it cannot load, those items fall back to the share's thumbnail and the album
  reports `heic: thumbnails`.
- A hidden `.sync.json` marker in each synced folder names the album and lists the files that
  belong to it. It holds no link and no password. Folders without a marker are never written,
  renamed or deleted. A synced folder is managed in Synology Photos: files you add or change
  there by hand are not tracked, and a photo removed from the share is removed from the folder, and a photo you delete by hand is
  downloaded again at the next sync.

## Share link requirements

Create the share link in Synology Photos, with a password if you want one (the password is
optional, a link without one works). It also needs the **download** permission. Without it the
sync uses the share's `xl` thumbnails: the photos are smaller, carry no camera or lens data, and
the album reports mode `thumbnails` instead of `originals`.

The share-link API of Synology Photos is undocumented and may change with DSM updates; it was
verified on DSM in November 2026.

## Add the albums entry

Open **Settings -> Devices & services -> Add integration -> DomoSenso Frame** and choose
**Photo albums**. This is a second kind of config entry next to the YAML or UI bridge; it does
not replace or change either. When a bridge entry or the YAML bridge already exists, the flow
goes straight to the albums step. There is one albums entry per Home Assistant. The only field is
the photo folder, an existing absolute path (default `/config/www/ramka`). Everything under
`/config/www` is served without login, so add only albums you are fine to show there.

Add albums with **Configure** on the entry (**Add album**) or with the `album_add` action: share
link, optional password and an optional folder inside the photo folder (for example
`2026/11/Holiday`) instead of the default layout. The first sync runs right away. In the
options form a wrong password or an unreachable host is reported in the form and nothing is
kept. The `album_add` action keeps the album and returns it with status `auth_failed` or
`unreachable`; fix the cause and sync again, or remove it.

**Remove album** (form or `album_remove`) deletes the photos listed in the folder's marker, any
leftover temporary files and the marker, and then the folder itself only if it is empty. Files
you added to the folder by hand stay, and so does the folder. A folder without the album's own
marker is never touched. The shared album is not changed. While the album is syncing, the options form answers that the album is busy and
`album_remove` reports `removed: false`; try again afterwards. `album_sync` on a syncing album
returns it with status `syncing`.

Removing the albums entry itself keeps every synced folder as a plain folder and deletes the
stored links and passwords.

## Sync schedule and options

An album is synced every `interval_min` minutes (default 60), about 60 seconds after Home
Assistant starts, and on demand (**Sync now** in the options or the `album_sync` action). Photos
appear on the Ramka within its own re-index, at most about 15 minutes after the files land; the
sync does not call it.

**Configure -> Settings** holds:

| Option | Default | Range |
| --- | --- | --- |
| Sync interval | 60 min | 15-1440 |
| Longest edge | 2560 px | 1024-8192 |
| JPEG quality | 85 | 60-95 |
| Keep free on disk | 1 GB | 0.1-1000 GB |
| Host map | empty | |

A sync stops with status `disk_full` when the free space on the photo folder's disk would drop
below the floor; the check runs before each file. Files already written stay.

### Host map

If the NAS's public name does not answer from inside your network (no NAT loopback), map it to
its LAN address. One line per mapping, each side an origin only (scheme, host, optional port; no
path):

```text
https://photos.example.com -> http://192.168.1.20:5000
```

The share link itself stays unchanged; only the address Home Assistant connects to differs.

## Status, services and the sensor

`sensor.domosenso_frame_albums` shows the number of albums. Its `albums` attribute lists, per
album: `id`, `name`, `path`, `photos`, `last_sync`, `status`, `error`, `mode`, `heic` and
`skipped_count`. It never contains links or passwords.

| Status | Meaning |
| --- | --- |
| `ok` | Synced. |
| `syncing` | A sync is running. |
| `awaiting_confirmation` | A deletion hold, see below. |
| `auth_failed` | The link password is wrong or missing. |
| `unreachable` | The share host does not answer; check the link, the network or the host map. |
| `disk_full` | The free-space floor was reached. |
| `error` | See `error`, for example `incomplete_listing`, `unexpected_response`, `write_failed`, `invalid_path`, `interrupted`. |

Photos that cannot be decoded are skipped and counted in `skipped_count`; they are tried again
at the next sync.

Three actions, all for administrators only, return a response:

| Action | Fields | Response |
| --- | --- | --- |
| `domosenso_frame.album_add` | `url`, `password` (optional), `path` (optional) | `album` |
| `domosenso_frame.album_remove` | `album_id` | `removed` |
| `domosenso_frame.album_sync` | `album_id` (empty syncs all), `confirm_deletions` | `albums` |

The `album_id` is the `id` from the sensor attribute.

## Safety holds

Nothing is deleted unless the login, the album request and the complete photo listing all
succeed. If the listing is incomplete, the sync fails with `incomplete_listing` and keeps every
file. If more than half of an album's photos would disappear, or an album that had photos comes
back empty, nothing is deleted: the status becomes `awaiting_confirmation` and a repair issue
appears. If the change is intended, confirm it with `album_sync` and `confirm_deletions: true`
for that album, or with **Sync now** and **Confirm held deletions** in the options.

## Passwords and privacy

Links and passwords are stored in Home Assistant's `.storage/domosenso_frame.albums`, never under
the photo folder; removing the albums entry deletes that store. Keep Home Assistant backups
private. Transport timeouts are 30 s to connect and 60 s without data, and requests identify
as `domosenso-frame/0.6.0`.

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
