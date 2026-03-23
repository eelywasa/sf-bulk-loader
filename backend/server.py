"""
PyInstaller entry point for the desktop binary.

Two modes:
  sf_bulk_loader            → start uvicorn server (normal operation)
  sf_bulk_loader --migrate  → run alembic upgrade head, then exit
"""
import os
import sys


def _run_migrations() -> None:
    """Run alembic migrations programmatically (no alembic CLI required)."""
    from alembic.config import Config
    from alembic import command

    # When frozen, sys._MEIPASS is the PyInstaller extraction directory where
    # bundled data files (alembic/ and alembic.ini) are placed at build time.
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    cfg = Config(os.path.join(base_dir, 'alembic.ini'))
    # Override script_location to absolute path so alembic finds versions/.
    cfg.set_main_option('script_location', os.path.join(base_dir, 'alembic'))
    command.upgrade(cfg, 'head')


def _run_server() -> None:
    """Start uvicorn serving app.main:app."""
    import uvicorn

    uvicorn.run(
        'app.main:app',
        host=os.environ.get('BACKEND_HOST', '127.0.0.1'),
        port=int(os.environ.get('BACKEND_PORT', '8000')),
        log_level='info',
    )


if '--migrate' in sys.argv:
    _run_migrations()
else:
    _run_server()
