# Pinned canonical inputs

Copied byte-for-byte from the framework revision in `provenance.json`. The schema is
vendored in the integration. `provenance.json` maps destination paths to their hashes;
`SHA256SUMS` is the original framework file, whose relative schema path describes that
upstream layout. Check destination hashes through the test, rather than rewriting the
upstream file for this repository.

The two artifacts add temperature then humidity at the same synthetic component
version/commit. They are private configuration, not the client wire or an access grant.
The isolated `tests/canonical_metadata_ha_smoke.py` adds fictional Polish text as test
data, pairs an ordinary client over authenticated WebSocket, reloads metadata at the
same version, explicitly approves the second capability, then checks area invalidation
and revocation. Exported wire contains only fictional values/opaque identifiers and no
tokens or pairing credentials. Frame consumes that wire, not these private files.
