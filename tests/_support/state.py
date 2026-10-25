"""Write a manifest literal (definition plus any inline node state) in the 2.0 on-disk layout."""
import yaml

from omac.core.manifest import loads_manifest, save_manifest


def save_literal(path, data) -> str:
    text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    save_manifest(loads_manifest(text), str(path))
    return str(path)
