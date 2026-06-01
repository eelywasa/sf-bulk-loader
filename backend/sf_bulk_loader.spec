# sf_bulk_loader.spec — PyInstaller build spec for the desktop backend binary.
#
# Build from the backend/ directory:
#   pyinstaller sf_bulk_loader.spec --clean --noconfirm
#
# Output: backend/dist/sf_bulk_loader/  (--onedir format)
# The entire dist/sf_bulk_loader/ folder is bundled into the Electron app as
# an extraResource at Contents/Resources/backend/sf_bulk_loader/.

from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

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

# mcp SDK data files. Use collect_data_files — NOT collect_all/collect_submodules
# over the whole `mcp` package: walking all of mcp imports the optional `mcp.cli`,
# which requires the mcp[cli] extra (typer) we don't bundle, and crashes the build.
# The runtime only needs mcp.server.* / mcp.shared.* (see hiddenimports below).
datas += collect_data_files('mcp')

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

    # anyio — loaded dynamically by fastapi/starlette; missed by static analysis
    'anyio',
    'anyio._backends._asyncio',
    'anyio._backends._trio',
    'anyio.abc',
    'anyio.streams.memory',

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

    # pyotp + segno (2FA TOTP verification + QR rendering — SFBL-244)
    'pyotp',
    'segno',

    # ── MCP server (SFBL-364) ──────────────────────────────────────────────
    # Lazily imported only when argv == ['mcp'], but PyInstaller needs them
    # bundled so the frozen binary can import them at run time.
    'sf_bulk_loader_mcp',
    'sf_bulk_loader_mcp.server',
    'sf_bulk_loader_mcp.client',
    'sf_bulk_loader_mcp.config',
    'sf_bulk_loader_mcp.discovery',
    'sf_bulk_loader_mcp.tools',
    'sf_bulk_loader_mcp.tools.health',
    'sf_bulk_loader_mcp.tools.connections',
    'sf_bulk_loader_mcp.tools.plans',
    'sf_bulk_loader_mcp.tools.runs',
    'sf_bulk_loader_mcp.tools.results',
    'mcp',
    'mcp.server',
    'mcp.server.stdio',
    'mcp.types',
]

# Collect all uvicorn submodules — its protocol loading is dynamic
hiddenimports += collect_submodules('uvicorn')

# Collect the mcp submodules the stdio server uses — scoped to mcp.server and
# mcp.shared to AVOID importing the optional mcp.cli (requires typer / mcp[cli]).
hiddenimports += collect_submodules('mcp.server')
hiddenimports += collect_submodules('mcp.shared')

# mcp.server.__init__ eagerly imports FastMCP, and mcp.server.lowlevel does
# `import jsonschema`. jsonschema's runtime stack — jsonschema-specifications
# ships meta-schema DATA loaded via importlib.resources, and referencing uses
# the compiled `rpds` extension — is NOT fully captured by PyInstaller's static
# analysis on a clean build venv (it only worked locally by luck). Collect it
# explicitly so the frozen `mcp` subcommand imports cleanly on Linux/Windows.
mcp_extra_binaries = []
for _pkg in ('jsonschema', 'jsonschema_specifications', 'referencing', 'rpds'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    mcp_extra_binaries += _b
    hiddenimports += _h
hiddenimports += ['sse_starlette', 'sse_starlette.sse', 'pydantic_settings']

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
    pathex=['.', '../mcp-server/src'],
    binaries=mcp_extra_binaries,
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
