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


def test_no_markdown_is_not_a_pass(tmp_path):
    (tmp_path / "app.py").write_text("print(1)\n")
    assessment = verdx.validate(tmp_path, domain="markdown", flywheel=False)
    assert assessment.verdict == "block"
    assert assessment.validators_run == ()
    assert assessment.metadata["gate"]["coverage_status"] != "full"
    assert all(
        "no applicable .md files" in gap["reason"]
        for gap in assessment.metadata["gate"]["coverage_gaps"]
    )
