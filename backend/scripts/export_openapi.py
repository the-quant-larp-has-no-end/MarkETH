"""Write the API contract to `backend/openapi.json` (`make api-client`).

    uv run python -m scripts.export_openapi

The frontend's types are generated from this file, and CI fails when it is stale. The
output depends on the code alone: keys are sorted, and building the application reads
neither `.env` nor any data store.
"""

import json
from pathlib import Path
from typing import Final

from app.main import create_app

OPENAPI_FILE: Final[Path] = Path(__file__).resolve().parents[1] / "openapi.json"


def render_openapi() -> str:
    """The OpenAPI document as it is committed: sorted keys, two-space indent, one
    trailing newline."""
    document = create_app().openapi()
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    OPENAPI_FILE.write_text(render_openapi(), encoding="utf-8")
    print(f"Wrote {OPENAPI_FILE}")


if __name__ == "__main__":
    main()
