import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VALID_ARCHES = {"amd64", "arm64", "armhf", "i386", "riscv64"}
INDEX_FILES = ["Packages", "Packages.gz"]
SIGNED_FILES = ["Release", "InRelease", "Release.gpg"]


class AptError(Exception):
    pass


# ---- config


def load_config(path):
    path = Path(path)
    with open(path) as f:
        cfg = json.load(f)

    for key in ["channel", "github_repo", "architectures", "packages"]:
        if key not in cfg:
            raise AptError(f"{path}: missing '{key}'")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", cfg["channel"]):
        raise AptError(f"{path}: channel '{cfg['channel']}' is not a valid tag name")
    for a in cfg["architectures"]:
        if a not in VALID_ARCHES:
            raise AptError(f"{path}: unknown architecture '{a}'")

    cfg.setdefault("origin", cfg["github_repo"].split("/")[0])
    cfg.setdefault("label", cfg["channel"])
    cfg.setdefault("description", "")
    cfg.setdefault("public_key_asset", "archive-key.asc")

    for p in cfg["packages"]:
        if "name" not in p or "versions" not in p:
            raise AptError(f"{path}: each package needs 'name' and 'versions'")
        for v in p["versions"]:
            if "version" not in v or not v.get("debs"):
                raise AptError(f"{path}: {p['name']}: each version needs 'version' and 'debs'")
    return cfg


def build_dir(cfg):
    return ROOT / "build" / cfg["channel"]


def repo_url(cfg):
    return f"https://github.com/{cfg['github_repo']}/releases/download/{cfg['channel']}/"


# ---- fetching


def is_url(src):
    return src.startswith("http://") or src.startswith("https://")


def fetch_deb(src, published_url=None):
    if not is_url(src):
        p = Path(src)
        if not p.is_absolute():
            p = ROOT / p
        if p.is_file():
            return p
        if not published_url:
            raise AptError(f"missing deb: {p}")
        # a fresh checkout has no content/, but the release already holds the deb
        try:
            return fetch_deb(published_url + p.name)
        except OSError as e:
            raise AptError(f"missing deb: {p}, and {published_url}{p.name} failed: {e}")

    cache = ROOT / "cache"
    cache.mkdir(exist_ok=True)
    key = hashlib.sha256(src.encode()).hexdigest()[:16]
    dest = cache / f"{key}-{Path(src).name}"
    if not dest.exists():
        print(f"  downloading {src}")
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(src) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(dest)
    return dest


# ---- deb parsing


def ar_members(data):
    if data[:8] != b"!<arch>\n":
        raise AptError("not an ar archive")
    pos = 8
    while pos + 60 <= len(data):
        hdr = data[pos : pos + 60]
        name = hdr[0:16].decode().strip().rstrip("/")
        size = int(hdr[48:58].decode().strip())
        pos += 60
        yield name, data[pos : pos + size]
        pos += size + (size & 1)


def open_control_tar(name, blob):
    if name == "control.tar.zst":
        try:
            return tarfile.open(fileobj=io.BytesIO(blob), mode="r:zst")
        except tarfile.CompressionError:
            # python < 3.14 has no zstd
            raw = subprocess.run(["zstd", "-dc"], input=blob, capture_output=True, check=True).stdout
            return tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
    return tarfile.open(fileobj=io.BytesIO(blob), mode="r:*")


def read_control(deb_path):
    data = Path(deb_path).read_bytes()
    for name, blob in ar_members(data):
        if name.startswith("control.tar"):
            with open_control_tar(name, blob) as tf:
                for m in tf.getmembers():
                    if m.name in ("control", "./control"):
                        return tf.extractfile(m).read().decode()
    raise AptError(f"{deb_path}: no control file")


def parse_control(text):
    fields = {}
    current = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t":
            if current is None:
                raise AptError("control continuation line before any field")
            fields[current] += "\n" + line
        else:
            k, _, v = line.partition(":")
            current = k.strip()
            fields[current] = v.strip()
    return fields


# ---- debian version ordering, as dpkg's verrevcmp


def _order(c):
    if c == "" or c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    if c == "~":
        return -1
    return ord(c) + 256


def _verrevcmp(a, b):
    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i] if i < len(a) else "")
            bc = _order(b[j] if j < len(b) else "")
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        first_diff = 0
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def _split_version(v):
    epoch, _, rest = v.partition(":") if ":" in v else ("0", "", v)
    upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
    return int(epoch or 0), upstream, revision


