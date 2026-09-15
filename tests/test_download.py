import hashlib
import io
import os
import tempfile
import unittest

import helpers  # noqa: F401
import download

URL = "https://github.com/user-attachments/files/1/theme.zip"


class FakeStream(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.read_bytes = 0

    def read(self, n=-1):
        chunk = super().read(n)
        self.read_bytes += len(chunk)
        return chunk


class Transport:
    """Serves a scripted list of responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        status, response_headers, body = self.responses.pop(0)
        return status, response_headers, FakeStream(body)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self.temp.name, "out.zip")

    def tearDown(self):
        self.temp.cleanup()

    def test_follows_allowed_redirect_and_hashes(self):
        body = b"PK" + b"x" * 1000
        transport = Transport([
            (302, {"Location": "https://objects.githubusercontent.com/github-production-"
                               "repository-file-5c1aeb/1/2?X-Amz-Signature=abc"}, b""),
            (200, {"Content-Length": str(len(body))}, body),
        ])
        size, digest = download.download(URL, self.dest, 2000, fetch=transport,
                                          headers={"Authorization": "Bearer secret"})
        self.assertEqual((size, digest), (len(body), hashlib.sha256(body).hexdigest()))
        with open(self.dest, "rb") as handle:
            self.assertEqual(handle.read(), body)
        # The credential goes to the first host only.
        self.assertIn("Authorization", transport.requests[0][1])
        self.assertNotIn("Authorization", transport.requests[1][1])

    def test_refuses_redirect_to_other_hosts(self):
        for location in ("https://evil.example/x", "http://objects.githubusercontent.com/x",
                         "https://github-production-evil.s3.amazonaws.com/x",
                         "https://objects.githubusercontent.com.evil.example/x",
                         "https://user@objects.githubusercontent.com/x"):
            transport = Transport([(302, {"Location": location}, b"")])
            with self.assertRaises(download.DownloadError, msg=location):
                download.download(URL, self.dest, 100, fetch=transport)

    def test_refuses_first_url_off_github(self):
        with self.assertRaises(download.DownloadError):
            download.download("https://evil.example/x.zip", self.dest, 100,
                              fetch=Transport([]))

    def test_content_length_over_cap_refused_before_reading(self):
        stream_body = b"x" * 50
        transport = Transport([(200, {"Content-Length": "10485761"}, stream_body)])
        with self.assertRaises(download.DownloadError) as caught:
            download.download(URL, self.dest, 10 * 1024 * 1024, fetch=transport)
        self.assertTrue(caught.exception.too_large)
        self.assertFalse(os.path.exists(self.dest))

    def test_stream_over_cap_stops(self):
        cap = 1000
        transport = Transport([(200, {}, b"y" * (cap * 200))])
        with self.assertRaises(download.DownloadError) as caught:
            download.download(URL, self.dest, cap, fetch=transport)
        self.assertTrue(caught.exception.too_large)
        self.assertLessEqual(os.path.getsize(self.dest), cap)

    def test_exact_cap_is_allowed(self):
        body = b"z" * 1000
        transport = Transport([(200, {"Content-Length": "1000"}, body)])
        self.assertEqual(download.download(URL, self.dest, 1000, fetch=transport)[0], 1000)

    def test_http_error_and_redirect_loops(self):
        with self.assertRaises(download.DownloadError):
            download.download(URL, self.dest, 100, fetch=Transport([(404, {}, b"")]))
        loop = [(302, {"Location": "https://objects.githubusercontent.com/a"}, b"")] * 10
        with self.assertRaises(download.DownloadError):
            download.download(URL, self.dest, 100, fetch=Transport(loop))


if __name__ == "__main__":
    unittest.main()
