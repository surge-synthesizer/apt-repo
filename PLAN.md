# apt-repo plan

An https apt repository whose files live as assets on GitHub releases. Each
release is one *channel* (today just `distro`), described by one config file.
The scripts are a copy of baconpaul's apt-infra; keep them in step until they
move somewhere shared.

## How it works

Apt's flat repository format is a single directory with no `dists/` or `pool/`,
which is exactly what a release's asset list is. A machine adds a `.sources`
entry with `URIs: https://github.com/surge-synthesizer/apt-repo/releases/download/<channel>/`
and `Suites: ./`, and apt fetches `InRelease`, `Packages.gz` and the `.deb`s from
the release (GitHub 302-redirects to its object store; apt follows https redirects).

Each release holds:

- every `.deb` the channel config lists, named `{pkg}_{version}_{arch}.deb`
- `Packages`, `Packages.gz` with bare-filename `Filename:` entries
- `Release`, `InRelease` (clearsigned), `Release.gpg` (detached)
- the public key, so users can fetch it from the same place

## Layout

```
channels/<name>.json     one per channel / release tag
public_key.asc           committed
private_key.asc          gitignored
scripts/
  aptlib.py              shared code (config, deb parsing, versions, index, signing)
  create-keys            one-time ed25519 signing key pair
  add-debs               debs (paths or URLs) -> channel config, from their control fields
  add-nightlies          each project's Nightly release -> add-debs
  create-pkg-info        channel config + debs -> build/<channel>/ (signed)
  verify                 debian trixie in docker: add repo, update, fetch, install
  update-release         build/<channel>/ -> the GitHub release
content/                 gitignored, local debs
cache/                   gitignored, downloaded debs
build/                   gitignored, staged output per channel
tests/                   local end-to-end test with fake debs and a throwaway key
```

## Channel config

`channels/distro.json` is the example. Beyond baconpaul's schema it has
`nightlies`, a list of `{repo, tag}` whose release `.deb` assets `add-nightlies`
picks up. Each deb's control `Package`, `Version` and `Architecture` must match
the config; mismatches fail the build. Prior versions stay listed so
`apt install pkg=version` works; apt picks the newest by default.

## Key

ed25519, signing only, 2 year expiry (November 2028), no passphrase (an optional
one is read from `APT_SIGNING_PASSPHRASE`). One key signs every channel. Signing
happens in a throwaway `GNUPGHOME`. CI gets the private key from the
`APT_SIGNING_KEY` secret. Extend before expiry (`gpg --quick-set-expire`) and
re-export, or every client's `apt update` breaks.

## Steps

1. done: scripts, key, `distro` channel with surge-xt, shortcircuit-xt, ob-xf, spectrumworx nightlies
2. done: `create-pkg-info` + `verify` locally, arm64 and amd64 trixie
3. first `update-release` to the `distro` tag, `verify --remote`
4. `APT_SIGNING_KEY` secret, and a `workflow_dispatch` workflow that runs
   `add-nightlies`, builds and publishes (could run on a schedule after the nightlies)
5. later: numbered releases. Each project's release CI dispatches its deb URLs to
   a workflow here which runs `add-debs` on `distro` and publishes it.
   The hand-cached nightly path then goes away.
