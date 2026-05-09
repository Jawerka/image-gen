"""Tests for the newly added ``img2img`` MCP tool.

The real ``img2img`` implementation talks to a Stable Diffusion WebUI
instance via HTTP. In the test suite we replace the network layer with a
light‑weight mock so the tests run offline and deterministically.

The tests cover:
* The private helper ``_resolve_init_image_path`` – it must accept a plain
  filename and a full public URL and reject unsafe inputs.
* Basic validation performed by ``img2img`` (empty prompt, out‑of‑range
  parameters).
* Successful execution path where the mocked WebUI returns a base64 image.
"""

import base64
import json
from pathlib import Path

import pytest
from PIL import Image

# Import the module under test
from app import tools as t


def _create_dummy_image(tmp_path: Path) -> Path:
    """Create a tiny PNG image and return its path."""
    img = Image.new("RGB", (10, 10), color="red")
    img_path = tmp_path / "dummy.png"
    img.save(img_path, "PNG")
    return img_path


def test_resolve_init_image_path_filename(tmp_path: Path):
    """Helper should resolve a bare filename inside ``IMAGE_DIR``.

    The test monkey‑patches ``app.settings.IMAGE_DIR`` to a temporary
    directory so we do not touch the real storage.
    """
    # Patch the IMAGE_DIR used by the helper
    from app import settings as s
    original_dir = s.IMAGE_DIR
    s.IMAGE_DIR = tmp_path
    try:
        img_path = _create_dummy_image(tmp_path)
        resolved = t._resolve_init_image_path(img_path.name)
        assert resolved == img_path
    finally:
        s.IMAGE_DIR = original_dir


def test_resolve_init_image_path_url(tmp_path: Path, monkeypatch):
    """Helper should resolve a full public URL pointing to ``/images/``.
    """
    from app import settings as s
    # Use a temporary IMAGE_DIR and ensure the file exists there
    monkeypatch.setattr(s, "IMAGE_DIR", tmp_path)
    img_path = _create_dummy_image(tmp_path)
    url = f"{s.PUBLIC_BASE_URL}/images/{img_path.name}"
    resolved = t._resolve_init_image_path(url)
    assert resolved == img_path


def test_resolve_init_image_path_invalid(monkeypatch):
    """Invalid inputs must raise appropriate exceptions."""
    # Empty string
    with pytest.raises(ValueError):
        t._resolve_init_image_path("")
    # External URL should be rejected
    with pytest.raises(ValueError):
        t._resolve_init_image_path("http://evil.com/image.png")
    # Path traversal attempt
    with pytest.raises(ValueError):
        t._resolve_init_image_path("../../../etc/passwd")


class DummyMCP:
    """A minimal stub mimicking the FastMCP API used in ``register_image_tools``.

    The ``tool`` decorator simply stores the wrapped function in ``self.tools``
    and returns it unchanged.
    """

    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func
        return decorator


def _mock_session_factory(b64_image: str):
    """Create a mock ``requests.Session`` whose ``post`` method returns a
    response object with the expected JSON payload.
    """

    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "images": [b64_image],
                "parameters": {"test": "value"},
                "info": "mock info",
            }

    class MockSession:
        def post(self, url, json, timeout):  # noqa: A002 (shadowing built‑in)
            return MockResponse()

    return MockSession()


def test_img2img_success(tmp_path: Path, monkeypatch):
    """End‑to‑end test of the ``img2img`` tool with a mocked WebUI.

    The test patches ``_resolve_init_image_path`` to return a known image and
    patches ``get_session`` to return a mock session that supplies a deterministic
    response. The returned report string is inspected for expected fragments.
    """
    # Prepare a dummy init image and its base64 representation
    img_path = _create_dummy_image(tmp_path)
    with open(img_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode("utf-8")

    # Patch the helper to return our temporary image regardless of input
    monkeypatch.setattr(t, "_resolve_init_image_path", lambda _: img_path)
    # Patch the session creator to use our mock session
    monkeypatch.setattr(t, "get_session", lambda: _mock_session_factory(b64_data))

    # Register tools on a dummy MCP instance and retrieve the img2img function
    dummy = DummyMCP()
    t.register_image_tools(dummy)
    img2img = dummy.tools["img2img"]

    # Call the tool with minimal valid arguments
    result = img2img(
        prompt="test prompt",
        init_image_url="ignored",  # value is ignored because of the monkeypatch
    )

    # The result should contain the prompt, init image name and a URL placeholder
    assert "test prompt" in result
    assert img_path.name in result
    assert "http" in result  # URL generated from PUBLIC_BASE_URL
    # The mock info text should appear in the parameters block
    assert "mock info" in result


def test_img2img_validation_errors(monkeypatch):
    """Validate that ``img2img`` raises ``ValueError`` for out‑of‑range inputs."""
    dummy = DummyMCP()
    t.register_image_tools(dummy)
    img2img = dummy.tools["img2img"]

    # Empty prompt
    with pytest.raises(ValueError, match="prompt must not be empty"):
        img2img(prompt="   ", init_image_url="dummy.png")

    # Invalid denoising strength
    with pytest.raises(ValueError, match="denoising_strength must be in range"):
        img2img(prompt="p", init_image_url="dummy.png", denoising_strength=1.5)

    # Invalid resize mode
    with pytest.raises(ValueError, match="resize_mode must be in range"):
        img2img(prompt="p", init_image_url="dummy.png", resize_mode=5)
