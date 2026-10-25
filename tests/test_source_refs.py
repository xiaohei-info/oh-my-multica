import pytest

from omac.pipeline.dispatch import _source_ref_link


@pytest.mark.parametrize(("ref", "expected"), [
    ({"issue_id": "123"}, "`#123`"),                                      # numeric platform number gets a hash
    ({"issue_id": "#123"}, "`#123`"),
    ({"issue_id": "01a17534-a862-7526-8b48-752288177a90"}, "`01a17534-a862-7526-8b48-752288177a90`"),
    ({"issue_id": "x", "url": "mention://issue/x"}, "[x](mention://issue/x)"),
])
def test_source_ref_link_rendering(ref, expected):
    assert _source_ref_link(ref) == expected
