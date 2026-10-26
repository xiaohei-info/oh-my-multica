import pytest

from _support import shard


def test_every_file_is_in_exactly_one_shard():
    files = [f"tests/it/test_{name}.py" for name in "abcdefghij"]

    parts = [shard.mine(files, f"{i}/3") for i in (1, 2, 3)]

    assert sorted(set().union(*parts)) == files and sum(len(p) for p in parts) == len(files)


def test_shards_take_alternate_files_so_neighbouring_heavy_files_are_split():
    assert shard.mine(["a", "b", "c", "d"], "1/2") == {"a", "c"} and shard.mine(["a", "b", "c", "d"], "2/2") == {"b", "d"}


@pytest.mark.parametrize("spec", ["0/3", "4/3", "x/3", "1", "1/0", "-1/3"])
def test_a_malformed_shard_is_refused(spec):
    with pytest.raises(ValueError, match="--shard must look like"):
        shard.mine(["a"], spec)
