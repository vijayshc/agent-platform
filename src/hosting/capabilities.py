"""What isolation can this host actually provide? Probed, never assumed.

Kernel version is not a reliable indicator: RHEL 8 is a backported 4.18 base,
hardened images ship the same kernel with user namespaces switched off, and
Landlock can be compiled in but disabled at boot. The only trustworthy answer
comes from trying each mechanism once per process.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import shutil
import subprocess

from src.hosting import settings

USENS_PROBE_TIMEOUT = 10
_SYS_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_CREATE_RULESET_VERSION = 1

ERRNO_ENOSYS = 38
ERRNO_EOPNOTSUPP = 95


def userns_available() -> bool:
    """Can an unprivileged process create its own namespaces?

    The decisive question on RHEL 8: ``unshare`` ships in util-linux (base
    install, no download, no root), so if this is true we get mount + network +
    pid isolation with nothing installed.

    ``HOSTED_APPS_ALLOW_USERNS=false`` refuses to use them even where the kernel
    allows it - for sites whose hardening policy forbids unprivileged user
    namespaces. The tier then falls to B+ or B, which the UI and the audit trail
    both report.
    """
    from src.hosting import settings

    if not settings.allow_userns():
        return False
    if not shutil.which("unshare"):
        return False
    try:
        probe = subprocess.run(
            ["unshare", "--user", "--map-root-user", "true"],
            capture_output=True,
            timeout=USENS_PROBE_TIMEOUT,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def seccomp_available() -> bool:
    """Whether a seccomp filter can be loaded. Never needs root."""
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2")
        return bool(lib.seccomp_init)
    except OSError:
        return False


def landlock_probe() -> tuple[int, str]:
    """``(abi, status)`` for Landlock.

    The status distinguishes the two failure codes, because they call for
    completely different responses:

    ``ENOSYS``     the kernel predates Landlock (5.13). Nothing can be
                   installed to add it - not a package, not a tarball.
    ``EOPNOTSUPP`` Landlock exists but was disabled at boot; enabling it means
                   ``lsm=landlock`` on the kernel command line, i.e. root and a
                   reboot.
    """
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        result = libc.syscall(
            _SYS_LANDLOCK_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION)
        )
        if result >= 0:
            return int(result), f"abi:{int(result)}"
        errno = ctypes.get_errno()
        if errno == ERRNO_ENOSYS:
            return 0, "unsupported (kernel has no Landlock)"
        if errno == ERRNO_EOPNOTSUPP:
            return 0, "disabled at boot (needs lsm=landlock)"
        return 0, f"error (errno {errno})"
    except Exception as exc:  # pragma: no cover - defensive
        return 0, f"error ({type(exc).__name__})"


def best_available_tier(caps: dict) -> str:
    if caps["userns"]:
        return "A"
    if caps["landlock_abi"]:
        return "B+"
    return "B"


def resolve_tier(caps: dict, configured: str | None = None) -> tuple[str | None, str]:
    """Pick the tier to run under, or refuse.

    Returns ``(tier, reason)``. ``tier`` is ``None`` when a *pinned* tier is
    unavailable: refusing to start is deliberate, because silently falling back
    from an isolating tier to a non-isolating one would be a security
    regression that nobody notices.
    """
    configured = (configured or settings.configured_tier()).strip()
    if configured == "auto":
        tier = best_available_tier(caps)
        return tier, f"auto-selected {tier}"

    if configured == "A" and not caps["userns"]:
        return None, (
            "HOSTED_APPS_TIER=A but this host cannot create user namespaces "
            "(`unshare -Ur` failed). Set HOSTED_APPS_TIER=auto to accept the "
            "best available mechanism, or ask the platform team to enable "
            "user.max_user_namespaces."
        )
    if configured == "B+" and not caps["landlock_abi"]:
        return None, (
            f"HOSTED_APPS_TIER=B+ but Landlock is {caps['landlock_status']}. "
            "No tarball can supply it. Use HOSTED_APPS_TIER=auto or B."
        )
    return configured, f"pinned by configuration ({configured})"


@functools.lru_cache(maxsize=1)
def detect() -> dict:
    """Probe once per process and cache; used at startup, in the UI and in audit."""
    abi, status = landlock_probe()
    caps = {
        "userns": userns_available(),
        "seccomp": seccomp_available(),
        "landlock_abi": abi,
        "landlock_status": status,
    }
    caps["best_tier"] = best_available_tier(caps)
    tier, reason = resolve_tier(caps)
    caps["tier"] = tier
    caps["tier_reason"] = reason
    caps["filesystem_isolated"] = tier in ("A", "B+")
    # Network isolation is delivered by the network namespace (tier A), by
    # seccomp denying socket creation (all tiers), or - only from Landlock ABI 10
    # - by Landlock itself. Without any of those the host cannot deliver it, and
    # saying otherwise would be a false assurance in the UI and the audit trail.
    caps["network_isolated"] = bool(
        tier is not None and (caps["userns"] or caps["seccomp"] or caps["landlock_abi"] >= 10)
    )
    return caps


def describe(caps: dict | None = None) -> str:
    caps = caps or detect()
    tier = caps.get("tier")
    if tier is None:
        return f"No tier available: {caps['tier_reason']}"
    if tier == "A":
        return (
            "Tier A - user namespaces: the platform's files are hidden from the "
            "app, it has no network interfaces, and it is resource-capped."
        )
    if tier == "B+":
        abi = caps["landlock_abi"]
        detail = "filesystem + TCP" if abi >= 4 else "filesystem only (no TCP restriction)"
        return f"Tier B+ - Landlock ABI {abi} ({detail}) + seccomp + rlimits."
    warning = "" if caps["seccomp"] else (
        " WARNING: libseccomp is unavailable, so socket creation could not be "
        "denied - this host does NOT isolate the app from the network."
    )
    return (
        "Tier B - seccomp + rlimits only: the app cannot create sockets or reach "
        "the network, but it shares the platform's user and CAN read its files. "
        "Host only reviewed, administrator-authored apps on this node." + warning
    )


def tier_guide() -> list[dict]:
    """Plain-language description of every tier, for the admin UI.

    Kept next to the tier logic rather than in the front-end so the explanation
    cannot drift from the mechanism, and so a reviewer can read one file to see
    both what is enforced and what is claimed.
    """
    return [
        {
            "key": "A",
            "label": "Tier A - user namespaces",
            "tagline": "Strongest. The app has its own filesystem, network and process view.",
            "works": [
                "Serve requests, and read and write its own directory",
                "Use its own virtualenv and installed libraries",
                "Receive the signed-in user's identity from the platform",
            ],
            "blocked": [
                "Reading platform files - .env, the database, the source: they do not exist in its namespace",
                "Any network access: the namespace has no interfaces beyond loopback",
                "Creating sockets (it inherits its listener), seeing or signalling other processes",
            ],
            "caveat": "Memory and CPU limits are per process, so a multi-worker app multiplies them; and the app cannot restart itself.",
        },
        {
            "key": "B+",
            "label": "Tier B+ - Landlock",
            "tagline": "Strong. The kernel denies the app access to platform paths.",
            "works": [
                "Serve requests, and read and write its own directory",
                "Use its own virtualenv and installed libraries",
                "Receive the signed-in user's identity from the platform",
            ],
            "blocked": [
                "Reading or writing platform files: denied by the kernel, not by convention",
                "Creating sockets, and network access where seccomp is available",
                "Signalling other processes, and ptrace, mount and kernel interfaces",
            ],
            "caveat": "The kernel cannot restrict stat/chmod/utime, so the app can still inspect file metadata and change permissions on files it cannot read - a denial-of-service risk, not a disclosure one.",
        },
        {
            "key": "B",
            "label": "Tier B - seccomp and limits only",
            "tagline": "Floor. No filesystem isolation: the app runs as the platform's own user.",
            "works": [
                "Serve requests, and read and write its own directory",
                "Everything the app needs to run normally",
            ],
            "blocked": [
                "Network access and socket creation",
                "Signalling other processes, and ptrace, mount and kernel interfaces",
            ],
            "caveat": "It CAN read .env, SECRET_KEY and the database, and modify platform code and the audit trail. Host only reviewed, administrator-authored apps on a node in this state.",
        },
    ]


def platform_controls() -> list[str]:
    """What every tier gets, because it comes from the platform rather than the sandbox."""
    return [
        "Sign-in is required, and the app is refused without access to the Hosted Apps module",
        "The platform tells the app who is signed in; the app never authenticates anyone",
        "Every request is recorded in the audit trail, attributed to the user and the app",
        "The app is served at /apps/<name>/ on the platform's hosted-apps origin - it has no "
        "port or hostname of its own, and the platform's own URL redirects there",
        "App content runs on its own origin, so its JavaScript cannot call platform APIs as "
        "the signed-in user",
    ]


def exposure_warnings(caps: dict | None = None) -> list[str]:
    """Configuration that quietly weakens the tier this host selected.

    Said out loud at boot rather than enforced, because each one is a deployment
    choice (an existing database location, a secret that has not been set) rather
    than a bug in the feature.
    """
    caps = caps or detect()
    warnings: list[str] = []

    if settings.secret_key_is_default():
        warnings.append(
            "SECRET_KEY is the built-in default. Session cookies and the identity headers "
            "handed to apps are signed with a value anyone can read in the source; set "
            "SECRET_KEY before hosting anything."
        )

    if caps.get("tier") == "A":
        database = _sqlite_database_path()
        if database is not None and not _hidden_by_tier_a(database):
            warnings.append(
                f"tier A hides {settings.platform_root()} and $HOME, but the database is at "
                f"{database}, which the app can still read. Move it under one of those trees, "
                "or run this app on a tier that denies paths (B+)."
            )
    return warnings


def _sqlite_database_path():
    """The database file, when it is a local SQLite one."""
    from pathlib import Path

    from config import config as platform_config

    uri = str(getattr(platform_config, "DATABASE_URI", "") or "")
    if not uri.startswith("sqlite"):
        return None
    raw = uri.split("sqlite:///")[-1]
    if not raw or raw == ":memory:":
        return None
    path = Path(raw)
    if not path.is_absolute():
        # Relative URIs resolve against the process working directory, which is
        # the platform root in every supported way of starting the app.
        path = settings.platform_root() / path
    return path.resolve()


def _hidden_by_tier_a(path) -> bool:
    from pathlib import Path

    import os

    hidden = [settings.platform_root(), Path.home(), Path("/dev/shm"), Path(f"/run/user/{os.getuid()}")]
    for candidate in hidden:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if path == resolved or resolved in path.parents:
            return True
    return False


def startup_report() -> dict:
    """One-line-per-fact summary for the application log at boot."""
    caps = detect()
    return {
        "enabled": settings.enabled(),
        "tier": caps.get("tier"),
        "tier_configured": settings.configured_tier(),
        "tier_reason": caps["tier_reason"],
        "userns": caps["userns"],
        "landlock": caps["landlock_status"],
        "seccomp": caps["seccomp"],
        "root": str(settings.root()),
    }
