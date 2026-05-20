# CineLink fnOS FPK packaging

This directory contains the fnOS application package project for CineLink.

Build:

```bash
fnpack build --directory fnos/cinelink
```

The package uses the published Docker image:

```text
akjehsmhq5/cinelink:v2.2.0
```

During installation, fnOS wizard fields can set:

- `wizard_cinelink_config_dir`: mounted into `/app/data`
- `wizard_cinelink_media_dir`: mounted into `/data/media`
- `wizard_cinelink_web_port`: mapped to container port `8000`
- `wizard_cinelink_play_public_url`: written to `CINELINK_PLAY_PUBLIC_URL`

The wizard pre-fills `/vol1/@appdata/cinelink/config` and
`/vol1/@appdata/cinelink/media`; users can replace them with another absolute
Linux path during installation or configuration.
