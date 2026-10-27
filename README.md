# DomoSenso Frame for Home Assistant

![DomoSenso Frame](custom_components/domosenso_frame/brand/icon.png)

Free, local Home Assistant integration for DomoSenso Frame: configure data sources,
arrange screens in Frame Studio and approve Android tablets using a six-digit code.
Works with DomoSenso installations and vanilla Home Assistant. The Android/AI app
is maintained separately; this repository contains no app binaries or AI models.

**Version 0.3.0 is an alpha/pilot release. Requires Home Assistant 2026.9.2 or newer.**

## Install through HACS

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/zerostorypoints/domosenso-frame-ha`, type **Integration**.
3. Find **DomoSenso Frame**, download it and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → DomoSenso Frame**.
5. Choose a name, language and optional temperature, air, weather, calendar and media
   sources. You can leave sources empty and map them later in Frame Studio.

[Open the repository in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=zerostorypoints&repository=domosenso-frame-ha&category=integration)

This is a HACS custom repository, not a listing in the HACS default catalog.
Installing files in HACS and adding the integration in Home Assistant are separate
steps. HACS must already be installed. No terminal or YAML editing is needed for
new basic installations. Back up HA before upgrading an existing installation.

## Pair an Android tablet

1. Install the DomoSenso Frame Android pilot app, enter your HA address and sign in
   directly to HA with a dedicated ordinary tablet account.
2. Select the panel. The tablet displays a six-digit pairing code.
3. On another device, an administrator opens **DomoSenso Frame → Devices and pairing**
   in HA, compares the panel and code, then approves the request.
4. Select **Show frame** on the tablet. AI preparation is optional and independent
   of display pairing. Android 0.6.0 was used for the onboarding acceptance test.

The device grant is bound to the tablet's key, HA user and panel. It survives normal
restarts and can be revoked by an administrator. Grants restrict the bridge API;
they do not narrow the existing permissions of the HA account used on the tablet.

DomoSenso component metadata provides mapping suggestions. Vanilla HA uses the
same entity selectors. Suggestions never grant access automatically; the
administrator chooses the sources. Raw air readings do not imply health thresholds.

## Layouts and existing installations

Use **DomoSenso Frame → Template Studio** to map sources, arrange widgets on a grid,
save templates and apply them to the frame. Widgets indicate whether sources are
ready or still need mapping. See the [Studio guide](docs/frame-studio.md).

Existing YAML configurations remain supported and keep ownership of their endpoints;
the wizard will not create a second configuration over them. Advanced plant/photo
bindings and existing scheduler scripts remain optional YAML features. Only one
UI configuration entry is supported; multiple tablets can pair to its panel.

See [bridge and protocol details](docs/bridge.md) for advanced configuration.
The catalog-only exporter and test commands mentioned there belong to the
[DomoSenso source project](https://github.com/zerostorypoints/domosenso), not this
standalone distribution repository.

## Validation and support

The 0.3.0 source was tested on an isolated HA 2026.9.2 instance with fictional
sensors, including UI setup, restart persistence, ordinary-user tablet pairing and
automatic recovery after integration reload. Android onboarding tests ran on a TCL
tablet. Compatibility with every HA version, tablet or household is not guaranteed.

Report reproducible issues in this repository. Do not post tokens, passwords,
household configuration, calendar contents or other private data in issues.

## License and source

[Apache License 2.0](LICENSE). Initial integration source was exported from DomoSenso
commit `81eea61bebe972305a17e5576d04c6abdbe05604`. Packaging changes add installation
instructions, standalone repository links and HACS validation. Household data,
credentials, Android app code and AI models are excluded.
