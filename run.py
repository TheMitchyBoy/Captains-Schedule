#!/usr/bin/env python3
"""
Development server entry point.

Starts the FastAPI application with auto-reload enabled. For production,
use: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
