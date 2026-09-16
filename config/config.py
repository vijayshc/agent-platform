import os
import logging
import logging.config
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# OpenRouter / Provider configuration (used only for initial default seeding if llm_connections is empty)
# NOTE: All active LLM provider connections and API keys are managed dynamically via
# the admin LLM Connection Manager (src/utils/llm_connection_manager.py) and stored in llm_connections table.
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_BASE_URL = os.getenv('OPENROUTER_BASE_URL', 'https://api.commandcode.ai/provider/v1')
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL', 'z-ai/glm-5.3-flash')

OPENAI_API_KEY = OPENROUTER_API_KEY
OPENAI_API_BASE = OPENROUTER_BASE_URL


# Azure OpenAI configuration (kept for backward compatibility)
AZURE_ENDPOINT = os.getenv('AZURE_ENDPOINT', 'https://models.inference.ai.azure.com')
AZURE_MODEL_NAME = os.getenv('AZURE_MODEL_NAME', 'Phi-3-small-8k-instruct')
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')

# Database configuration
DATABASE_URI = os.getenv('DATABASE_URI', 'sqlite:///text2sql.db')

# Application settings
# Support both DEBUG and FLASK_DEBUG (common convention) for developer convenience
DEBUG = (
    os.environ.get("DEBUG", "False").lower() == "true"
    or os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
)
SECRET_KEY = os.environ.get("SECRET_KEY", "default-dev-key-change-in-production")

# Session cookie security.
# IMPORTANT: Set SESSION_COOKIE_SECURE=true ONLY when the app is served over HTTPS.
# Default false allows login to work when accessing the app directly over HTTP (common for dev/local use).
# In production behind a TLS-terminating proxy (nginx, etc.), you can set this to true.
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"

# Authentication settings
# Options: 'local' (password in DB) or 'ldap'
AUTH_PROVIDER = os.getenv('AUTH_PROVIDER', 'local').lower()

# LDAP configuration (used when AUTH_PROVIDER='ldap')
LDAP_SERVER_URI = os.getenv('LDAP_SERVER_URI', '')  # e.g., ldap://ad.example.com or ldaps://ad.example.com
LDAP_USE_SSL = os.getenv('LDAP_USE_SSL', 'true').lower() == 'true'
LDAP_START_TLS = os.getenv('LDAP_START_TLS', 'false').lower() == 'true'

# How to bind with the end-user credentials. Examples:
#   '{username}@example.com' (userPrincipalName) or 'EXAMPLE\\{username}' (DOMAIN\\user)
LDAP_BIND_DN_TEMPLATE = os.getenv('LDAP_BIND_DN_TEMPLATE', '{username}')

# Where and how to search the user entry after bind
LDAP_USER_SEARCH_BASE = os.getenv('LDAP_USER_SEARCH_BASE', '')  # e.g., 'DC=example,DC=com'
# Example filters: '(sAMAccountName={username})' or '(userPrincipalName={username}@example.com)'
LDAP_USER_FILTER_TEMPLATE = os.getenv('LDAP_USER_FILTER_TEMPLATE', '(sAMAccountName={username})')

# Allowed AD group (full DN) for access control. Example:
# 'CN=Text2SQL Users,OU=Groups,DC=example,DC=com'
LDAP_ALLOWED_GROUP_DN = os.getenv('LDAP_ALLOWED_GROUP_DN', '')

# Optional attribute mappings to fetch from LDAP
LDAP_ATTRIBUTE_MAIL = os.getenv('LDAP_ATTRIBUTE_MAIL', 'mail')
LDAP_ATTRIBUTE_DISPLAY_NAME = os.getenv('LDAP_ATTRIBUTE_DISPLAY_NAME', 'displayName')

# Model configuration
MAX_TOKENS = int(os.getenv('MAX_TOKENS', '10000'))
TEMPERATURE = float(os.getenv('TEMPERATURE', '0.7'))

# Optional browser-local LLM proxy configuration
def _get_env_non_empty(key: str, default: str) -> str:
    value = os.getenv(key)
    if value is None:
        return default
    value = value.strip()
    return value if value else default

