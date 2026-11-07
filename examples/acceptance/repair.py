"""Apply the sample repair. Verdx must check the result afterward."""

from pathlib import Path

Path(__file__).with_name("value.txt").write_text("42\n")
