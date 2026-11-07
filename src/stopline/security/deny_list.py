# The secret deny-list (spec sections 14 and 17, NFR-08): files Stopline never reads.
# Spec section 19 calls this module secrets.py; that name trips secret-file guards.

# The defaults from spec section 14. Each name matches in any folder, as in a .gitignore.
BUILT_IN_DENY = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "client_secret*.json",
    "token*.json",
    "credentials*",
    "*.p12",
    "*.pfx",
)


def pathspecs(*, exclude: bool) -> list[str]:
    """Return git pathspecs that select, or with `exclude` leave out, every secret file.

    Matching ignores case, so `.ENV` is a secret too.
    """
    magic = "exclude,glob,icase" if exclude else "glob,icase"
    return [f":({magic})**/{pattern}" for pattern in BUILT_IN_DENY]
