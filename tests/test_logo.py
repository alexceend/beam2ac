from src.ac.logo import default_logo_png, png_size


def test_default_logo_sizes():
    assert png_size(default_logo_png(128)) == (128, 128)
    assert png_size(default_logo_png(64)) == (64, 64)
    assert png_size(b"no es png") is None
