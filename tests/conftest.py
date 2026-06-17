from __future__ import annotations

import sys
import types


def _install_test_dependency_stubs() -> None:
    rouge_module = types.ModuleType("rouge")
    setattr(rouge_module, "Rouge", type("Rouge", (), {}))

    ascii_colors_module = types.ModuleType("ascii_colors")
    setattr(
        ascii_colors_module,
        "ASCIIColors",
        type(
            "ASCIIColors",
            (),
            {
                "info": staticmethod(lambda *args, **kwargs: None),
                "yellow": staticmethod(lambda *args, **kwargs: None),
                "warning": staticmethod(lambda *args, **kwargs: None),
                "error": staticmethod(lambda *args, **kwargs: None),
            },
        ),
    )
    setattr(ascii_colors_module, "trace_exception", lambda *args, **kwargs: None)

    pipmaster_module = types.ModuleType("pipmaster")
    setattr(pipmaster_module, "is_installed", lambda *args, **kwargs: True)
    setattr(pipmaster_module, "install", lambda *args, **kwargs: None)

    neo4j_module = types.ModuleType("neo4j")
    setattr(neo4j_module, "AsyncGraphDatabase", object)

    dotenv_module = types.ModuleType("dotenv")
    setattr(dotenv_module, "load_dotenv", lambda *args, **kwargs: None)

    sys.modules.setdefault("rouge", rouge_module)
    sys.modules.setdefault("ascii_colors", ascii_colors_module)
    sys.modules.setdefault("pipmaster", pipmaster_module)
    sys.modules.setdefault("neo4j", neo4j_module)
    sys.modules.setdefault("dotenv", dotenv_module)


_install_test_dependency_stubs()
