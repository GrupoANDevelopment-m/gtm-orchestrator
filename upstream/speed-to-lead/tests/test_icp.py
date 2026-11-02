"""ICP similarity index (FAISS + embeddings). Skipped without the `infra` extra."""

import pytest

pytest.importorskip("sentence_transformers")
pytest.importorskip("faiss")


def test_icp_scores_on_profile_above_off_profile() -> None:
    from speed_to_lead.services.icp import IcpIndex

    idx = IcpIndex.build()
    on_profile = idx.similarity("200-person SaaS company evaluating tools for our sales team")
    off_profile = idx.similarity("buy cheap crypto and casino offers, guaranteed returns")
    assert 0.0 <= off_profile <= on_profile <= 1.0
    assert on_profile > off_profile
