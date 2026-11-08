"""Deprecated alias of ``enablebanking_psd2`` (removed in 0.3): ``psd2demo.x`` is the same module object as
``enablebanking_psd2.x``, so monkeypatching through either name is visible through both."""

import importlib
import importlib.abc
import importlib.util
import sys
import warnings

warnings.warn("psd2demo is renamed to enablebanking_psd2; the old name is removed in 0.3",
              DeprecationWarning, stacklevel=2)

_OLD, _NEW = "psd2demo", "enablebanking_psd2"


class _AliasLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return importlib.import_module(_NEW + spec.name[len(_OLD):])

    def exec_module(self, module):  # already executed under its real name
        pass


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(_OLD + "."):
            real = importlib.import_module(_NEW + fullname[len(_OLD):])
            return importlib.util.spec_from_loader(fullname, _AliasLoader(), is_package=hasattr(real, "__path__"))
        return None


if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())
sys.modules[_OLD] = importlib.import_module(_NEW)