BROWSER_LLM_PROXY_ENABLED = os.getenv('BROWSER_LLM_PROXY_ENABLED', 'false').lower() == 'true'
BROWSER_LLM_PROXY_URL = _get_env_non_empty('BROWSER_LLM_PROXY_URL', 'http://127.0.0.1:5010/llm')
BROWSER_LLM_PROXY_TIMEOUT_SECONDS = int(os.getenv('BROWSER_LLM_PROXY_TIMEOUT_SECONDS', '120'))
BROWSER_LLM_PROXY_POLL_INTERVAL_SECONDS = float(os.getenv('BROWSER_LLM_PROXY_POLL_INTERVAL_SECONDS', '0.25'))

# Message format configuration
MESSAGE_FORMAT = os.getenv('MESSAGE_FORMAT', 'openai').lower()  # 'openai' or 'llama'
# Valid options: 'openai', 'llama'
SUPPORTED_MESSAGE_FORMATS = ['openai', 'llama']

# Validate message format
if MESSAGE_FORMAT not in SUPPORTED_MESSAGE_FORMATS:
    print(f"Warning: Invalid MESSAGE_FORMAT '{MESSAGE_FORMAT}'. Defaulting to 'openai'.")
    MESSAGE_FORMAT = 'openai'

# Knowledge base configuration
UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
os.makedirs(UPLOADS_DIR, exist_ok=True)
CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', '1000'))
CHUNK_OVERLAP = int(os.getenv('CHUNK_OVERLAP', '200'))

# File browser configuration
FILE_BROWSER_ROOT = os.path.abspath(os.getenv('FILE_BROWSER_ROOT', UPLOADS_DIR))
os.makedirs(FILE_BROWSER_ROOT, exist_ok=True)

_default_file_exts = '.txt,.md,.json,.csv,.yaml,.yml,.sql,.py'
_configured_exts = os.getenv('FILE_BROWSER_ALLOWED_EXTENSIONS', _default_file_exts)
FILE_BROWSER_ALLOWED_EXTENSIONS = {
    ext if ext.startswith('.') else f'.{ext}'
    for ext in (item.strip().lower() for item in _configured_exts.split(',') if item.strip())
}


# Conversation history configuration
KNOWLEDGE_CONVERSATION_HISTORY_LIMIT = int(os.getenv('KNOWLEDGE_CONVERSATION_HISTORY_LIMIT', '10'))

# Rate Limiting Configuration
RATE_LIMIT_ENABLED = os.getenv('RATE_LIMIT_ENABLED', 'false').lower() == 'true'
LOGIN_ATTEMPT_LIMIT = int(os.getenv('LOGIN_ATTEMPT_LIMIT', '10'))  # Maximum login attempts per window
LOGIN_ATTEMPT_WINDOW = int(os.getenv('LOGIN_ATTEMPT_WINDOW', '3600'))  # Time window in seconds (default: 1 hour)
FAILED_LOGIN_LOCKOUT_THRESHOLD = int(os.getenv('FAILED_LOGIN_LOCKOUT_THRESHOLD', '5'))  # Lock account after this many failed attempts

# ChromaDB Service configuration
CHROMADB_SERVICE_URL = os.getenv('CHROMADB_SERVICE_URL', 'http://localhost:8001')
CHROMADB_SERVICE_TIMEOUT = int(os.getenv('CHROMADB_SERVICE_TIMEOUT', '30'))

