from verdx.domains.markdown.validators.links import check_links


def test_single_file_target_is_checked(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("See [other](./missing.md)\n")
    assert any("broken link" in i for i in check_links.fn(f))


def test_flags_link_escaping_target_root(tmp_path):
    # A relative link whose resolved path is OUTSIDE the target root must be
    # flagged, not silently accepted, even if the file exists on disk.
    (tmp_path.parent / "outside.md").write_text("x\n")
    root = tmp_path / "docs"
    root.mkdir()
    (root / "a.md").write_text("See [x](../../outside.md)\n")
    issues = check_links.fn(root)
    assert any("outside" in i.lower() or "escapes" in i.lower() for i in issues)


def test_flags_broken_relative_link(tmp_path):
    (tmp_path / "a.md").write_text("See [other](./missing.md)\n")
    assert any("broken link to ./missing.md" in i for i in check_links.fn(tmp_path))


def test_valid_relative_link_passes(tmp_path):
    (tmp_path / "b.md").write_text("ok\n")
    (tmp_path / "a.md").write_text("See [other](./b.md)\n")
    assert check_links.fn(tmp_path) == []


def test_external_link_ignored(tmp_path):
    (tmp_path / "a.md").write_text("See [site](https://example.com)\n")
    assert check_links.fn(tmp_path) == []
