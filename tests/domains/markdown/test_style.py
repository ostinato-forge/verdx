from verdx.domains.markdown.validators.style import check_style


def test_flags_trailing_whitespace(tmp_path):
    (tmp_path / "a.md").write_text("clean line\ntrailing   \n")
    assert any("trailing whitespace" in i for i in check_style.fn(tmp_path))


def test_flags_tab(tmp_path):
    (tmp_path / "a.md").write_text("no\ttabs\n")
    assert any("contains a tab" in i for i in check_style.fn(tmp_path))


def test_clean_style_passes(tmp_path):
    (tmp_path / "a.md").write_text("clean\nlines\n")
    assert check_style.fn(tmp_path) == []


def test_single_file_target_is_checked(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("clean line\ntrailing   \n")
    assert any("trailing whitespace" in i for i in check_style.fn(f))
