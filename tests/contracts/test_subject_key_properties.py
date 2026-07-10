"""Property tests for subject-key stability."""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given
from hypothesis import strategies as st

from verdx.contracts.subjects import derive_subject_key


@given(
    line_a=st.integers(min_value=1, max_value=10000),
    line_b=st.integers(min_value=1, max_value=10000),
)
def test_line_number_edits_do_not_change_subject_key(line_a: int, line_b: int):
    first = derive_subject_key("target", "file", f"content/ch01/setup/ge.adoc:{line_a}")
    second = derive_subject_key("target", "file", f"content/ch01/setup/ge.adoc:{line_b}")
    assert first == second


@given(extra_space=st.sampled_from(("", " ", "\t", "  ")))
def test_case_and_surrounding_space_do_not_change_subject_key(extra_space: str):
    first = derive_subject_key("target", "section", "Content/CH01/Setup/GE")
    second = derive_subject_key(
        "target",
        "section",
        f"{extra_space}content/ch01/setup/ge{extra_space}",
    )
    assert first == second
