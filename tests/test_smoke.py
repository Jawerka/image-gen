"""
Smoke tests for image-gen web endpoints.

These tests verify that the FastAPI routes return expected status codes
and basic response shapes WITHOUT requiring a running SD WebUI backend.
"""

from pathlib import Path

from app.settings import IMAGE_DIR, THUMB_DIR, WEBP_DIR


class TestHealth:
    """GET /health"""

    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "images_dir" in data
        assert "thumb_dir" in data


class TestGallery:
    """GET /gallery and GET /"""

    def test_gallery_empty(self, client):
        """Gallery should return 200 even when there are no images."""
        resp = client.get("/gallery")
        assert resp.status_code == 200
        data = resp.json()
        assert "images" in data
        assert "count" in data
        assert isinstance(data["images"], list)

    def test_gallery_limit(self, client):
        """Gallery should respect the `limit` query parameter."""
        resp = client.get("/gallery?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] <= 5

    def test_index_html(self, client):
        """Root path should return an HTML page."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


class TestImageEndpoints:
    """GET /images/{filename}, /thumbs/, /webp/, /meta/"""

    def test_image_not_found(self, client):
        resp = client.get("/images/nonexistent.png")
        assert resp.status_code == 404

    def test_thumb_not_found(self, client):
        resp = client.get("/thumbs/nonexistent.jpg")
        assert resp.status_code == 404

    def test_webp_not_found(self, client):
        resp = client.get("/webp/nonexistent.webp")
        assert resp.status_code == 404

    def test_meta_not_found(self, client):
        resp = client.get("/meta/nonexistent.png")
        assert resp.status_code == 404

    def test_image_path_traversal_blocked(self, client):
        """Path traversal attempts must be rejected."""
        resp = client.get("/images/../../../etc/passwd")
        # FastAPI / Starlette will return 404 or 422 for invalid path params
        assert resp.status_code in (400, 404, 422)


class TestRefresh:
    """GET /api/refresh"""

    def test_refresh_returns_json(self, client):
        resp = client.get("/api/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert "images" in data
        assert "count" in data


class TestCleanup:
    """POST /cleanup"""

    def test_cleanup_returns_removed_count(self, client):
        resp = client.post("/cleanup")
        assert resp.status_code == 200
        data = resp.json()
        assert "removed" in data
        assert isinstance(data["removed"], int)