#!/usr/bin/env python3
"""Install and enroll the Sigvid agent on an Ubuntu/Debian server.

Standard library only. The .deb package is what gets installed, upgraded and
removed; this script fetches it, verifies it, installs it, writes the backend
address, enrolls the host and starts the services.

  sudo python3 install.py --server https://sigvid.example --token enr_...
  sudo python3 install.py --server http://192.168.122.1:8080 --token enr_... --allow-insecure-http --deb ./sigvid-agent_0.1.0_amd64.deb
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

DEFAULT_REPO = "norvidinc/sigvid-agentic-defense-v6-a-install"
DEFAULT_API = "https://api.github.com"
CONFIG = "/etc/sigvid/agent.yaml"
# Components a release may carry; the package name decides which asset to fetch.
COMPONENTS = {"host-agent": "sigvid-agent"}


def log(msg):
    print(msg, flush=True)


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd, check=True, capture=False):
    log("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, check=False, text=True, capture_output=capture)
    if check and r.returncode != 0:
        die("command failed (%d): %s" % (r.returncode, " ".join(cmd)))
    return r


def fetch(url, dest, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "sigvid-install.py"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sigvid-install.py", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def preflight(args):
    if args.download_only:
        return
    if os.geteuid() != 0:
        die("run as root (sudo): the package install and service start need it")
    if platform.machine() not in ("x86_64", "amd64"):
        die("only amd64 packages are published; this machine is " + platform.machine())
    if not shutil.which("apt-get") and not shutil.which("dpkg"):
        die("this installer supports apt/dpkg systems (Ubuntu 24.04 is the validated baseline)")
    if not shutil.which("systemctl"):
        die("systemd is required")
    if not args.server or not args.token:
        die("--server and --token are required")
    if not args.server.startswith("https://") and not args.allow_insecure_http:
        die("--server must be https://...; use --allow-insecure-http only for a lab")


_COMPONENT = "host-agent"


def component_of(_base):
    return _COMPONENT


def obtain_from_backend(base, tmp):
    """Fetch SHA256SUMS and the package it names from a Sigvid backend."""
    base = base.rstrip("/")
    sums_path = os.path.join(tmp, "SHA256SUMS")
    log("Fetching package list from " + base)
    fetch(base + "/SHA256SUMS", sums_path)
    deb_name = expected = None
    with open(sums_path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*").startswith(COMPONENTS[component_of(base)] + "_") and parts[1].endswith(".deb"):
                expected, deb_name = parts[0], parts[1].lstrip("*")
                break
    if not deb_name:
        die("the checksum file lists no package for this component")
    deb_path = os.path.join(tmp, deb_name)
    log("Downloading " + deb_name + " ...")
    fetch(base + "/" + deb_name, deb_path)
    actual = sha256(deb_path)
    if actual != expected:
        die("checksum mismatch for %s: expected %s, got %s" % (deb_name, expected, actual))
    log("Verified SHA-256 against the backend's checksum file ... ok")
    return deb_path


def obtain_deb(args, tmp):
    if args.deb:
        if not os.path.isfile(args.deb):
            die("no such file: " + args.deb)
        log("Using local package " + args.deb)
        return args.deb
    if getattr(args, "from_url", None):
        return obtain_from_backend(args.from_url, tmp)
    api = "%s/repos/%s/releases/%s" % (args.api_base.rstrip("/"), args.repo, "latest" if args.version == "latest" else "tags/v" + args.version.lstrip("v"))
    log("Looking up release from " + api)
    rel = fetch_json(api)
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    tag = rel.get("tag_name", "").lstrip("v")
    deb_name = "%s_%s_amd64.deb" % (COMPONENTS[args.component], tag)
    if deb_name not in assets:
        die("release %s has no asset %s (assets: %s)" % (rel.get("tag_name"), deb_name, ", ".join(sorted(assets)) or "none"))
    if "SHA256SUMS" not in assets:
        die("release %s has no SHA256SUMS asset; refusing to install an unverified package" % rel.get("tag_name"))
    deb_path = os.path.join(tmp, deb_name)
    sums_path = os.path.join(tmp, "SHA256SUMS")
    log("Downloading " + deb_name + " ...")
    fetch(assets[deb_name], deb_path)
    fetch(assets["SHA256SUMS"], sums_path)
    expected = None
    with open(sums_path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == deb_name:
                expected = parts[0]
    if not expected:
        die("SHA256SUMS does not list " + deb_name)
    actual = sha256(deb_path)
    if actual != expected:
        die("checksum mismatch for %s: expected %s, got %s" % (deb_name, expected, actual))
    log("Verified SHA-256 against the release checksum file ... ok")
    return deb_path


def install_deb(path):
    log("Installing package ...")
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    if shutil.which("apt-get"):
        # --reinstall makes a rerun with the same version refresh the files.
        r = subprocess.run(["apt-get", "install", "-y", "-q", "--reinstall", "--no-install-recommends", os.path.abspath(path)], env=env, text=True)
        if r.returncode != 0:
            die("apt-get install failed")
    else:
        run(["dpkg", "-i", path])


def write_config(args):
    """Set server_url, host_name and allow_insecure_http in agent.yaml, keeping other keys."""
    lines = []
    if os.path.isfile(CONFIG):
        with open(CONFIG) as f:
            lines = f.read().splitlines()
    wanted = {
        "server_url": json.dumps(args.server),
        "host_name": json.dumps(args.name or ""),
        "allow_insecure_http": "true" if args.allow_insecure_http else "false",
    }
    seen = set()
    out = []
    for line in lines:
        key = line.split(":", 1)[0].strip() if ":" in line and not line.startswith((" ", "#", "-")) else None
        if key in wanted:
            out.append("%s: %s" % (key, wanted[key]))
            seen.add(key)
        else:
            out.append(line)
    for key, val in wanted.items():
        if key not in seen:
            out.append("%s: %s" % (key, val))
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(CONFIG, 0o640)
    try:
        import grp
        os.chown(CONFIG, 0, grp.getgrnam("sigvid").gr_gid)
    except Exception:
        pass
    log("Wrote " + CONFIG)


def main():
    p = argparse.ArgumentParser(description="Install and enroll the Sigvid agent.")
    p.add_argument("--server", default="", help="backend URL, e.g. https://sigvid.example (required unless --download-only)")
    p.add_argument("--token", default="", help="one-time enrollment token from the backend (required unless --download-only)")
    p.add_argument("--name", help="host name to enroll as (default: system hostname)")
    p.add_argument("--version", default="latest", help="release version to install (default: latest)")
    p.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository owner/name")
    p.add_argument("--deb", help="install this local .deb instead of downloading")
    p.add_argument("--from", dest="from_url", help="fetch the package from this URL (a Sigvid backend's /downloads) instead of GitHub")
    p.add_argument("--re-enroll", action="store_true", help="replace an existing enrollment on this host")
    p.add_argument("--allow-insecure-http", action="store_true", help="lab only: allow an http:// backend")
    p.add_argument("--api-base", default=DEFAULT_API, help="release API base (default: GitHub); tests point this at a local server")
    p.add_argument("--download-only", metavar="DIR", help="fetch and verify the package into DIR, then stop (no root needed)")
    p.add_argument("--component", default="host-agent", choices=sorted(COMPONENTS), help="which component to install (default: host-agent)")
    args = p.parse_args()
    global _COMPONENT
    _COMPONENT = args.component

    t0 = time.time()
    preflight(args)
    if args.download_only:
        os.makedirs(args.download_only, exist_ok=True)
        deb = obtain_deb(args, args.download_only)
        log("Verified package ready at " + deb)
        return
    tmp = tempfile.mkdtemp(prefix="sigvid-install-")
    try:
        deb = obtain_deb(args, tmp)
        install_deb(deb)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    write_config(args)

    cred = "/var/lib/sigvid/credential.json"
    if os.path.exists(cred) and not args.re_enroll:
        log("Already enrolled (" + cred + " exists); keeping the existing identity. Use --re-enroll to replace it.")
    else:
        if os.path.exists(cred):
            os.remove(cred)
        log("Enrolling with the backend ...")
        cmd = ["sigvid-agent", "enroll", "--server", args.server, "--token", args.token]
        if args.name:
            cmd += ["--name", args.name]
        r = subprocess.run(cmd, text=True, capture_output=True)
        if r.returncode != 0:
            die("enrollment failed: " + (r.stderr or r.stdout).strip())
        log("  " + r.stdout.strip())

    log("Starting services ...")
    run(["systemctl", "enable", "sigvid-executor.service", "sigvid-agent.service"])
    run(["systemctl", "restart", "sigvid-executor.service", "sigvid-agent.service"])
    time.sleep(1.5)
    st = subprocess.run(["sigvid-agent", "status"], text=True, capture_output=True)
    log(st.stdout.strip())
    log("Done in %d s. Check later with: sigvid-agent status" % round(time.time() - t0))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("interrupted", 130)