def compare_versions(a, b):
    """negative, zero or positive as a sorts below, equal to or above b."""
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)
    if ea != eb:
        return ea - eb
    return _verrevcmp(ua, ub) or _verrevcmp(ra, rb)


def hashes(path):
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
    return {"MD5sum": md5.hexdigest(), "SHA1": sha1.hexdigest(), "SHA256": sha256.hexdigest()}


def asset_name(pkg, version, arch):
    # github rewrites unusual characters in asset names, so we pick a safe one
    raw = f"{pkg}_{version}_{arch}.deb"
    return re.sub(r"[^A-Za-z0-9._-]", ".", raw)


# ---- index


def stanza_for(control_text, filename, size, h):
    lines = [l for l in control_text.rstrip("\n").splitlines() if l.strip()]
    drop = {"Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512"}
    kept = []
    skipping = False
    for l in lines:
        if l[0] not in " \t":
            skipping = l.partition(":")[0].strip() in drop
        if not skipping:
            kept.append(l)
    kept += [
        f"Filename: {filename}",
        f"Size: {size}",
        f"MD5sum: {h['MD5sum']}",
        f"SHA1: {h['SHA1']}",
        f"SHA256: {h['SHA256']}",
    ]
    return "\n".join(kept) + "\n"


def write_packages(out_dir, stanzas):
    text = "\n".join(stanzas).encode()
    (out_dir / "Packages").write_bytes(text)
    # mtime=0 keeps the gz byte-identical across rebuilds
    with open(out_dir / "Packages.gz", "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            gz.write(text)


def write_release(out_dir, cfg):
    files = []
    for name in INDEX_FILES:
        p = out_dir / name
        files.append((name, p.stat().st_size, p.read_bytes()))

    date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC")
    lines = [
        f"Origin: {cfg['origin']}",
        f"Label: {cfg['label']}",
        f"Suite: {cfg['channel']}",
        f"Codename: {cfg['channel']}",
        f"Date: {date}",
        f"Architectures: {' '.join(cfg['architectures'])}",
    ]
    if cfg["description"]:
        lines.append(f"Description: {cfg['description']}")
    for field, algo in [("MD5Sum", hashlib.md5), ("SHA256", hashlib.sha256), ("SHA512", hashlib.sha512)]:
        lines.append(f"{field}:")
        for name, size, data in files:
            lines.append(f" {algo(data).hexdigest()} {size:>16} {name}")
    (out_dir / "Release").write_text("\n".join(lines) + "\n")


# ---- signing


def private_key_material(key_file=None):
    env = os.environ.get("APT_SIGNING_KEY")
    if env:
        return env
    p = Path(key_file) if key_file else ROOT / "private_key.asc"
    if not p.is_file():
        raise AptError(f"no signing key: set APT_SIGNING_KEY or create {p}")
    return p.read_text()


@contextmanager
def temp_gnupg():
    # short base dir because gpg-agent socket paths are length limited
    home = tempfile.mkdtemp(prefix="gpg-", dir="/tmp" if os.path.isdir("/tmp") else None)
    os.chmod(home, 0o700)
    try:
        yield home
    finally:
        subprocess.run(["gpgconf", "--homedir", home, "--kill", "all"], capture_output=True)
        shutil.rmtree(home, ignore_errors=True)


def gpg(home, *args, input=None):
    cmd = ["gpg", "--homedir", home, "--batch", "--yes", "--no-tty", *args]
    r = subprocess.run(cmd, input=input, capture_output=True)
    if r.returncode != 0:
        raise AptError(f"gpg {' '.join(args)} failed:\n{r.stderr.decode()}")
    return r.stdout


def sign_release(out_dir, key_file=None):
    material = private_key_material(key_file)
    passphrase = os.environ.get("APT_SIGNING_PASSPHRASE", "")
    with temp_gnupg() as home:
        gpg(home, "--import", input=material.encode())
        pp = ["--pinentry-mode", "loopback", "--passphrase", passphrase]
        release = str(out_dir / "Release")
        gpg(home, *pp, "--digest-algo", "SHA512", "--clearsign", "-o", str(out_dir / "InRelease"), release)
        gpg(home, *pp, "--digest-algo", "SHA512", "-abs", "-o", str(out_dir / "Release.gpg"), release)
