"""Only the Drive service is vendored; upstream imports every service here."""
from icloudlite.services.drive import DriveService

__all__ = ["DriveService"]
