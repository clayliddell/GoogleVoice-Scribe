from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_readme_script_references_exist():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    referenced_scripts = sorted(set(re.findall(r"\.\\scripts\\([A-Za-z0-9_.-]+\.py)", readme)))

    assert referenced_scripts
    for script_name in referenced_scripts:
        assert (REPO_ROOT / "scripts" / script_name).is_file()