# Logging configuration
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# Audit trail: one file per day, written centrally by the authorization layer
# (src/auth/audit.py).  Older daily archives are pruned past the retention window.
AUDIT_LOG_ENABLED = os.getenv('AUDIT_LOG_ENABLED', 'true').lower() == 'true'
AUDIT_LOG_DIR = os.getenv('AUDIT_LOG_DIR', os.path.join(LOG_DIR, 'audit'))
AUDIT_LOG_RETENTION_DAYS = int(os.getenv('AUDIT_LOG_RETENTION_DAYS', '365'))

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        # Stamps the signed-in account onto every record so the formatters below
        # can render it - including Werkzeug's own access line.
        'user_context': {
            '()': 'src.utils.log_filters.UserContextFilter'
        }
    },
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s] %(name)s [%(user)s]: %(message)s'
        },
        'detailed': {
            'format': '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - [%(user)s] - %(message)s'
        },
        'query': {
            'format': '%(asctime)s - [%(user)s] - %(message)s'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'filters': ['user_context'],
            'stream': 'ext://sys.stderr'
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'DEBUG',
            'formatter': 'detailed',
            'filters': ['user_context'],
            'filename': os.path.join(LOG_DIR, 'text2sql.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5
        },
        'error_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'ERROR',
            'formatter': 'detailed',
            'filters': ['user_context'],
            'filename': os.path.join(LOG_DIR, 'error.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5
        },
        'query_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': 'INFO',
            'formatter': 'query',
            'filters': ['user_context'],
            'filename': os.path.join(LOG_DIR, 'queries.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5
        }
    },
    'loggers': {
        'text2sql': {
            'level': 'DEBUG',
            'handlers': ['console', 'file', 'error_file'],
            'propagate': False
        },
        'text2sql.agents': {
            'level': 'DEBUG',
            'handlers': ['console', 'file', 'error_file'],
            'propagate': False
        },
        'text2sql.queries': {
            'level': 'INFO',
            'handlers': ['query_file'],
            'propagate': False
        },
        'text2sql.schema_manager': {
            'level': 'DEBUG',
            'handlers': ['console', 'file', 'error_file'],
            'propagate': False
        },
        'text2sql.sql_generator': {
            'level': 'DEBUG',
            'handlers': ['console', 'file', 'error_file'],
            'propagate': False
        }
    },
    'root': {
        'level': 'WARNING',
        'handlers': ['console', 'file']
    }
}

# Initialize logging configuration only if no handlers exist
logger = logging.getLogger('text2sql')
if not logger.hasHandlers():
    logging.config.dictConfig(LOGGING_CONFIG)

# MCP server scripts are configured per-server in the database (see mcp_servers table).
# ---------------------------------------------------------------------------
# Hosted apps (see docs/hosted-apps-isolation.md)
#
# These are the recommended values and they live here, not in .env, because a
# .env file does not travel with the code: a deployment that never had one would
# otherwise start with the feature switched off and the isolation tier
# unconfigured. Every value can still be overridden from the environment.
# ---------------------------------------------------------------------------
HOSTED_APPS_ENABLED = os.getenv("HOSTED_APPS_ENABLED", "true").lower() == "true"

# "auto" adopts the strongest isolation this host can actually provide
# (A namespaces > B+ Landlock > B seccomp+rlimits). Pinning "A", "B+" or "B"
# turns an unavailable tier into a refusal to start apps rather than a silent
# downgrade.
HOSTED_APPS_TIER = os.getenv("HOSTED_APPS_TIER", "auto").strip().lower()

# Bring back, at boot, every app the operator left running - a restart should be
# invisible - plus any app whose manifest sets autostart and that the operator
# has not stopped. Setting this to false starts nothing at boot at all; apps
# then run only when they are started from Hosted Apps.
HOSTED_APPS_AUTOSTART = os.getenv("HOSTED_APPS_AUTOSTART", "true").lower() == "true"

# Where app workspaces live, and the tree an app must never be able to see.
HOSTED_APPS_ROOT = os.getenv("HOSTED_APPS_ROOT", "").strip()
HOSTED_APPS_PLATFORM_ROOT = os.getenv("HOSTED_APPS_PLATFORM_ROOT", "").strip()
HOSTED_APPS_PYTHON = os.getenv("HOSTED_APPS_PYTHON", "").strip()

