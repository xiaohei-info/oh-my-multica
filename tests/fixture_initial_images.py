"""Test-only immutable initial images; every case gets independent files/model."""
from functools import lru_cache
import os
from pathlib import Path
import tempfile
from omac.core.manifest import loads_manifest, save_manifest, _ENV_PAT

@lru_cache(maxsize=8)
def _initial_image(raw, environment):
    # The key includes every referenced variable; never reuse another expansion.
    with tempfile.TemporaryDirectory(prefix="omac-fixture-image-") as folder:
        path = Path(folder) / "state.yaml"
        save_manifest(loads_manifest(raw.decode("utf-8")), str(path))
        return path.read_bytes()

def seed_manifest(source, target):
    raw = Path(source).read_bytes()
    names = {match.group(1) for match in _ENV_PAT.finditer(raw.decode("utf-8"))}
    environment = tuple((name, os.environ.get(name)) for name in sorted(names))
    image = _initial_image(raw, environment)
    Path(target).write_bytes(image)
    Path(target).chmod(0o600)
    return image
