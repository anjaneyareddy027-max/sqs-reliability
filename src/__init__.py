"""Source package for the SQS reliability / recovery artefact.

Small path shim so the same imports work in two places:

* inside Lambda the code root is ``src/`` itself (SAM ``CodeUri: src/``), so
  modules import each other as ``common.faults``, ``common.models`` ...
* locally we run things as ``python -m src.control.experiment_runner`` from the
  project root, where ``common`` would normally not be importable.

Putting this folder on ``sys.path`` when the ``src`` package is imported means
we never need two copies of the import lines.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
