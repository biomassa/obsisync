"""The single source of truth for the version.

It lived in seven places before: pyproject, build.py, the entry point, the
PKGBUILD, the NSIS script and two workflow fallbacks. Keeping them in step by
hand is a reliable way to ship mislabelled artifacts.
"""
__version__ = "0.1.1"
