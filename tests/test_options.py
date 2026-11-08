import pytest

from app.options import Options, OptionsError


def test_defaults_and_round_trip():
    options = Options.from_dict({})
    assert options.resize == "none" and options.format == "original" and options.quality == 82
    assert not options.watermark.enabled
    assert Options.from_dict(options.to_dict()) == options


def test_parses_and_normalises_values():
    options = Options.from_json(
        '{"format": "JPG", "quality": "70", "resize": "fit", "width": 800, "background": "#FFFFFF",'
        ' "remove_background": "true", "suffix": "-web/../x", "watermark": {"text": "  © Me \\n 2026 "}}'
    )
    assert options.format == "jpeg" and options.quality == 70 and options.width == 800
    assert options.background == "#ffffff" and options.remove_background is True
    assert options.suffix == "-web..x"  # path characters stripped
    assert options.watermark.text == "© Me 2026" and options.watermark.enabled


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"resize": "fit"}, "width or a height"),
        ({"resize": "cover", "width": 100}, "both a width and a height"),
        ({"resize": "stretch"}, "'resize' must be one of"),
        ({"format": "gif"}, "'format' must be one of"),
        ({"quality": 0}, "Quality must be between 1 and 100"),
        ({"width": "abc"}, "Width must be a number"),
        ({"percent": 5000, "resize": "percent"}, "Scale must be between"),
        ({"background": "red"}, "colour like #ffffff"),
        ({"watermark": {"text": "x" * 121}}, "at most 120"),
        ({"watermark": {"opacity": 2}}, "Watermark opacity"),
        ({"watermark": "nope"}, "must be an object"),
        ([1, 2], "JSON object"),
    ],
)
def test_rejects_bad_options(raw, message):
    with pytest.raises(OptionsError, match=message):
        Options.from_dict(raw)


def test_invalid_json():
    with pytest.raises(OptionsError, match="valid JSON"):
        Options.from_json("{not json")
