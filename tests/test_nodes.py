"""Tests against a local mock of the APIKO API. Client tests need only the standard library; node tests
also need torch, numpy and Pillow (run them inside a ComfyUI Python environment).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mock_apiko  # noqa: E402

spec = importlib.util.spec_from_file_location("apiko_pkg", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
pkg = importlib.util.module_from_spec(spec)
sys.modules["apiko_pkg"] = pkg
spec.loader.exec_module(pkg)
client = sys.modules["apiko_pkg.client"]
nodes = sys.modules["apiko_pkg.nodes"]

try:
    import torch  # noqa: F401

    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False


class Base(unittest.TestCase):
    def setUp(self):
        self.server, self.state = mock_apiko.start()
        os.environ["APIKO_BASE_URL"] = self.state.base
        os.environ["APIKO_API_KEY"] = mock_apiko.KEY
        client._schema_cache.update(at=0.0, models=None)
        self._sleep = client.time.sleep
        client.time.sleep = lambda s: None

    def tearDown(self):
        client.time.sleep = self._sleep
        self.server.shutdown()
        self.server.server_close()

    def posts(self, path):
        return [b for m, p, b in self.state.requests if m == "POST" and p == path]


class ClientTests(Base):
    def test_key_never_sent_to_plain_http_remote(self):
        os.environ["APIKO_BASE_URL"] = "http://api.example.com"
        with self.assertRaisesRegex(client.ApiError, "non-HTTPS"):
            client.api_key()

    def test_missing_key_explains_setup(self):
        os.environ.pop("APIKO_API_KEY")
        if os.path.exists(client.KEY_FILE):
            self.skipTest("api_key.txt present")
        with self.assertRaisesRegex(client.ApiError, "APIKO_API_KEY"):
            client.api_key()

    def test_unknown_field_dropped_and_tier_case_matched(self):
        path, body, use_async = client.plan("image", "minimax-h3-image", {"model": "minimax-h3-image", "prompt": "x", "resolution": "4k", "size": "1:1"})
        self.assertEqual(path, "/v1/images/generations")
        self.assertFalse(use_async)
        self.assertEqual(body, {"model": "minimax-h3-image", "prompt": "x", "resolution": "4K"})

    def test_bad_enum_rejected_before_submit(self):
        with self.assertRaisesRegex(client.ApiError, "nothing was billed"):
            client.submit("video", "seedance-2.5", {"prompt": "x", "resolution": "4k"})
        self.assertEqual(self.posts("/v1/videos"), [])

    def test_range_and_required(self):
        with self.assertRaisesRegex(client.ApiError, "out of range"):
            client.submit("video", "seedance-2.5", {"prompt": "x", "duration": 2})
        with self.assertRaisesRegex(client.ApiError, "first_frame"):
            client.submit("video", "wan3.0-image-to-video", {"prompt": "x"})

    def test_unknown_model(self):
        with self.assertRaisesRegex(client.ApiError, "not available to this API key"):
            client.submit("image", "nano-banana-9", {"prompt": "x"})

    def test_async_image_task_polls_and_downloads(self):
        tid, urls = client.submit("image", "nano-banana-2.1", {"prompt": "x", "resolution": "2k"})
        self.assertEqual(urls, [])
        self.assertEqual(self.posts("/v1/images/generations")[0], {"model": "nano-banana-2.1", "prompt": "x", "resolution": "2k", "async": True})
        task = client.poll(tid, interval=0, timeout=30)
        raw = client.download(client.result_urls(task)[0])
        self.assertEqual(raw, mock_apiko.PNG_1x1)

    def test_sync_image(self):
        tid, urls = client.submit("image", "minimax-h3-image", {"prompt": "x"})
        self.assertIsNone(tid)
        self.assertTrue(urls[0].endswith("/files/sync.png"))
        self.assertNotIn("async", self.posts("/v1/images/generations")[0])

    def test_failed_task_reports_refund(self):
        self.state.fail_next = True
        tid, _ = client.submit("video", "seedance-2.5", {"prompt": "x"})
        with self.assertRaisesRegex(client.ApiError, "refunded in full.*content refused"):
            client.poll(tid, interval=0, timeout=30)

    def test_timeout_tells_how_to_resume(self):
        self.state.polls_until_done = 10**6
        tid, _ = client.submit("video", "seedance-2.5", {"prompt": "x"})
        with self.assertRaisesRegex(client.ApiError, f"resume_task_id"):
            client.poll(tid, interval=1, timeout=0.5)

    def test_bad_key_is_reported(self):
        os.environ["APIKO_API_KEY"] = "sk-wrong"
        with self.assertRaisesRegex(client.ApiError, "HTTP 401"):
            client.submit("image", "nano-banana-2.1", {"prompt": "x"})


@unittest.skipUnless(HAVE_TORCH, "torch not installed (run inside ComfyUI's Python)")
class NodeTests(Base):
    def test_image_edit_uploads_batch(self):
        import torch

        images = torch.rand(2, 8, 8, 3)
        out, url, tid = nodes.APIKOImage().generate("nano-banana-2.1", "make it blue", "16:9", "default", "default", 0, images=images)
        self.assertEqual(tuple(out.shape), (1, 1, 1, 3))
        self.assertTrue(tid.startswith("task_"))
        body = self.posts("/v1/images/edits")[0]
        self.assertEqual(body["images"], ["https://img.example/upload-1.png", "https://img.example/upload-2.png"])
        self.assertEqual(body["size"], "16:9")
        self.assertNotIn("quality", body)

    def test_video_first_frame_and_options(self):
        import torch

        frame = torch.rand(1, 8, 8, 3)
        try:
            video, url, tid = nodes.APIKOVideo().generate(
                "seedance-2.5", "a cat surfing", 5, "720p", "16:9", "off", 42, first_frame=frame,
                reference_video_urls="https://example.com/a.mp4",
            )
        except client.ApiError as err:
            if "native VIDEO" not in str(err):
                raise
        body = self.posts("/v1/videos")[0]
        self.assertEqual(body["first_image_url"], "https://img.example/upload-1.png")
        self.assertEqual(body["generate_audio"], False)
        self.assertEqual(body["seed"], 42)
        self.assertEqual(body["reference_videos"], ["https://example.com/a.mp4"])

    def test_resume_does_not_submit(self):
        tid, _ = client.submit("image", "nano-banana-2.1", {"prompt": "x"})
        before = len(self.posts("/v1/images/generations"))
        nodes.APIKOImage().generate("nano-banana-2.1", "", "default", "default", "default", 0, resume_task_id=tid)
        self.assertEqual(len(self.posts("/v1/images/generations")), before)

    def test_chat(self):
        (text,) = nodes.APIKOChat().chat("gemini-3.5-flash", "write a prompt", "be brief", 100)
        self.assertEqual(text, "a cinematic prompt")
        self.assertEqual(self.posts("/v1/chat/completions")[0]["messages"][0], {"role": "system", "content": "be brief"})

    def test_chat_out_of_tokens_explains(self):
        with self.assertRaisesRegex(client.ApiError, "Raise max_tokens"):
            nodes.APIKOChat().chat("gemini-3.5-flash", "hi", "", 5)

    def test_reference_urls_must_be_https(self):
        with self.assertRaisesRegex(client.ApiError, "https://"):
            nodes.APIKOVideo().generate("seedance-2.5", "x", 5, "default", "default", "default", 0, reference_video_urls="http://x/a.mp4")


if __name__ == "__main__":
    unittest.main()
