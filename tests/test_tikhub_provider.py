import unittest
from unittest.mock import patch

from social_media_toolkit.providers.tikhub import (
    TIKHUB_API_KEY_SECRET_NAME,
    TikHubDouyinMediaProvider,
    TikHubError,
    _duration_seconds,
    _select_address_url,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def video_payload():
    return {
        "code": 200,
        "message": "Request successful. This request will incur a charge.",
        "data": {
            "aweme_detail": {
                "aweme_id": "7678265840617278720",
                "aweme_type": 0,
                "desc": "公开作品",
                "create_time": 1_783_000_000,
                "author": {
                    "uid": "author-1",
                    "sec_uid": "sec-1",
                    "unique_id": "public-author",
                    "nickname": "公开作者",
                    "avatar_thumb": {"url_list": ["https://p.example.com/avatar.jpg"]},
                },
                "statistics": {
                    "play_count": 100,
                    "digg_count": 8,
                    "comment_count": 3,
                    "share_count": 2,
                    "collect_count": 1,
                },
                "video": {
                    "duration": 645_446,
                    "cover": {"url_list": ["https://p.example.com/cover.jpg"]},
                    "play_addr_h264": {
                        "url_list": [
                            "https://www.douyin.com/aweme/v1/play/?video_id=demo",
                            "https://v3-dy-o.zjcdn.com/public/video.mp4",
                        ]
                    },
                    "bit_rate_audio": [
                        {
                            "play_addr": {
                                "url_list": ["https://v3-dy-o.zjcdn.com/public/audio.m4a"]
                            }
                        }
                    ],
                },
            }
        },
    }


class TikHubProviderTests(unittest.TestCase):
    def test_fetches_video_and_prefers_direct_cdn_url(self):
        provider = TikHubDouyinMediaProvider()
        with (
            patch(
                "social_media_toolkit.providers.tikhub._load_secret",
                return_value="synthetic-key",
            ),
            patch(
                "social_media_toolkit.providers.tikhub.requests.get",
                return_value=FakeResponse(video_payload()),
            ) as request,
        ):
            post = provider.fetch_post("复制打开 https://v.douyin.com/demo/ 看视频")

        self.assertEqual(post.platform, "douyin")
        self.assertEqual(post.post_id, "7678265840617278720")
        self.assertEqual(post.duration_sec, 645)
        self.assertEqual(post.video_url, "https://v3-dy-o.zjcdn.com/public/video.mp4")
        self.assertEqual(post.media["audio_url"], "https://v3-dy-o.zjcdn.com/public/audio.m4a")
        self.assertEqual(
            post.extra["metadata_route"],
            "tikhub.douyin.web.fetch_one_video_by_share_url",
        )
        self.assertTrue(post.extra["media_urls_ephemeral"])
        self.assertTrue(post.extra["may_incur_usage_cost"])
        call = request.call_args
        self.assertEqual(call.kwargs["params"], {"share_url": "https://v.douyin.com/demo/"})
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer synthetic-key")

    def test_fetches_public_image_note(self):
        payload = video_payload()
        detail = payload["data"]["aweme_detail"]
        detail["aweme_type"] = 68
        detail["video"] = {"cover": {"url_list": ["https://p.example.com/cover.jpg"]}}
        detail["images"] = [
            {"url_list": ["https://p.example.com/image-1.jpg"]},
            {"display_image": {"url_list": ["https://p.example.com/image-2.jpg"]}},
        ]
        with (
            patch("social_media_toolkit.providers.tikhub._load_secret", return_value="key"),
            patch(
                "social_media_toolkit.providers.tikhub.requests.get",
                return_value=FakeResponse(payload),
            ),
        ):
            post = TikHubDouyinMediaProvider().fetch_post("https://v.douyin.com/demo/")

        self.assertEqual(post.content_type, "image_note")
        self.assertIsNone(post.video_url)
        self.assertEqual(
            post.image_urls,
            [
                "https://p.example.com/image-1.jpg",
                "https://p.example.com/image-2.jpg",
            ],
        )
        self.assertIn("/note/", post.page_url)

    def test_missing_secret_stops_before_network(self):
        with (
            patch("social_media_toolkit.providers.tikhub._load_secret", return_value=None),
            patch("social_media_toolkit.providers.tikhub.requests.get") as request,
        ):
            with self.assertRaisesRegex(TikHubError, TIKHUB_API_KEY_SECRET_NAME):
                TikHubDouyinMediaProvider().fetch_post("https://v.douyin.com/demo/")
        request.assert_not_called()

    def test_reports_filtered_or_missing_work(self):
        payload = {
            "code": 200,
            "data": {"filter_list": [{"reason": 5, "detail_msg": "private"}]},
        }
        with (
            patch("social_media_toolkit.providers.tikhub._load_secret", return_value="key"),
            patch(
                "social_media_toolkit.providers.tikhub.requests.get",
                return_value=FakeResponse(payload),
            ),
        ):
            with self.assertRaisesRegex(TikHubError, r"\(5\)"):
                TikHubDouyinMediaProvider().fetch_post("https://v.douyin.com/demo/")

    def test_reports_provider_error_without_leaking_credentials(self):
        payload = {"code": 429, "message_zh": "额度不足", "data": None}
        with (
            patch("social_media_toolkit.providers.tikhub._load_secret", return_value="secret-value"),
            patch(
                "social_media_toolkit.providers.tikhub.requests.get",
                return_value=FakeResponse(payload),
            ),
        ):
            with self.assertRaisesRegex(TikHubError, "额度不足") as raised:
                TikHubDouyinMediaProvider().fetch_post("https://v.douyin.com/demo/")
        self.assertNotIn("secret-value", str(raised.exception))

    def test_normalizes_duration_and_direct_url_priority(self):
        self.assertEqual(_duration_seconds(645_446), 645)
        self.assertEqual(_duration_seconds(367.6), 368)
        self.assertEqual(_duration_seconds(3_000, milliseconds=True), 3)
        self.assertEqual(
            _select_address_url(
                {
                    "url_list": [
                        "https://www.douyin.com/aweme/v1/play/?video_id=demo",
                        "https://v5-dy-ov-experiment.zjcdn.com/public/video.mp4",
                        "https://v5-dy-o.zjcdn.com/public/video.mp4",
                        "https://v3-dy-o.zjcdn.com/public/video.mp4",
                    ]
                }
            ),
            "https://v3-dy-o.zjcdn.com/public/video.mp4",
        )


if __name__ == "__main__":
    unittest.main()
