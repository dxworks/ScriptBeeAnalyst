"""RelationBuilder implementations.

Importing this package side-loads every implementation module, which in turn
auto-registers each :class:`RelationBuilder` with the module-level
:data:`src.enrichment.relations.BUILDERS` singleton via the
``@BUILDERS.register`` decorator.

Import order matters for one pair only: ``issue_file`` MUST register
before ``issue_issue`` because the latter reads back
``relations.of_kind("issue_file")`` from the pipeline output. The
dict-backed ``BUILDERS`` preserves insertion order (CPython 3.7+
guarantee), and ``run_pipeline`` iterates ``list(BUILDERS)`` once at
start — so this single ordering constraint is held by the import order
below. Every other builder's registration is independent.

See the Chunk-7 handoff for the legacy-file → new-class mapping table.
"""
from __future__ import annotations

# Cross-source linkers (subsume ProjectLinker). ``issue_file`` before
# ``issue_issue`` per the ordering note in the module docstring.
from . import (  # noqa: F401  side-effect imports
    issue_file,
    issue_issue,
    pr_file,
    pr_issue,
    pr_reviewer,
)

# Git-domain builders.
from . import (  # noqa: F401
    coauthor,
    cochange,
    ownership,
)

# Code-structure builders.
from . import (  # noqa: F401
    calls,
    coupling,
    data_access,
    hierarchy,
)

# Duplication builders.
from . import (  # noqa: F401
    duplication_external,
    duplication_internal_summary,
    duplication_sibling,
)

# Similarity builders — substantively ported.
from . import similarity_file_names  # noqa: F401

# File-domain cochange variants (Chunk 13). MUST register BEFORE the
# component-domain cochange variants below: ``cochange.component*`` builders
# aggregate the file-* relations emitted earlier in the same pipeline pass
# (the pipeline writes builder output to ``host.relations`` between
# builders so intra-stage reads work — but only when the producer
# registered first). The author-domain variants are NOT order-sensitive
# (they walk commits directly via the TemporalIndex), but kept grouped
# with file/component cochange for cohesion.
from . import (  # noqa: F401
    cochange_file_shared_devs,
    cochange_file_shared_task_prefixes,
    cochange_file_time_windowed,
    cochange_author_shared_task_prefixes,
    cochange_author_time_windowed,
    cochange_component,
    cochange_component_shared_devs,
    cochange_component_shared_task_prefixes,
    cochange_component_time_windowed,
)


__all__: list[str] = []
