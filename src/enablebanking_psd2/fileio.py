"""Writing personal data safely: temp file in the target directory, then rename (no partial files), mode 0600."""

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str, *, dir_mode: int = 0o700) -> None:
    """Write ``text`` to ``path`` atomically with mode 0600; a missing parent is created with ``dir_mode``."""
    path.parent.mkdir(mode=dir_mode, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")  # mkstemp: mode 0600
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
