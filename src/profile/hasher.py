"""IdentityHasher — deterministic au ID -> customer id hashing (US-5.2).

The au ID is a personal identifier and must never be persisted or logged in
plaintext. :class:`IdentityHasher` produces a stable SHA-256 hex digest that is
used downstream as the ``customerId`` partition key in the CustomerHistory
table. The transform is pure and deterministic so the same au ID always maps to
the same ``customerId`` across invocations.
"""

from __future__ import annotations

import hashlib

from src.common.errors import ValidationError


class IdentityHasher:
    """Hash an au ID into a stable, non-reversible customer id.

    The hashing is intentionally deterministic (plain SHA-256, no salt) so the
    derived ``customerId`` is consistent across Lambdas and over time. Callers
    MUST treat the input as sensitive: it is never logged here.
    """

    @staticmethod
    def hash_au_id(au_id: str) -> str:
        """Return the SHA-256 hex digest of ``au_id``.

        Args:
            au_id: The raw au ID. Must be a non-empty, non-whitespace string.

        Returns:
            A 64-character lowercase hexadecimal digest.

        Raises:
            ValidationError: If ``au_id`` is empty or whitespace-only.
        """
        if not au_id or not au_id.strip():
            # NEVER include the plaintext au_id in the error message or logs.
            raise ValidationError("au_id must not be empty")
        return hashlib.sha256(au_id.encode("utf-8")).hexdigest()
