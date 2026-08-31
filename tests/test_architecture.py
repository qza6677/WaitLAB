import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _imported_modules(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


@pytest.mark.parametrize(
    "module_name, forbidden",
    (
        ("waitlab/ui.py", {"waitlab.storage"}),
        ("waitlab/ui_dialogs.py", {"waitlab.storage"}),
        ("waitlab/ui_primitives.py", {"waitlab.storage", "waitlab.service"}),
        ("waitlab/ui_widgets.py", {"waitlab.storage", "waitlab.service"}),
        ("waitlab/ui_charts.py", {"waitlab.storage", "waitlab.service"}),
        ("waitlab/ui_styles.py", {"waitlab.storage", "waitlab.service"}),
        ("waitlab/storage_defaults.py", {"waitlab.ui", "PySide6"}),
        ("waitlab/storage_schema.py", {"waitlab.ui", "PySide6"}),
        ("waitlab/storage_stats.py", {"waitlab.ui", "PySide6"}),
        ("waitlab/storage_tasks.py", {"waitlab.ui", "PySide6"}),
        ("waitlab/storage_focus.py", {"waitlab.ui", "PySide6"}),
        ("waitlab/storage_ai.py", {"waitlab.ui", "PySide6"}),
        ("waitlab/service_policy.py", {"waitlab.storage", "PySide6"}),
        ("waitlab/service_focus.py", {"waitlab.ui", "PySide6"}),
        ("waitlab/service_ai.py", {"waitlab.ui", "PySide6"}),
    ),
)
def test_leaf_modules_keep_dependency_boundaries(module_name: str, forbidden: set[str]):
    imported = _imported_modules(ROOT / module_name)

    assert imported.isdisjoint(forbidden)
