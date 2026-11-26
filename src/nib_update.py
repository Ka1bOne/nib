"""Release check and atomic installation for the Nib executable zipapp."""
from http.client import HTTPException as http_error
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZipFile

REPO = "Ka1bOne/nib"
LIMIT = 1024 * 1024


def fetch(url, limit, timeout):
    request = Request(url, headers={"User-Agent": "Nib-terminal-editor"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Release exceeds size limit")
    return data


def latest(version):
    try:
        metadata = json.loads(fetch(f"https://api.github.com/repos/{REPO}/releases/latest", LIMIT, 1))
        tag = metadata["tag_name"]
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            return
        if tuple(map(int, tag[1:].split("."))) <= tuple(map(int, version.split("."))):
            return
        url = f"https://github.com/{REPO}/releases/download/{tag}/nib"
        for asset in metadata["assets"]:
            digest = asset.get("digest") or ""
            if (asset["name"] == "nib" and asset["browser_download_url"] == url
                    and re.fullmatch(r"sha256:[a-f0-9]{64}", digest)):
                return tag, url, digest[7:]
    except (OSError, ValueError, KeyError, TypeError, EOFError, http_error):
        return



def install(release, target):
    tag, url, digest = release
    data = fetch(url, LIMIT, 5)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Download checksum mismatch")
    with ZipFile(io.BytesIO(data)) as archive:
        if archive.read("VERSION").decode().strip() != tag[1:] or archive.testzip():
            raise ValueError("Release files do not match the version")
        for name in ("__main__.py", "nib_editor.py", "nib_update.py"):
            compile(archive.read(name), name, "exec")
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as out:
        temp = Path(out.name)
    try:
        temp.write_bytes(data)
        temp.chmod(target.stat().st_mode & 0o777)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
