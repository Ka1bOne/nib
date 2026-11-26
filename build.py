"""Rebuild the standalone nib command from the editable source."""
from pathlib import Path
from zipapp import create_archive

root = Path(__file__).resolve().parent
create_archive(root / "src", root / "nib", interpreter="/usr/bin/env python3",
               compressed=True, filter=lambda p: len(p.parts) == 1 and p.suffix != ".pyc")

# Keep the downloadable installer pinned to exactly this release build.
import hashlib
import re
installer = root / "install.py"
text = installer.read_text()
version = (root / "src/VERSION").read_text().strip()
digest = hashlib.sha256((root / "nib").read_bytes()).hexdigest()
text = re.sub(r'^VERSION = ".*"$', f'VERSION = "{version}"', text, flags=re.M)
text = re.sub(r'^DIGEST = ".*"$', f'DIGEST = "{digest}"', text, flags=re.M)
installer.write_text(text)
