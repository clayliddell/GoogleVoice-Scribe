from __future__ import annotations

import json
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = REPO_ROOT / "extension"


def test_extension_manifest_icons_exist_with_expected_sizes():
    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))
    icon_sets = [manifest["icons"], manifest["action"]["default_icon"]]

    for icons in icon_sets:
        for size, relative_path in icons.items():
            icon_path = EXTENSION_ROOT / relative_path
            assert icon_path.is_file()
            with Image.open(icon_path) as image:
                assert image.size == (int(size), int(size))
