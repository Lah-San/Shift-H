"""Tests never touch the live database: point the app at a scratch file before anything imports it."""
import os, tempfile

_path = os.path.join(tempfile.gettempdir(), 'shift-h-tests.db')
os.environ.setdefault('LEAVECOVER_DB', _path)
if os.path.exists(_path):
    os.remove(_path)
