"""A Drive-only fork of pyicloud.

Vendored from pyicloud 2.6.5 (MIT — see LICENSE.pyicloud) and cut down to the
iCloud Drive surface this application actually uses. Upstream's ``base.py``
eagerly imports fido2, CloudKit and every service module — photos, calendar,
contacts, reminders, find-my, notes, invites — before Drive is ever touched, and
those imports cannot be excluded at build time because they are unconditional.
That cost about 30 MB of resident memory in a program that never calls them.

Removed relative to upstream:

* ``fido2`` and the security-key 2FA path (``confirm_security_key``,
  ``fido2_devices``, ``security_key_names``, webauthn assertion handling).
  SMS and trusted-device 2FA still work; hardware security keys do not.
* CloudKit (``pydantic``, ``protobuf``).
* Every service except Drive.

**Maintenance:** upstream fixes are not picked up automatically. When Apple
changes authentication, diff ``base.py``, ``session.py`` and ``hsa2_bridge.py``
against the matching pyicloud release and port the change across.
"""
from icloudlite.base import PyiCloudService

__all__ = ["PyiCloudService"]