# The origin hosted apps are served from. App *content* must not share the
# platform's origin: a document on the platform origin is same-origin with the
# platform's own JavaScript, so a hosted app could call every platform API as the
# signed-in user through the browser, which no process sandbox can prevent.
#
# Default: a second listener on this host, port 5001, serving only /apps/*.
# Production: set HOSTED_APPS_ORIGIN to the public apps origin
# (e.g. https://apps.example.com) and serve it there - a reverse proxy, or a
# second WSGI instance started with APP_ORIGIN_ONLY=1 on that port.
# HOSTED_APPS_ORIGIN_PORT=0 restores same-origin serving for a migration window;
# it is insecure and every start says so in the log.
HOSTED_APPS_ORIGIN = os.getenv("HOSTED_APPS_ORIGIN", "").strip()
HOSTED_APPS_ORIGIN_PORT = int(os.getenv("HOSTED_APPS_ORIGIN_PORT", "5001"))
HOSTED_APPS_ORIGIN_HOST = os.getenv("HOSTED_APPS_ORIGIN_HOST", "0.0.0.0").strip()
# Only needed when the platform's public origin differs from the request's own
# (TLS terminated upstream), so redirects and cross-origin checks compare the
# right pair.
HOSTED_APPS_PLATFORM_ORIGIN = os.getenv("HOSTED_APPS_PLATFORM_ORIGIN", "").strip()

# Creating an app's virtualenv and installing its declared dependencies.
HOSTED_APPS_VENV = os.getenv("HOSTED_APPS_VENV", "true").lower() == "true"
HOSTED_APPS_REQUIRE_REQUIREMENTS = os.getenv("HOSTED_APPS_REQUIRE_REQUIREMENTS", "true").lower() == "true"
HOSTED_APPS_NPM_INSTALL = os.getenv("HOSTED_APPS_NPM_INSTALL", "true").lower() == "true"
HOSTED_APPS_NODE_BUILD = os.getenv("HOSTED_APPS_NODE_BUILD", "true").lower() == "true"
HOSTED_APPS_INSTALL_TIMEOUT = int(os.getenv("HOSTED_APPS_INSTALL_TIMEOUT", "900"))

# TCP ports a dependency install may reach. The installer needs the package
# index and nothing else: with Landlock's port rules (ABI 4+) everything else -
# including the platform's own loopback API and sibling app sockets - is denied
# while the index stays reachable. Widen it for an internal mirror on another
# port (for example "80,443,8080").
HOSTED_APPS_INSTALL_NET_PORTS = os.getenv("HOSTED_APPS_INSTALL_NET_PORTS", "80,443")

# Sharing data with a hosted app. The size cap bounds what a request may buffer
# in the platform before it is forwarded.
HOSTED_APPS_MAX_BODY_BYTES = int(os.getenv("HOSTED_APPS_MAX_BODY_BYTES", str(32 * 1024 * 1024)))

# Isolation policy. Unprivileged user namespaces give the strongest tier; a site
# whose hardening policy forbids them sets this false and gets B+ or B.
HOSTED_APPS_ALLOW_USERNS = os.getenv("HOSTED_APPS_ALLOW_USERNS", "true").lower() == "true"

# Installing dependencies executes third-party build code. It runs confined to
# the app's directory; on a kernel that cannot confine it the install is refused
# unless this accepts the risk of platform privileges.
HOSTED_APPS_ALLOW_UNCONFINED_INSTALL = (
    os.getenv("HOSTED_APPS_ALLOW_UNCONFINED_INSTALL", "false").lower() == "true"
)

# Denying signals stops a confined app attacking sibling processes, at the cost
# of gunicorn no longer being able to reap a hung worker.
HOSTED_APPS_ALLOW_SIGNALS = os.getenv("HOSTED_APPS_ALLOW_SIGNALS", "false").lower() == "true"

# A Landlock-confined app cannot read /proc unless this is set: withholding it
# hides the host's process table and their environment files.
HOSTED_APPS_LANDLOCK_PROC = os.getenv("HOSTED_APPS_LANDLOCK_PROC", "false").lower() == "true"

# Extra environment variables handed to every hosted app, as JSON.
HOSTED_APPS_APP_ENV = os.getenv("HOSTED_APPS_APP_ENV", "").strip()
