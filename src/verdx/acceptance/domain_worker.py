"""Run a domain in a child process and emit its complete assessment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from verdx.engine.api import validate


def main() -> None:
    domain, target = sys.argv[1:]
    assessment = validate(Path(target), domains=[domain], static_only=True)
    print(json.dumps(assessment.to_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
