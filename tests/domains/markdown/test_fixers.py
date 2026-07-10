from verdx.domains.markdown.fixers import fix_trailing_whitespace
from verdx.types import Observation


def _obs(message: str) -> Observation:
    """Construct a minimal valid Observation with the given message."""
    return Observation(
        severity="medium",
        category="style",
        location=None,
        message=message,
        fixable=True,
        signature="test-sig",
    )


def test_strips_trailing_whitespace(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("line one   \nline two\n")
    result = fix_trailing_whitespace.fn(tmp_path, _obs("a.md:1 trailing whitespace"))
    assert result is not None
    assert f.read_text() == "line one\nline two\n"


def test_fixer_ignores_unrelated_observation(tmp_path):
    assert fix_trailing_whitespace.fn(tmp_path, _obs("a.md:1 broken link to x")) is None


def test_fixer_refuses_to_write_outside_target(tmp_path):
    # A crafted observation naming ../evil.md must not let the fixer write
    # outside the target root.
    target = tmp_path / "target"
    target.mkdir()
    evil = tmp_path / "evil.md"
    evil.write_text("original   \n")  # has trailing whitespace to "fix"
    result = fix_trailing_whitespace.fn(target, _obs("../evil.md:1 trailing whitespace"))
    assert result is None
    # The outside file is untouched.
    assert evil.read_text() == "original   \n"
