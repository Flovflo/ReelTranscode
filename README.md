# ReelTranscode

Production-focused Apple Direct Play optimization pipeline for media libraries.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

```bash
reeltranscode --config config/reeltranscode.yaml batch
reeltranscode --config config/reeltranscode.yaml watch
reeltranscode --config config/reeltranscode.yaml batch --dry-run
```

## Commands

- `batch`: process existing files under watch roots
- `watch`: daemon mode for new files
- `analyze <path>`: decision-only inspection
- `process <path>`: process one file

## Notes

- This tool prioritizes stream copy and remux before any transcode.
- Dolby Vision handling is conservative; fragile paths are downgraded based on policy.
- Reports are written as JSON and CSV under `reports/`.
