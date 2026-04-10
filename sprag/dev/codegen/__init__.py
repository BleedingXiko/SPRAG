"""Python -> JavaScript codegen for SPRAG.

This package compiles SPRAG ``Component`` and ``Module`` Python classes
into the equivalent Ragot ESM sources, and emits the surrounding browser
entry point and runtime that the SPRAG compiler ships into ``dist/``.

The package is split along functional seams so each phase of work
(rendering primitives, state bridge, Specter bridge, etc.) can land in a
focused file rather than a single monolith:

  - ``mappings``     - name / operator translation tables + JSCodegenError
  - ``imports``      - regex-based detection of optional Ragot imports
  - ``expressions``  - ``_compile_expr`` + ui./dom. namespace dispatchers
  - ``statements``   - block-level statement compilation (if/for/try/etc)
  - ``modules``      - ``compile_module_class`` + setup/decorator wrappers
  - ``components``   - ``compile_component_class`` + lifecycle merge
  - ``emit``         - high-level emitters used by the SPRAG build pipeline

Public API: only the names re-exported here are considered stable. Other
modules in the package are implementation details.
"""

from .emit import (
    build_browser_entry,
    emit_generated_files,
    emit_ragot_runtime,
    emit_stores_shim,
)
from .components import compile_component_class
from .modules import compile_module_class
from .mappings import JSCodegenError

__all__ = [
    "build_browser_entry",
    "emit_generated_files",
    "emit_ragot_runtime",
    "emit_stores_shim",
    "compile_component_class",
    "compile_module_class",
    "JSCodegenError",
]
