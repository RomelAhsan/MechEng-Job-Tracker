"""Resume Studio (SPEC Phase 4): master-resume parsing, tailored generation
with strict fact preservation, and ATS-friendly export.

The package is split into pure-function modules so the service and dashboard
layers stay thin:

* :mod:`jobtracker.resume.parser` - bytes in, structured sections out.
* :mod:`jobtracker.resume.generator` - reorder/emphasize existing facts for a
  job and verify every generated claim is grounded in the master resume.
* :mod:`jobtracker.resume.export` - render Markdown, JSON, and an ATS PDF.
"""

from __future__ import annotations
