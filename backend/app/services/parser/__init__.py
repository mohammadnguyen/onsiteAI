"""Phase 2 parser package.

Public API: :func:`parse` takes natural-language expense text and
returns a :class:`ParseResult`. See :mod:`app.services.parser.orchestrator`
for the orchestration steps and :mod:`app.services.parser.llm_adapter`
for the frozen ParsePartial mutation contract.
"""

from app.services.parser.llm_adapter import LLMParser, MockLLMParser, ParsePartial
from app.services.parser.orchestrator import ParseResult, parse

__all__ = ["LLMParser", "MockLLMParser", "ParsePartial", "ParseResult", "parse"]
