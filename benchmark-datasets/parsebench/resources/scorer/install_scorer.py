"""Fetch and verify only the public, pinned ParseBench scoring source."""

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request

REVISION = "34b73455032797754f6ed62e14c27a8b5423d11e"
URL = f"https://codeload.github.com/run-llama/parsebench/tar.gz/{REVISION}"
ARCHIVE_SHA256 = "44f21b59e97c955633cf9d1f5e7ded5227c4fba8d5fa58e88acededa6c3c7723"
MANIFEST_SHA256 = "70c69695e47dfd9467f2aaadd8b93ec6c7254dd61f4941fcfcc12f7c92742065"
MANIFEST_NAME = "AWORLD_SCORER_BUNDLE_MANIFEST.json"
HERE = Path(__file__).resolve().parent
DEST = Path("/opt/parsebench-scorer")


def checked(raw, expected, name):
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RuntimeError(f"SHA256 mismatch: {name}")
    return raw


manifest_bytes = checked((HERE / MANIFEST_NAME).read_bytes(), MANIFEST_SHA256, MANIFEST_NAME)
manifest = json.loads(manifest_bytes)
assert manifest["scorer_revision"] == REVISION
with urllib.request.urlopen(URL, timeout=120) as response:
    archive_bytes = checked(response.read(), ARCHIVE_SHA256, URL)

DEST.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
    files = {}
    for member in archive.getmembers():
        parts = PurePosixPath(member.name).parts
        if len(parts) > 1:
            files[PurePosixPath(*parts[1:]).as_posix()] = member
    for entry in manifest["files"]:
        name = entry["path"]
        path = PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts
        if name == "VENDORED.md":
            raw = (HERE / name).read_bytes()
        else:
            member = files[name]
            if not member.isfile():
                raise RuntimeError(f"Source is not a regular file: {name}")
            with archive.extractfile(member) as stream:
                raw = stream.read()
            if name == "README.md":
                # The existing attestation normalizes the upstream README's
                # two trailing blank lines; no Python source is modified.
                raw = raw.rstrip(b"\n") + b"\n"
        checked(raw, entry["sha256"], name)
        if len(raw) != entry["size"]:
            raise RuntimeError(f"Size mismatch: {name}")
        output = DEST / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
(DEST / MANIFEST_NAME).write_bytes(manifest_bytes)
print(f"Verified {len(manifest['files'])} scorer files at {REVISION}")
