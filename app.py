"""inbox-agent — FastAPI + Gradio (Milestone 4 fills this in; see CLAUDE.md §7).

Run: uvicorn app:app --port 7860
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="inbox-agent", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
