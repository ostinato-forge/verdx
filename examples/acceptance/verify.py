"""Check that the sample value meets its acceptance rule."""

from pathlib import Path

value = Path(__file__).with_name("value.txt").read_text().strip()
if value != "42":
    print(f"Expected 42, found {value}.")
    raise SystemExit(1)
print("The value is 42.")
