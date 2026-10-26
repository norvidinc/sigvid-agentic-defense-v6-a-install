# Sigvid agentic defense, variant A: install

Public releases of the Sigvid host agent and its installer. Everything a
server needs to install the agent and enroll with a Sigvid backend is in a
release here: the package, a checksum file, and `install.py`.

## Enroll a server

In the Sigvid backend: **Home → Enroll a server**. Copy the one command it
shows and run it on the server (Ubuntu 24.04, as a user with sudo). When the
backend serves the installer itself the command fetches from the backend;
otherwise it is:

```bash
curl -fsSL https://github.com/norvidinc/sigvid-agentic-defense-v6-a-install/releases/latest/download/install.py | sudo python3 - --server https://YOUR-BACKEND --token enr_…
```

The installer

1. looks up the latest release here and downloads the package and `SHA256SUMS`,
2. verifies the package checksum and refuses to continue on any mismatch or missing checksum file,
3. installs the package with apt (two small systemd services: `sigvid-agent`, unprivileged, and `sigvid-executor`, with the network-admin capability only),
4. writes `/etc/sigvid/agent.yaml`, exchanges the one-time token for a per-host credential, and starts the services.

The server appears in the backend within seconds. Rerunning the command on
an already enrolled server reinstalls the same version and keeps its
identity; `--re-enroll` replaces it.

## Options

| Flag | Meaning |
|---|---|
| `--server URL` | backend address (https required unless `--allow-insecure-http`) |
| `--token TOKEN` | one-time enrollment token from the backend |
| `--name HOST` | enroll under this name (default: system hostname) |
| `--version X.Y.Z` | install a specific release (default: latest) |
| `--component NAME` | which component to install; `host-agent` today |
| `--download-only DIR` | fetch and verify into DIR, then stop; no root needed |
| `--from URL` | fetch from a backend's `/downloads` instead of this repository |

## What is in a release

| Asset | Purpose |
|---|---|
| `sigvid-agent_<version>_amd64.deb` | host agent and executor, Ubuntu 24.04 amd64 |
| `SHA256SUMS` | checksums the installer verifies against |
| `install.py` | the installer, standard-library Python 3 only |

Verify by hand:

```bash
sha256sum -c SHA256SUMS
```

## Removing

```bash
sudo apt remove sigvid-agent
```

State under `/var/lib/sigvid` and the configuration under `/etc/sigvid` are
kept; remove them by hand if wanted.

## Source

The agent, the backend and the design documents live in the platform
repository `sigvid-agentic-defense-v6`. This repository holds only what a
server needs to install; `install.py` here is a copy made at release time.
