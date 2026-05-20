# CineLink fnOS native FPK packaging

This package builds a native fnOS application that runs CineLink without Docker.

The native FPK contains:

- CineLink source code
- A portable Linux x86_64 Python runtime
- Python dependencies from `requirements.txt`
- The Linux x86_64 AList binary
- fnOS lifecycle scripts for start, stop, and status

Build in GitHub Actions:

```text
.github/workflows/fnos-native-fpk.yml
```

Manual build on Linux x86_64:

```bash
bash fnos-native/scripts/assemble-native.sh
curl -fsSL -o /tmp/fnpack https://static2.fnnas.com/fnpack/fnpack-1.2.1-linux-amd64
chmod +x /tmp/fnpack
/tmp/fnpack build --directory dist/fnos-native/cinelink
```

Default install paths:

- Config and database: `/vol1/@appdata/cinelink-native/config`
- STRM output: `/vol1/@appdata/cinelink-native/media`
- Web port: `8000`
- Playback public URL: `http://127.0.0.1:8000`

The package is intended for fnOS x86_64. ARM builds need a separate Python
runtime and AList binary.
