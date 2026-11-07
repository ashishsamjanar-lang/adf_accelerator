"""Real worker generator + validator for the E2E API stub (`healthcare-scenarios.spec.ts`).

The Playwright stub has no backend, Postgres or worker. For scenario runs it shells out to this
script on `POST /pipelines/{id}/generate|validate` so the UI shows what the *real* worker would
produce: the same calls `worker/app/jobs/runner.py` makes (`validate_draft` → `build_spec` →
`build_generated_artifacts` → `run_validation`), minus the database.

Usage: `python worker_bridge.py [OUT_DIR]` with the SpecDraft JSON on stdin. Prints one JSON object;
when OUT_DIR is given, every generated artifact is also written there for inspection.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORKER = Path(__file__).resolve().parents[3] / "worker"
sys.path.insert(0, str(WORKER))

from app.generator import build_generated_artifacts, build_spec  # noqa: E402
from app.generator.spec import SpecDraft, validate_draft  # noqa: E402
from app.validator import run_validation  # noqa: E402


def main() -> None:
    draft = SpecDraft.from_dict(json.loads(sys.stdin.read() or "{}"))
    draft_errors = [i.to_dict() for i in validate_draft(draft) if i.severity == "error"]
    if draft_errors:
        print(json.dumps({"generated": False, "draftIssues": draft_errors, "artifacts": [], "validation": None}))
        return
    spec = build_spec(draft)
    artifacts = build_generated_artifacts(draft)
    report = run_validation(spec, artifacts)
    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
        for artifact in artifacts:
            target = out / artifact.file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(artifact.content, encoding="utf-8")
    print(
        json.dumps(
            {
                "generated": True,
                "draftIssues": [],
                "artifacts": [
                    {k: v for k, v in a.to_dict().items() if k != "content"} | {"bytes": a.bytes} for a in artifacts
                ],
                "validation": {"state": report.state, "issues": [i.to_dict() for i in report.issues]},
            }
        )
    )


if __name__ == "__main__":
    main()
