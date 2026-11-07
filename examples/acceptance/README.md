# Acceptance example

Run these commands from the Verdx repository after installing its dependencies:

```bash
cp -R examples/acceptance /tmp/verdx-acceptance-example
uv run verdx check /tmp/verdx-acceptance-example --output text
```

The check fails because `value.txt` contains 41 and the rule requires 42.
The command exits 1 and prints the saved receipt path. Keep that path.

Apply the repair and check again:

```bash
python3 /tmp/verdx-acceptance-example/repair.py
uv run verdx check /tmp/verdx-acceptance-example --output text
```

The second check passes and writes a new receipt. The first receipt remains.
Replay either receipt with `uv run verdx replay PATH/receipt.json`. Replay of the
first receipt still reports failure, even though the current file is repaired.
Replay does not run the checker or the repair script.
