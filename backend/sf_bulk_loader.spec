# sf_bulk_loader.spec — PyInstaller build spec for the desktop backend binary.
#
# Build from the backend/ directory:
#   pyinstaller sf_bulk_loader.spec --clean --noconfirm
#
# Output: backend/dist/sf_bulk_loader/  (--onedir format)
# The entire dist/sf_bulk_loader/ folder is bundled into the Electron app as
# an extraResource at Contents/Resources/backend/sf_bulk_loader/.

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# ── Data files ────────────────────────────────────────────────────────────────
# alembic/ directory (with versions/) and alembic.ini must be present at
# runtime so server.py --migrate can find them via sys._MEIPASS.
datas = [
    ('alembic', 'alembic'),   # migrations directory → _MEIPASS/alembic/
    ('alembic.ini', '.'),     # ini file → _MEIPASS/alembic.ini
]

# pydantic / pydantic-settings ship JSON schema data files
datas += collect_all('pydantic_settings')[0]
datas += collect_all('pydantic')[0]
datas += collect_all('structlog')[0]

# ── Hidden imports ────────────────────────────────────────────────────────────
hiddenimports = [
    # SQLAlchemy — only the SQLite/aiosqlite dialect is needed for desktop
    'sqlalchemy.dialects.sqlite',
    'sqlalchemy.dialects.sqlite.aiosqlite',
    'sqlalchemy.ext.asyncio',
    'aiosqlite',
    'greenlet',

    # Alembic runtime internals (not fully auto-detected)
    'alembic',
    'alembic.config',
    'alembic.command',
    'alembic.runtime.migration',
    'alembic.runtime.environment',
    'alembic.script',
    'alembic.script.base',
    'alembic.op',
    'alembic.context',
    'alembic.autogenerate',

    # App package — migration scripts import from app models/config
    'app',
    'app.main',
    'app.config',
    'app.database',
    'app.models',
    'app.models.connection',
    'app.models.input_connection',
    'app.models.job',
    'app.models.load_plan',
    'app.models.load_run',
    'app.models.load_step',
    'app.models.user',

    # FastAPI / Starlette
    'fastapi',
    'starlette.routing',
    'starlette.middleware.cors',
    'starlette.staticfiles',
    'starlette.websockets',

    # Uvicorn — protocol loading is string-based and missed by static analysis
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',

    # websockets (used by uvicorn WebSocket protocol)
    'websockets',
    'websockets.legacy',
    'websockets.legacy.server',
    'websockets.legacy.protocol',

    # cryptography — Fernet encryption + RSA for Salesforce JWT signing
    'cryptography',
    'cryptography.fernet',
    'cryptography.hazmat.primitives.asymmetric.rsa',
    'cryptography.hazmat.primitives.asymmetric.padding',
    'cryptography.hazmat.primitives.serialization',
    'cryptography.hazmat.backends.openssl',
    'cryptography.hazmat.backends.openssl.backend',

    # python-jose — uses string-based backend registry; must be explicit
    'jose',
    'jose.jwt',
    'jose.jws',
    'jose.backends',
    'jose.backends.rsa_backend',
    'jose.backends.cryptography_backend',

    # pydantic v2
    'pydantic',
    'pydantic.v1',

    # httpx (Salesforce API client)
    'httpx',
    'httpx._transports.default',
    'httpx._transports.asgi',

    # structlog
    'structlog',

    # aiofiles (used by csv_processor)
    'aiofiles',
    'aiofiles.os',
    'aiofiles.threadpool',

    # boto3 / botocore (imported at module level in input_connections.py and
    # input_storage.py — must be bundled even though desktop uses local storage)
    'boto3',
    'boto3.session',
    'botocore',
    'botocore.exceptions',
    'botocore.session',

    # bcrypt (password hashing)
    'bcrypt',

    # pyotp (2FA TOTP verification — SFBL-244)
    'pyotp',
]

# Collect all uvicorn submodules — its protocol loading is dynamic
hiddenimports += collect_submodules('uvicorn')

# ── Excludes ──────────────────────────────────────────────────────────────────
excludes = [
    # asyncpg has platform-specific C extensions and is unused on desktop
    # (SQLite only). It is not imported anywhere in the app tree.
    'asyncpg',
    # Test infrastructure
    'pytest',
    'pytest_asyncio',
    # Notebook / scientific artefacts sometimes pulled in by boto3/botocore
    'IPython',
    'matplotlib',
    'numpy',
    'pandas',
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ['server.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='sf_bulk_loader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX corrupts OpenSSL shared libraries bundled by cryptography on macOS
    # and Windows. Never enable.
    upx=False,
    # console=True is required so Electron's stdout/stderr listeners receive
    # backend logs. A windowed binary swallows all output.
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='sf_bulk_loader',
)
