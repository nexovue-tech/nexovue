"""One hop of imports, by plain line parsing (F5 plan T3.18; spec FR12a, AC79).

Each language's fixtures resolve to the stated files in a fixture tree; a wildcard, a
library and an unknown language add nothing; a path climbing out of the folder is never
returned, since only the tree given (the folder's own listing) can be named.
"""

from __future__ import annotations

from typing import Final

import pytest
from nexovue_platform.fixes.imports import go_module_of, imported_paths
from nexovue_platform.fixes.matching import TreeIndex

TREE: Final = TreeIndex(
    [
        "acme/__init__.py",
        "acme/util.py",
        "shop/orders/handler.py",
        "shop/orders/money.py",
        "shop/billing/__init__.py",
        "shop/billing/tax.py",
        "shop/orders/views.py",
        "web/src/pay.ts",
        "web/src/a.ts",
        "web/b.js",
        "web/src/c.tsx",
        "web/src/lib/index.ts",
        "cmd/main.go",
        "internal/store/a.go",
        "internal/store/b.go",
        "internal/store/c.go",
        "internal/store/d.go",
        "internal/store/a_test.go",
        "src/main/java/com/acme/util/Money.java",
        "src/main/kotlin/com/acme/util/Rate.kt",
        "src/main/java/com/acme/Handler.java",
    ]
)


@pytest.mark.parametrize(
    ("path", "text", "expected"),
    [
        ("shop/orders/handler.py", "import acme.util\n", ["acme/util.py"]),
        ("shop/orders/handler.py", "from . import money\n", ["shop/orders/money.py"]),
        ("shop/orders/handler.py", "from ..billing import tax\n", ["shop/billing/tax.py"]),
        (
            "shop/orders/handler.py",
            "from . import (\n    money,\n    views,  # the page\n)\n",
            ["shop/orders/money.py", "shop/orders/views.py"],
        ),
        ("web/src/pay.ts", "import x from './a'\n", ["web/src/a.ts"]),
        ("web/src/pay.ts", "const b = require('../b')\n", ["web/b.js"]),
        ("web/src/pay.ts", "export * from './c'\n", ["web/src/c.tsx"]),
        ("web/src/pay.ts", "import { y } from './lib'\n", ["web/src/lib/index.ts"]),
        (
            "cmd/main.go",
            'import (\n\t"fmt"\n\tstore "example.com/shop/internal/store"\n)\n',
            ["internal/store/a.go", "internal/store/b.go", "internal/store/c.go"],
        ),
        (
            "src/main/java/com/acme/Handler.java",
            "import com.acme.util.Money;\n",
            ["src/main/java/com/acme/util/Money.java"],
        ),
        (
            "src/main/java/com/acme/Handler.java",
            "import com.acme.util.Rate\n",
            ["src/main/kotlin/com/acme/util/Rate.kt"],
        ),
    ],
)
def test_ac79_each_language_s_imports_resolve_inside_the_tree(
    path: str, text: str, expected: list[str]
) -> None:
    assert imported_paths(path, text, TREE, go_module="example.com/shop") == expected


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("shop/orders/handler.py", "import requests\nfrom flask import Flask\n"),
        ("web/src/pay.ts", "import lodash from 'lodash'\n"),
        ("cmd/main.go", 'import "fmt"\n'),
        ("src/main/java/com/acme/Handler.java", "import java.util.List;\n"),
        ("src/main/java/com/acme/Handler.java", "import com.acme.util.*;\n"),
        ("src/main/java/com/acme/Handler.java", "import static com.acme.util.Money.ZERO;\n"),
        ("shop/notes.rb", "require_relative 'acme/util'\n"),
    ],
)
def test_ac79_a_library_a_wildcard_and_an_unknown_language_add_nothing(
    path: str, text: str
) -> None:
    assert imported_paths(path, text, TREE, go_module="example.com/shop") == []


def test_ac80_an_import_climbing_out_of_the_folder_is_never_returned() -> None:
    assert imported_paths("web/src/pay.ts", "import s from '../../../shared/x'\n", TREE) == []
    assert imported_paths("acme/util.py", "from ...shared import x\n", TREE) == []


def test_go_imports_need_the_folder_s_module_path() -> None:
    text = 'import "example.com/shop/internal/store"\n'

    assert imported_paths("cmd/main.go", text, TREE, go_module=None) == []
    assert go_module_of("module example.com/shop\n\ngo 1.24\n") == "example.com/shop"


def test_a_test_file_is_never_an_import_of_a_go_package() -> None:
    found = imported_paths(
        "cmd/main.go",
        'import "example.com/shop/internal/store"\n',
        TREE,
        go_module="example.com/shop",
    )

    assert "internal/store/a_test.go" not in found
