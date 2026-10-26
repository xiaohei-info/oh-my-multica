"""Split test files into CI shards: ``--shard I/N`` keeps every N-th file (sorted by path), starting at the I-th."""


def parse(spec: str) -> tuple[int, int]:
    index, _, count = spec.partition("/")
    if not (index.isdigit() and count.isdigit() and 1 <= int(index) <= int(count)):
        raise ValueError(f"--shard must look like 2/3 (shard number / shard count), got {spec!r}")
    return int(index), int(count)


def mine(files, spec: str) -> set[str]:
    index, count = parse(spec)
    return {name for position, name in enumerate(sorted(set(files))) if position % count == index - 1}
