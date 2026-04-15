"""trevor — airlock manager for KARECTL."""

import uvicorn


def main() -> None:
    uvicorn.run("trevor.app:app", host="0.0.0.0", port=8000, reload=False)  # noqa: S104
