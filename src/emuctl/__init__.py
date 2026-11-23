"""emuctl: run emulated machines as services."""
import os

__version__ = "0.3.0"


def full_version(here=None):
    """The version, plus the git commit it was installed from when known.

    install.sh writes the commit to a BUILD file beside this one. A release
    archive or a pip install has no BUILD file, and gets the bare version.
    """
    here = here or os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "BUILD")) as fh:
            commit = fh.read().strip()
    except OSError:
        commit = ""
    return f"{__version__} ({commit})" if commit else __version__
