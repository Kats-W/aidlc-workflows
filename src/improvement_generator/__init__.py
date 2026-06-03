"""Self-Improvement Pipeline (U-06).

Weekly closed-loop quality improvement for the au Jibun Bank AI Agent:

  - :mod:`contact_lens_analyzer` — detect low-quality contacts from Amazon
    Connect Contact Lens (US-3.1).
  - :mod:`gap_analyzer` — classify knowledge gaps from PII-masked conversation
    summaries via Claude (US-3.2).
  - :mod:`suggestion_generator` — generate up to 10 prioritised website / FAQ
    improvement suggestions, skipping pending duplicates (US-3.3).
"""
