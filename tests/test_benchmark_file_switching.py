"""Prevent a wrong page/document from being reported as successful paint."""

from tools.benchmark_file_switching import matching_raster, summary


def test_raster_evidence_requires_path_generation_target_page_and_request_time():
    paints = [("A.pdf", 7, 5, 1.1), ("B.pdf", 8, 5, 1.2),
              ("A.pdf", 8, 0, 1.3), ("A.pdf", 8, 5, 0.9)]
    assert not matching_raster(paints, "A.pdf", 8, 5, 1.0)
    paints.append(("A.pdf", 8, 5, 1.4))
    assert matching_raster(paints, "A.pdf", 8, 5, 1.0)


def test_summary_uses_nearest_rank_p95_and_keeps_empty_samples_explicit():
    assert summary([]) == {"n": 0}
    stats = summary(list(range(1, 31)))
    assert stats == {"n": 30, "p50_ms": 15.5, "p95_ms": 29, "max_ms": 30}
