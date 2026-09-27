"""
Architecture tests — the layering is enforced, not just documented.

`README.md` claims the packages form inward-pointing layers: `core/` depends on
nothing internal, and no layer imports from a layer below it. A claim like that in a
README decays on the first commit that ignores it, and nothing fails. These tests
are the enforcement, so the structure has to be violated *deliberately* — by editing
the table below with a reason — rather than by accident.

Read by AST rather than by importing, so a cycle or a missing dependency shows up as
a failed assertion naming the file and the import, not as an ImportError during
collection.

The one deliberate exception is recorded as its own test: `validation/` reaches
`web.db` to persist a run, which points *upward*. That is allowed only as a
function-local import, and `test_validation_reaches_web_only_lazily` is what keeps
it from quietly becoming a module-level dependency.
"""

import ast
import pathlib
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Internal top-level packages, in dependency order. A package may import from the
# packages listed as its allowed set and from stdlib/third-party; anything else fails.
_INTERNAL = {"core", "adapters", "pipeline", "datasources", "producers", "validation", "web", "scripts", "tests"}

_ALLOWED: dict[str, set[str]] = {
    # The innermost layer. Nothing internal, which is what makes it depend-on-able.
    "core": set(),
    # Providers implement core.contracts.AgentAdapter and know nothing else.
    "adapters": {"core"},
    # The validation loop and tracing operate on core types only.
    "pipeline": {"core"},
    # Network egress: needs the JsonFetcher contract and nothing more.
    "datasources": {"core"},
    # A producer is a lens over a data source, run through the pipeline.
    "producers": {"core", "pipeline", "datasources"},
    # The verification stack scores and compares; it persists via a lazy web.db
    # import, asserted separately below.
    "validation": {"core", "pipeline", "web"},
    # The delivery layer may use everything.
    "web": {"core", "adapters", "pipeline", "datasources", "producers", "validation", "web"},
    # Entry points wire the whole thing together.
    "scripts": _INTERNAL,
    "tests": _INTERNAL,
}


def _internal_imports(path: pathlib.Path):
    """Yield (package, lineno, statement_text, is_module_level) for each internal import."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_level = set()
    for node in tree.body:
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Import, ast.ImportFrom)) and isinstance(
                node, (ast.Import, ast.ImportFrom)
            ):
                module_level.add(id(sub))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            top = name.split(".")[0]
            if top in _INTERNAL:
                yield top, node.lineno, name, id(node) in module_level


def _source_files(package: str):
    return [
        p
        for p in (_ROOT / package).rglob("*.py")
        if "__pycache__" not in p.parts
    ]


class TestLayering(unittest.TestCase):
    def test_every_package_only_imports_its_allowed_layers(self):
        for package, allowed in _ALLOWED.items():
            for path in _source_files(package):
                rel = path.relative_to(_ROOT).as_posix()
                for imported, lineno, name, _ in _internal_imports(path):
                    if imported == package:
                        continue  # intra-package imports are always fine
                    with self.subTest(file=rel, imports=name):
                        self.assertIn(
                            imported,
                            allowed,
                            f"{rel}:{lineno} imports {name!r}; {package}/ may only "
                            f"import from {sorted(allowed) or '(nothing internal)'}",
                        )

    def test_core_depends_on_nothing_internal_except_itself(self):
        """
        Stated separately because it is the property the whole layout rests on: if
        core/ ever imports outward, every other layer inherits that dependency and
        the arrows stop pointing inward.
        """
        for path in _source_files("core"):
            rel = path.relative_to(_ROOT).as_posix()
            for imported, lineno, name, _ in _internal_imports(path):
                self.assertEqual(
                    imported, "core", f"{rel}:{lineno} — core/ must not import {name!r}"
                )

    def test_validation_reaches_web_only_lazily(self):
        """
        `validation/` persisting through `web.db` is an upward dependency, allowed
        only inside a function body. As a module-level import it would make the
        comparator un-importable without the database layer and create a cycle with
        `web/server.py`, which imports the comparator.
        """
        found_lazy = False
        for path in _source_files("validation"):
            rel = path.relative_to(_ROOT).as_posix()
            for imported, lineno, name, is_module_level in _internal_imports(path):
                if imported != "web":
                    continue
                self.assertFalse(
                    is_module_level,
                    f"{rel}:{lineno} imports {name!r} at module level; keep it "
                    f"function-local so validation/ stays importable without web/",
                )
                found_lazy = True
        self.assertTrue(
            found_lazy,
            "expected at least one lazy web.db import in validation/ — if persistence "
            "moved, update this test rather than deleting it",
        )

    def test_datasources_is_the_only_package_that_opens_a_socket(self):
        """
        P2: only ingest code touches the network. `urllib.request` outside
        `datasources/` (or the provider adapters, which must reach their own APIs) is
        a new, undeclared egress point.
        """
        allowed_prefixes = ("datasources/", "adapters/")
        offenders = []
        for package in ("core", "pipeline", "producers", "validation", "web"):
            for path in _source_files(package):
                rel = path.relative_to(_ROOT).as_posix()
                if rel.startswith(allowed_prefixes):
                    continue
                source = path.read_text(encoding="utf-8")
                if "urllib.request" in source or "import requests" in source:
                    offenders.append(rel)
        # validation/verification.py fetches the source document it verifies against;
        # that is the one known exception and it is named here rather than excused.
        self.assertEqual(
            offenders,
            ["validation/verification.py"],
            "network access outside datasources/ and adapters/ changed — if this is "
            "intentional, name the new egress point here",
        )


class TestNoLooseRootModules(unittest.TestCase):
    """
    The root holds no importable Python except the package marker. Loose root modules
    are what produced the flat `from parser import ...` graph this layout replaced,
    and one of them shadowed a stdlib module name.
    """

    def test_root_has_no_python_modules_other_than_init(self):
        loose = sorted(
            p.name
            for p in _ROOT.glob("*.py")
            if p.name != "__init__.py"
        )
        self.assertEqual(loose, [], f"loose modules at the subsystem root: {loose}")

    def test_every_package_is_importable_as_a_package(self):
        for package in ("core", "adapters", "pipeline", "datasources", "producers", "validation", "web", "scripts"):
            with self.subTest(package=package):
                self.assertTrue(
                    (_ROOT / package / "__init__.py").is_file(),
                    f"{package}/ is missing __init__.py",
                )


if __name__ == "__main__":
    unittest.main()
