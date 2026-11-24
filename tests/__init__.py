"""Test package. Most tests publish an APPROVED handoff at revision 1 and are not about the VT design review
(D33), so they run with the review off. The design review tests set config.json "design_review": true."""
from harness.store import Store

Store.DESIGN_REVIEW_DEFAULT = False
