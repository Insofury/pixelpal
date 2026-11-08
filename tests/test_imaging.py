import io

import pytest
from PIL import Image

from app.imaging import ImageError, encode, has_alpha, load_image, resize, target_size
from app.options import Options
from app.pipeline import output_name, process
from tests.helpers import make_image, open_bytes


# ------------------------------------------------------------------ loading
@pytest.mark.parametrize(
    ("data", "message", "status"),
    [
        (b"", "empty", 400),
        (b"definitely not an image", "isn't an image", 400),
        (make_image((300, 300), quality=95)[:1500], "damaged", 400),  # header intact, data cut off
    ],
)
def test_load_rejects_bad_files(data, message, status):
    with pytest.raises(ImageError, match=message) as info:
        load_image(data)
    assert info.value.status == status


def test_load_rejects_too_many_pixels():
    with pytest.raises(ImageError, match="megapixel") as info:
        load_image(make_image((2000, 1000), "PNG"), max_pixels=1_000_000)
    assert info.value.status == 413


def test_exif_rotation_is_applied():
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90° clockwise", as phones store portrait photos
    loaded = load_image(make_image((400, 200), exif=exif.tobytes()))
    assert (loaded.width, loaded.height) == (200, 400)


def test_modes_are_normalised():
    palette = Image.new("P", (20, 20))
    palette.info["transparency"] = 0
    buf = io.BytesIO()
    palette.save(buf, "PNG", transparency=0)
    assert load_image(buf.getvalue()).image.mode == "RGBA"

    cmyk = load_image(make_image((40, 40), "JPEG", mode="CMYK", color=(0, 50, 100, 0)))
    assert cmyk.image.mode == "RGB" and cmyk.icc_profile is None

    deep = Image.new("I;16", (30, 30), 40000)
    buf = io.BytesIO()
    deep.save(buf, "PNG")
    assert load_image(buf.getvalue()).image.mode == "RGB"


def test_gif_first_frame():
    frames = [Image.new("RGB", (30, 20), c) for c in ((255, 0, 0), (0, 255, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    loaded = load_image(buf.getvalue())
    assert loaded.format == "gif" and loaded.image.getpixel((0, 0))[:3] == (255, 0, 0)


# ------------------------------------------------------------------ resizing
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"resize": "fit", "width": 200, "height": 200}, (200, 150)),
        ({"resize": "fit", "height": 60}, (80, 60)),
        ({"resize": "fit", "width": 1000}, (400, 300)),  # never enlarges by default
        ({"resize": "fit", "width": 800, "upscale": True}, (800, 600)),
        ({"resize": "percent", "percent": 25}, (100, 75)),
        ({"resize": "cover", "width": 100, "height": 100}, (100, 100)),
        ({}, (400, 300)),
    ],
)
def test_target_size(options, expected):
    assert target_size((400, 300), Options.from_dict(options)) == expected


def test_cover_crops_the_centre():
    img = load_image(make_image((400, 200), "PNG")).image
    out = resize(img, Options.from_dict({"resize": "cover", "width": 100, "height": 100}))
    assert out.size == (100, 100)
    assert out.getpixel((50, 50))[0] > 180  # the red subject in the middle survived the crop


def test_resize_limit():
    img = load_image(make_image((400, 300), "PNG")).image
    with pytest.raises(ImageError, match="over the"):
        resize(img, Options.from_dict({"resize": "percent", "percent": 1000}), max_pixels=1_000_000)


# ------------------------------------------------------------------ formats and compression
def test_convert_formats():
    data = make_image((300, 200), "PNG")
    for fmt, magic in (("jpeg", b"\xff\xd8"), ("webp", b"RIFF"), ("png", b"\x89PNG")):
        result = process(data, "pic.png", Options.from_dict({"format": fmt}))
        assert result.data.startswith(magic) and result.format == fmt
    assert process(data, "pic.png", Options.from_dict({"format": "webp"})).filename == "pic.webp"


def test_original_format_kept_and_others_become_png():
    assert process(make_image(), "a.jpg", Options()).format == "jpeg"
    assert process(make_image(fmt="BMP"), "a.bmp", Options()).format == "png"


def test_target_size_lowers_quality():
    data = make_image((1200, 900), "PNG")
    loose = process(data, "a.png", Options.from_dict({"format": "jpeg", "quality": 95}))
    target_kb = max(4, len(loose.data) // 1024 // 2)
    tight = process(data, "a.png", Options.from_dict({"format": "jpeg", "quality": 95, "target_kb": target_kb}))
    assert len(tight.data) <= target_kb * 1024 and tight.quality < 95


def test_impossible_target_adds_a_note():
    result = process(make_image((600, 400)), "a.jpg", Options.from_dict({"format": "jpeg", "target_kb": 1}))
    assert result.quality == 5 and any("lowest quality" in n for n in result.notes)


def test_png_palette_is_smaller():
    photo = make_image((600, 400), "PNG")
    plain = process(photo, "a.png", Options.from_dict({"format": "png"}))
    small = process(photo, "a.png", Options.from_dict({"format": "png", "png_colors": True}))
    assert len(small.data) < len(plain.data) and open_bytes(small.data).mode == "P"


def test_jpeg_flattens_transparency_with_a_note():
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    data, quality, notes = encode(img, "jpeg", Options())
    assert open_bytes(data).getpixel((10, 10)) == (255, 255, 255) and notes and quality == 82


def test_metadata_is_stripped_unless_kept():
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    exif[0x0112] = 6
    data = make_image((80, 40), exif=exif.tobytes())
    stripped = open_bytes(process(data, "a.jpg", Options()).data)
    assert not stripped.getexif()
    kept = open_bytes(process(data, "a.jpg", Options.from_dict({"keep_metadata": True})).data).getexif()
    assert kept[0x010F] == "PhoneMaker" and kept[0x0112] == 1  # rotation already applied to the pixels


def test_result_info_and_names():
    result = process(make_image((400, 300), "PNG"), "My Photo.png", Options.from_dict({"format": "jpeg", "suffix": "-small"}))
    info = result.info()
    assert info["filename"] == "My Photo-small.jpg" and info["bytes"] == len(result.data)
    assert info["original"] == {"width": 400, "height": 300, "bytes": info["original"]["bytes"], "format": "png"}
    assert output_name("../../etc/passwd", "", "png") == "passwd.png"
    assert output_name('bad:"name?.jpg', "", "png") == "bad_name.png"
    assert output_name("", "", "png") == "image.png"


def test_has_alpha():
    assert not has_alpha(Image.new("RGBA", (5, 5), (1, 2, 3, 255)))
    assert has_alpha(Image.new("RGBA", (5, 5), (1, 2, 3, 0)))
