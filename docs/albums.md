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
  (default 2560, quality 85). Originals are not kept. Live Photos are synced as their still
  image; videos are ignored.
- EXIF is kept, with these exceptions: the orientation is applied to the pixels and reset, the
  GPS location is always removed (files under `/config/www` are served without login), and an
  EXIF block that does not fit a JPEG is reduced, first by dropping MakerNote and the embedded
  thumbnail, then to date, camera, lens and exposure tags.
- HEIC/HEIF photos are converted with `pillow-heif` 1.8.1. It is not a manifest requirement:
  the albums entry asks Home Assistant to install it in the background when the entry is set
  up. Setup neither waits for the install nor fails with it; a failed install (for example
  without network) logs a warning. Until the package can be imported, HEIC items use the share's
  `xl` thumbnail and the album reports `heic: thumbnails`. In the HA Container image the package
  is installed into the image, so after each image update the next setup installs it again and
  needs network access for that.
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

Add albums with **Configure** on the entry (**Add album**): share link, optional password and an
optional folder inside the photo folder (for example `2026/11/Holiday`) instead of the default
layout. The first sync runs right away. If it ends with any status other than `ok` or
`awaiting_confirmation`, the form reports it (wrong password, unreachable host, or a general
share error) and nothing is kept.

The `album_add` action takes the link and the folder but no password: Home Assistant records the
data of every action call in its `call_service` events, so a password would end up in the
recorder database. Add password-protected links in the options form; integrations that run in
the same Home Assistant (such as a later cloud client) call the albums manager directly and can
pass a password there. A link that needs a
password comes back from `album_add` with status `auth_failed`. The action keeps the album
whatever its first sync returns (for example `auth_failed` or `unreachable`); fix the cause and
sync again, or remove it.

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

Each sync asks for the album's version first. Synology raises it when a photo is added or removed
(and when the link's settings change). While the version and the album's name are unchanged and
every synced file is still in its folder, the sync skips the photo listing, except for a full
listing at least once every 24 hours. A sync waiting for a deletion to be confirmed always lists
the album.

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

Albums sync one at a time. A sync that is due while another album syncs waits for it, and the
waiting album already shows `syncing`. If the photo folder itself is missing (for example
unmounted storage), every sync ends with `error` / `photo_dir_missing` and changes nothing; the
sync never creates the photo folder, only folders below it.

### Host map

If the NAS's public name does not answer from inside your network (no NAT loopback), map it to
its LAN address. One line per mapping, each side an origin only (scheme, host, optional port; no
path):

```text
https://photos.example.com -> http://192.168.1.20:5000
```

The share link itself stays unchanged; only the address Home Assistant connects to differs.

Redirects are not followed: a `3xx` answer ends the sync with `unreachable` / `redirect`. Map to
the address that answers directly, for example the `https://` address with its port when the
`http://` one redirects there. With an `https://` target the certificate must be valid for that
name.

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
| `unreachable` | The share host does not answer; check the link, the network or the host map. `error` is `redirect` for a redirect and `too_large` for a response over 200 MB. |
| `disk_full` | The free-space floor was reached. |
| `error` | See `error`, for example `incomplete_listing`, `unexpected_response`, `write_failed`, `invalid_path`, `photo_dir_missing`, `interrupted`. |

An original that cannot be decoded is replaced by the share's `xl` thumbnail. If that cannot be
decoded either, the photo is skipped and counted in `skipped_count`. Skipped photos are tried
again at most once every 24 hours while the album's version is unchanged; a changed album
version retries them at the next sync.

Three actions, all for administrators only, return a response:

| Action | Fields | Response |
| --- | --- | --- |
| `domosenso_frame.album_add` | `url`, `path` (optional); no password, see above | `album` |
| `domosenso_frame.album_remove` | `album_id`; deletes the synced photos and the marker, the folder only if empty | `removed` |
| `domosenso_frame.album_sync` | `album_id` (empty syncs all), `confirm_deletions` | `albums` |

The `album_id` is the `id` from the sensor attribute.

## Safety holds

Nothing is deleted unless the login, the album request and the complete photo listing all
succeed. If the listing is incomplete, the sync fails with `incomplete_listing` and keeps every
file. If more than half of an album's photos would disappear, or an album that had photos comes
back empty, nothing is deleted: the status becomes `awaiting_confirmation` and a repair issue
appears. If the change is intended, confirm it with `album_sync` and `confirm_deletions: true`
for that album, or with **Sync now** and **Confirm held deletions** in the options. A
confirmation applies only to albums that already wait for it when the call is made; an album that
enters a hold during the same run keeps it.

## Passwords and privacy

Links and passwords are stored in Home Assistant's `.storage/domosenso_frame.albums`, never under
the photo folder; removing the albums entry deletes that store. Keep Home Assistant backups
private. Transport timeouts are 30 s to connect, 60 s without data and 15 minutes per request,
responses over 200 MB are refused, and requests identify as `domosenso-frame/0.6.0`.

## Isolated HA smoke

`tests/albums_ha_smoke.py` boots Home Assistant with the YAML bridge, creates the albums entry
through the config flow and syncs against a fake Synology Photos share server on `127.0.0.1`
(host map `http://photos.invalid -> http://127.0.0.1:<port>`). It covers the config and options
flows, the album services with their responses and admin check, the sensor, repair issues, the
`pillow-heif` install by the albums entry, HEIC conversion, safety holds and their confirmation,
error states, startup sync, unload and entry removal. All data is fictional.

Run it from the repository root with Docker and the `ghcr.io/home-assistant/home-assistant:2026.9.2`
image. It needs network access because the albums entry has Home Assistant install `pillow-heif`;
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

`-e ALBUMS_SMOKE_OFFLINE=1` on a network without internet access checks that the YAML bridge and
the albums entry still load when the install fails and that HEIC then uses the thumbnail. Use an
internal Docker network (`docker network create --internal albums-smoke-offline`, then
`--network albums-smoke-offline`); with `--network none` the container has no IPv4 address and
Home Assistant cannot create any HTTP client session, which is unrelated to `pillow-heif`. The
failing install takes about three minutes before the test continues.
