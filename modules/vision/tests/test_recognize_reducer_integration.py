from __future__ import annotations

from pathlib import Path

from modules.vision import recognize


def test_multi_batch_recognition_uses_page_aware_reducer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    images = []
    for index in range(2):
        path = tmp_path / f"page-{index + 1}.png"
        path.write_bytes(b"small-image")
        images.append(str(path))

    duplicate = (
        "This substantive boundary paragraph should survive exactly once in "
        "the reduced document output."
    )
    responses = iter(
        [
            f"First section.\n\n{duplicate}",
            f"{duplicate}\n\nSecond section.",
        ]
    )

    def fake_batch(paths, prompt, api_key):
        return next(responses), {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setattr(recognize, "PDF_BATCH_SIZE", 1)
    monkeypatch.setattr(recognize, "_call_vision_api_batch", fake_batch)

    text, usage = recognize._call_vision_api(images, "recognize")

    assert "<!-- pages 1-1 -->" in text
    assert "<!-- pages 2-2 -->" in text
    assert text.count("This substantive boundary paragraph") == 1
    assert "First section." in text
    assert "Second section." in text
    assert usage == {"prompt_tokens": 2, "completion_tokens": 2}


def test_single_batch_keeps_legacy_plain_text_format(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image = tmp_path / "single.png"
    image.write_bytes(b"small-image")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setattr(
        recognize,
        "_call_vision_api_batch",
        lambda *args: ("single result", {}),
    )

    text, _ = recognize._call_vision_api([str(image)], "recognize")
    assert text == "single result"
