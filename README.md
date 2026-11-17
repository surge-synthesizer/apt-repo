# surge-synthesizer apt-repo

A signed apt repository for Surge Synth Team plugins, for Debian (trixie or
newer) and sndstation, on arm64 and amd64. The files live as assets on this
repo's GitHub releases; each release is one channel.

Today it carries the linux nightlies of

| package           | project                                              |
|-------------------|------------------------------------------------------|
| `surge-xt`        | [Surge XT](https://github.com/surge-synthesizer/surge) |
| `shortcircuit-xt` | [Shortcircuit XT](https://github.com/surge-synthesizer/shortcircuit-xt) |
| `ob-xf`           | [OB-Xf](https://github.com/surge-synthesizer/OB-Xf)   |
| `spectrumworx`    | [SpectrumWorx](https://github.com/surge-synthesizer/SpectrumWorx) |

## Using it

The `distro` channel is the only one today.

```sh
sudo mkdir -p /etc/apt/keyrings
sudo curl -fsSL -o /etc/apt/keyrings/surge-synthesizer-apt-repo.asc \
    https://github.com/surge-synthesizer/apt-repo/releases/download/distro/surge-synthesizer-apt-repo.asc

sudo tee /etc/apt/sources.list.d/surge-synthesizer.sources >/dev/null <<'EOF'
Types: deb
URIs: https://github.com/surge-synthesizer/apt-repo/releases/download/distro/
Suites: ./
Signed-By: /etc/apt/keyrings/surge-synthesizer-apt-repo.asc
EOF

sudo apt update
sudo apt install surge-xt
```

The key is ed25519, uid `surge-synthesizer apt-repo signing key`, fingerprint

```
7B36 CAB5 2FB8 6A0B FDC9  D817 4909 D371 6890 2F54
```

Check it with `gpg --show-keys /etc/apt/keyrings/surge-synthesizer-apt-repo.asc`.
It expires in November 2028 and will be extended before then; a machine then
needs the refreshed key file, fetched the same way.

## Versions

Nightlies are versioned `X.Y.Z~nightly.<date>.<count>.<hash>`, which sorts
below the `X.Y.Z` release, so apt moves from a nightly to the release it leads
up to on its own. The channel keeps the last few nightlies of each package, so
`apt install surge-xt=<version>` can step back if last night's build is bad.

## Nightlies today, numbered releases later

For now the debs are the ones each project publishes to its own `Nightly`
GitHub release. Those get replaced every night, so this repo copies them, keeps
them locally and re-publishes them here by hand.

That is a stopgap. The plan is to move to GitHub Actions publishing numbered
releases: each project's CI hands its release debs to a workflow here, which
adds them to the channel config, rebuilds the signed index and updates the
release, with no copy cached on anyone's machine. Numbered releases go into
this same `distro` channel and sort above the nightlies that led up to them, so
the install lines above keep working.

## Maintaining it

```sh
scripts/add-nightlies channels/distro.json      # copy the current nightly debs into content/ and the config
scripts/create-pkg-info channels/distro.json    # stage and sign build/distro/
scripts/verify channels/distro.json             # install them in trixie, in docker
scripts/update-release channels/distro.json     # publish build/distro/ to the release
scripts/verify channels/distro.json --remote    # check the published release
```

`add-nightlies` keeps the newest three versions of each package (`--keep N`
to change that). `scripts/add-debs` adds individual debs by path or URL.
`update-release --dry-run` prints what it would upload and delete.

`private_key.asc` is not in the repo; CI will read it from the
`APT_SIGNING_KEY` secret. `PLAN.md` has the design and what is left.

The scripts are copied from baconpaul's apt-repo tooling and may move to a
shared home later.
