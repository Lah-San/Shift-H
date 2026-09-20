"""Start the Shift-H app.  python run.py  ->  http://127.0.0.1:8000"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leavecover import env  # noqa: F401  (loads .env)
import uvicorn

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    host = os.environ.get('HOST', '127.0.0.1')   # the Dockerfile sets 0.0.0.0 for hosting
    print(f'Shift-H starting on http://{host}:{port}  (GEMINI_API_KEY {"set" if os.environ.get("GEMINI_API_KEY") else "not set: built-in assistant will answer chat"})')
    uvicorn.run('leavecover.api:app', host=host, port=port, reload=False)
