import fnmatch
import os

DEFAULT_IGNORE = [
    "*.tmp", "*.swp", "*.part",
    "*.icloud", ".DS_Store", "._*",
    "~$*", ".trash/",
    ".sync-tmp-*",
    ".obsidian/workspace*",
]


def should_ignore(rel_path, extra_patterns=None):
    patterns = list(DEFAULT_IGNORE)
    if extra_patterns:
        patterns.extend(extra_patterns)

    name = os.path.basename(rel_path)
    for pat in patterns:
        if pat.endswith("/"):
            if fnmatch.fnmatch(rel_path + "/", pat):
                return True
        else:
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_path, pat):
                return True
    return False
