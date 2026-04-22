"""Text-normalisation helpers.

:func:`normalize_alias` produces the key under which
:class:`app.models.job.JobAlias` rows are globally unique. The
natural-language expense parser (Phase 2) normalises candidate job
tokens through this same function before looking them up, so the
normalisation choices here define what counts as "the same alias" for
matching purposes.
"""

from __future__ import annotations

import re
import unicodedata

# Punctuation + whitespace stripped after NFKC + casefold. Any character
# in this class is removed entirely (not replaced with a space) so that
# ``site 1`` / ``site-1`` / ``site.1`` all collapse to ``site1``.
_PUNCT_AND_WHITESPACE = re.compile(
    r"[\s_\-\.\,\/\\\(\)\[\]\{\}\|\:\;\!\?\'\"]+"
)


def normalize_alias(text: str) -> str:
    """Return the normalised form of ``text`` used as the alias uniqueness key.

    Pipeline (in order):

    1. Unicode NFKC normalize — folds full-width ``１２３`` / ``Ｓｉｔｅ`` to
       half-width ``123`` / ``Site``.
    2. Casefold — Unicode-aware lowercase.
    3. Strip leading/trailing whitespace.
    4. Remove all punctuation + whitespace — so ``site 1``, ``site1``,
       ``SITE-1``, and ``Ｓｉｔｅ１`` all collapse to ``site1``.
    """
    # 1. NFKC
    s = unicodedata.normalize("NFKC", text)
    # 2. casefold (locale-unaware, Unicode-aware lowercase)
    s = s.casefold()
    # 3. strip outer whitespace
    s = s.strip()
    # 4. drop all punctuation + interior whitespace
    return _PUNCT_AND_WHITESPACE.sub("", s)
