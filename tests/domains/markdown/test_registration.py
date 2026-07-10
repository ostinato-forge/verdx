import verdx


def test_markdown_gate_validates_a_real_target(tmp_path):
    (tmp_path / "README.md").write_text("# Title\n### Skips h2\ntrailing   \n")
    assessment = verdx.validate(str(tmp_path), domain="markdown", flywheel=False)
    messages = " ".join(o.message for o in assessment.observations)
    assert "heading jumps" in messages
    assert "trailing whitespace" in messages


def test_fix_repairs_trailing_whitespace(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("# Title\nline   \n")
    verdx.fix(str(tmp_path), domain="markdown", flywheel=False)
    assert "line   " not in f.read_text()
