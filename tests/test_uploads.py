import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sprag import Controller, Field, Request, Schema, Screen, UploadedFile, action, page
from sprag.runtime.http.wsgi import SpragWSGIApp


class UploadScreen(Screen):
    def render(self, data):
        return None


class UploadController(Controller):
    route = "/upload-demo"

    @action(
        schema=Schema(
            "upload_asset",
            {
                "title": Field(str, required=True),
                "publish": Field(bool, required=False, default=False),
            },
        )
    )
    def upload_asset(self, title, publish=False):
        primary = self.request.file("asset")
        gallery = self.request.files_list("gallery")
        return {
            "title": title,
            "publish": bool(publish),
            "session_id": self.request.session_id,
            "primary": {
                "filename": primary.filename if primary else None,
                "content_type": primary.content_type if primary else None,
                "size": primary.size if primary else 0,
            },
            "gallery_count": len(gallery),
            "note": self.request.form.get("note"),
        }


def _multipart_body(boundary, *, fields=None, files=None):
    fields = fields or {}
    files = files or []
    chunks = []
    for name, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                    str(item).encode("utf-8"),
                    b"\r\n",
                ]
            )
    for name, filename, content_type, data in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                data,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)


class UploadSupportTests(unittest.TestCase):
    def setUp(self):
        upload_page = page(
            path="/upload-demo",
            controller=UploadController,
            screen=UploadScreen,
            mode="hybrid",
        )
        self.fake_app = SimpleNamespace(
            pages=lambda: [("app.routes.upload_demo.page", upload_page)],
            mounts=lambda: [],
        )
        self.tempdir = tempfile.TemporaryDirectory()
        self.wsgi = SpragWSGIApp(self.fake_app, Path(self.tempdir.name))

    def tearDown(self):
        self.tempdir.cleanup()

    def _post_upload(self, body, content_type, *, cookie=None):
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/__sprag__/uploads",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "QUERY_STRING": "",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.url_scheme": "http",
        }
        if cookie:
            environ["HTTP_COOKIE"] = cookie
        started = {}

        def start_response(status, headers):
            started["status"] = status
            started["headers"] = headers

        response = b"".join(self.wsgi(environ, start_response))
        return started["status"], dict(started["headers"]), json.loads(response.decode("utf-8"))

    def test_upload_endpoint_dispatches_payload_and_files(self):
        boundary = "sprag-boundary"
        body = _multipart_body(
            boundary,
            fields={
                "__sprag_route": "/upload-demo",
                "__sprag_action": "upload_asset",
                "__sprag_payload": json.dumps({"title": "Release Notes", "publish": True}),
                "note": "browser raw field",
            },
            files=[
                ("asset", "notes.txt", "text/plain", b"hello from sprag"),
                ("gallery", "one.txt", "text/plain", b"one"),
                ("gallery", "two.txt", "text/plain", b"two"),
            ],
        )

        status, headers, payload = self._post_upload(
            body,
            f"multipart/form-data; boundary={boundary}",
        )

        self.assertEqual(status, "200 OK")
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["value"]["title"], "Release Notes")
        self.assertTrue(payload["value"]["publish"])
        self.assertTrue(payload["value"]["session_id"])
        self.assertEqual(payload["value"]["primary"]["filename"], "notes.txt")
        self.assertEqual(payload["value"]["primary"]["content_type"], "text/plain")
        self.assertEqual(payload["value"]["primary"]["size"], len(b"hello from sprag"))
        self.assertEqual(payload["value"]["gallery_count"], 2)
        self.assertEqual(payload["value"]["note"], "browser raw field")
        self.assertIn("SPRAG_SID=", headers["Set-Cookie"])

    def test_upload_endpoint_rejects_non_multipart_requests(self):
        status, headers, payload = self._post_upload(
            b'{"route":"/upload-demo"}',
            "application/json",
        )

        self.assertEqual(status, "415 Unsupported Media Type")
        self.assertFalse(payload["ok"])
        self.assertIn("multipart/form-data", payload["error"])
        self.assertIn("SPRAG_SID=", headers["Set-Cookie"])

    def test_upload_endpoint_preserves_existing_session_cookie(self):
        boundary = "sprag-cookie-boundary"
        body = _multipart_body(
            boundary,
            fields={
                "__sprag_route": "/upload-demo",
                "__sprag_action": "upload_asset",
                "__sprag_payload": json.dumps({"title": "Cookie Path", "publish": False}),
            },
            files=[("asset", "cookie.txt", "text/plain", b"cookie")],
        )

        status, headers, payload = self._post_upload(
            body,
            f"multipart/form-data; boundary={boundary}",
            cookie="SPRAG_SID=known-session",
        )

        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["value"]["session_id"], "known-session")
        self.assertNotIn("Set-Cookie", headers)

    def test_uploaded_file_helper_can_save_and_decode(self):
        upload = UploadedFile(
            name="asset",
            filename="hello.txt",
            content_type="text/plain",
            data=b"hello",
        )

        self.assertEqual(upload.size, 5)
        self.assertEqual(upload.read(), b"hello")
        self.assertEqual(upload.text(), "hello")

        target = Path(self.tempdir.name) / "saved.txt"
        saved = upload.save(target)
        self.assertEqual(saved.read_text(encoding="utf-8"), "hello")

    def test_request_file_helpers_normalize_single_and_multiple_uploads(self):
        first = UploadedFile(name="asset", filename="one.txt", data=b"1")
        second = UploadedFile(name="asset", filename="two.txt", data=b"2")
        request = Request(path="/", files={"asset": [first, second], "cover": first})

        self.assertEqual(request.file("asset").filename, "one.txt")
        self.assertEqual(len(request.files_list("asset")), 2)
        self.assertEqual(request.file("cover").filename, "one.txt")
        self.assertEqual(request.files_list("missing"), [])


if __name__ == "__main__":
    unittest.main()
