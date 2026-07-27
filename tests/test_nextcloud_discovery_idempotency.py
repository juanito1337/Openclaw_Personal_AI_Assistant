from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_assistant.models import Resource
from personal_assistant.service import _replace_discovered_nextcloud_resources


def resource(resource_id: str, kind: str = "test", marker: str = "old") -> Resource:
    return Resource(
        id=resource_id,
        kind=kind,
        connector="nextcloud" if resource_id.startswith("nextcloud-") else "local",
        permissions=("read",),
        metadata={"marker": marker},
    )


class NextcloudDiscoveryIdempotencyTests(unittest.TestCase):
    def test_discovery_replaces_generated_resources_without_duplicates(self) -> None:
        existing = [
            Resource(id="mail-main", kind="email-account", connector="imap", permissions=("read",), metadata={"marker": "keep"}),
            resource("nextcloud-main", "nextcloud-instance", "old"),
            resource("nextcloud-files-main", "file-root", "old"),
            resource("nextcloud-calendar-personal", "calendar", "old"),
            resource("nextcloud-addressbook-contacts", "addressbook", "old"),
        ]
        discovered = [
            resource("nextcloud-main", "nextcloud-instance", "new"),
            resource("nextcloud-files-main", "file-root", "new"),
            resource("nextcloud-calendar-personal", "calendar", "new"),
            resource("nextcloud-addressbook-contacts", "addressbook", "new"),
        ]

        first = _replace_discovered_nextcloud_resources(
            existing,
            discovered,
            instance_resource_id="nextcloud-main",
        )
        second = _replace_discovered_nextcloud_resources(
            first,
            discovered,
            instance_resource_id="nextcloud-main",
        )

        ids = [item.id for item in second]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count("nextcloud-main"), 1)
        self.assertEqual(ids.count("nextcloud-files-main"), 1)
        self.assertEqual(
            next(item for item in second if item.id == "nextcloud-main").metadata["marker"],
            "new",
        )
        self.assertTrue(any(item.id == "mail-main" for item in second))


if __name__ == "__main__":
    unittest.main(verbosity=2)
