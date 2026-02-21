"""
Vercel serverless function entry point for FastAPI app.
"""
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from web.app import app

# Vercel expects a variable named 'app' or 'handler'
# FastAPI app is already ASGI-compatible
