"""Application version.

Single line on purpose: the release workflow rewrites it at build time, the same
way taplock-app's workflow injects the computed version into `Info.plist` before
packaging. CI derives that version from the latest tag plus the commit message
prefix, so nothing in the repository should ever hold a real version number.

Running from a checkout it stays `dev`. There is no tag to read once the app is
frozen into an executable, and guessing one here would only ever be wrong.
"""

VERSION = "dev"


def display():
    """`v1.2.3` for a release build, plain `dev` from source."""
    return "dev" if VERSION == "dev" else f"v{VERSION}"
