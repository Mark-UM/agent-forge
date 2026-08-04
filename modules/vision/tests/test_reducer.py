from __future__ import annotations

import pytest

from modules.vision.reducer import (
    VisionBatchResult,
    VisionReductionError,
    VisionTable,
    reduce_batches,
)


def test_batches_are_sorted_and_page_sources_are_preserved() -> None:
    result = reduce_batches(
        [
            VisionBatchResult(3, 4, "Third section"),
            VisionBatchResult(1, 2, "First section"),
        ],
        expected_page_count=4,
    )

    assert result.success is True
    assert result.degraded is False
    assert [section.page_start for section in result.sections] == [1, 3]
    assert result.text.index("pages 1-2") < result.text.index("pages 3-4")
    assert result.missing_pages == ()


def test_repeated_header_and_footer_are_removed() -> None:
    result = reduce_batches(
        [
            VisionBatchResult(
                1,
                2,
                "Company Confidential\n\nAlpha body content unique to pages one and two.\n\nPage Footer",
            ),
            VisionBatchResult(
                3,
                4,
                "Company Confidential\n\nBeta body content unique to pages three and four.\n\nPage Footer",
            ),
        ]
    )

    assert "Company Confidential" not in result.text
    assert "Page Footer" not in result.text
    assert "Alpha body" in result.text
    assert "Beta body" in result.text


def test_substantive_duplicate_paragraph_is_kept_once() -> None:
    duplicate = (
        "This substantive paragraph is repeated at the boundary of two OCR "
        "batches and should appear only once in the final document."
    )
    result = reduce_batches(
        [
            VisionBatchResult(1, 2, f"First paragraph.\n\n{duplicate}"),
            VisionBatchResult(3, 4, f"{duplicate}\n\nFinal paragraph."),
        ]
    )

    assert result.text.count("This substantive paragraph") == 1
    assert "First paragraph" in result.text
    assert "Final paragraph" in result.text


def test_failed_batch_and_missing_pages_are_explicitly_degraded() -> None:
    result = reduce_batches(
        [
            VisionBatchResult(1, 2, "Recognized pages one and two."),
            VisionBatchResult(
                3,
                4,
                "",
                success=False,
                error="provider timeout",
            ),
            VisionBatchResult(5, 5, "Recognized page five."),
        ],
        expected_page_count=5,
    )

    assert result.success is True
    assert result.degraded is True
    assert result.successful_batch_count == 2
    assert result.missing_pages == (3, 4)
    assert any("provider timeout" in warning for warning in result.warnings)
    assert any("missing Vision pages" in warning for warning in result.warnings)


def test_overlap_is_reported_without_dropping_sections() -> None:
    result = reduce_batches(
        [
            VisionBatchResult(1, 3, "First batch body."),
            VisionBatchResult(3, 5, "Second batch body."),
        ]
    )

    assert result.degraded is True
    assert len(result.sections) == 2
    assert any("overlapping" in warning for warning in result.warnings)


def test_headings_are_deduplicated_case_insensitively() -> None:
    result = reduce_batches(
        [
            VisionBatchResult(1, 1, "A", headings=("Introduction", "Methods")),
            VisionBatchResult(2, 2, "B", headings=("introduction", "Results")),
        ]
    )
    assert result.headings == ("Introduction", "Methods", "Results")


def test_tables_preserve_page_provenance() -> None:
    table = VisionTable(
        page_start=2,
        page_end=2,
        title="Metrics",
        rows=(("Name", "Value"), ("Accuracy", "95%")),
    )
    result = reduce_batches(
        [VisionBatchResult(1, 2, "Body", tables=(table,))]
    )

    assert result.tables == (table,)
    assert result.to_dict()["tables"][0]["page_start"] == 2
    assert result.to_dict()["tables"][0]["rows"][1] == ["Accuracy", "95%"]


def test_mapping_input_is_supported() -> None:
    result = reduce_batches(
        [
            {
                "page_start": 1,
                "page_end": 1,
                "text": "Mapped batch",
                "tables": [
                    {
                        "title": "Mapped table",
                        "rows": [["A", "B"]],
                    }
                ],
            }
        ]
    )
    assert "Mapped batch" in result.text
    assert result.tables[0].title == "Mapped table"
    assert result.tables[0].page_start == 1


@pytest.mark.parametrize(
    "batch",
    [
        VisionBatchResult(0, 1, "bad"),
        VisionBatchResult(3, 2, "bad"),
    ],
)
def test_invalid_page_ranges_are_rejected(batch: VisionBatchResult) -> None:
    with pytest.raises(VisionReductionError, match="invalid page range"):
        reduce_batches([batch])


def test_empty_input_is_rejected() -> None:
    with pytest.raises(VisionReductionError, match="at least one"):
        reduce_batches([])
