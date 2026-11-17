import io
import tarfile
import time
from pathlib import Path


def _tar(files, comp):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=f"w:{comp}") as tf:
        dirs = sorted({str(p) for n in files for p in Path(n).parents if str(p) != "."})
        for d in dirs:
            info = tarfile.TarInfo(f"./{d}/")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = int(time.time())
            tf.addfile(info)
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = int(time.time())
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _ar_member(name, data):
    hdr = f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(data):<10}`\n".encode()
    return hdr + data + (b"\n" if len(data) % 2 else b"")


def make_deb(out_dir, name, version, arch, comp="xz", control_name=None, control_version=None):
    control = (
        f"Package: {control_name or name}\n"
        f"Version: {control_version or version}\n"
        f"Architecture: {arch}\n"
        "Maintainer: test <test@example.invalid>\n"
        "Section: sound\n"
        "Priority: optional\n"
        f"Description: test package {name}\n"
        " a longer description that spans\n"
        " .\n"
        " more than one line\n"
    ).encode()
    ext = {"gz": "gz", "xz": "xz", "zst": "zst"}[comp]
    payload = f"{name} {version} {arch}\n".encode()
    deb = (
        b"!<arch>\n"
        + _ar_member("debian-binary", b"2.0\n")
        + _ar_member(f"control.tar.{ext}", _tar({"./control": control}, comp))
        + _ar_member(f"data.tar.{ext}", _tar({f"./usr/share/doc/{name}/hello.txt": payload}, comp))
    )
    path = Path(out_dir) / f"{name}-{version}-{arch}-{comp}.deb"
    path.write_bytes(deb)
    return path
