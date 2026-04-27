import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from social_post_extractor_mcp.social_extractor import (
    BilibiliPlatformAdapter,
    DEFAULT_ASR_MODEL,
    DEFAULT_ASR_PROVIDER,
    DashScopeASRProvider,
    ExtractionContext,
    OpenAICompatibleASRProvider,
    OwnerAnalyticsCommandProvider,
    SocialExtractorService,
    SocialPost,
    build_info_dict,
    build_volcengine_request_payload,
    build_volcengine_audio_request,
    build_volcengine_full_client_request,
    default_model_for_provider,
    parse_douyin_creator_items,
    parse_douyin_creator_overview,
    parse_douyin_creator_post_detail,
    parse_douyin_creator_profile,
    parse_volcengine_server_message,
    _extract_html_title,
    _extract_title_from_share_text,
    XHSStateParser,
    provider_asr_config,
    provider_volcengine_speech_config,
)
from social_post_extractor_mcp.env_loader import load_env_file


class FakePlatformAdapter:
    def __init__(self, post: SocialPost):
        self.post = post

    def can_handle(self, share_text: str) -> bool:
        return True

    def fetch_post(self, share_text: str) -> SocialPost:
        return self.post


class FakeAsrProvider:
    def __init__(self, transcript: str):
        self.transcript = transcript

    def transcribe(self, post: SocialPost, context: ExtractionContext) -> str:
        return self.transcript


class FakeCleanupProvider:
    def __init__(self, cleaned: str):
        self.cleaned = cleaned

    def cleanup(self, post: SocialPost, raw_transcript: str | None, image_texts: list[str], context: ExtractionContext) -> str:
        return self.cleaned


class FailingVisionProvider:
    def read_image_text(self, image_url: str, context: ExtractionContext, image_index: int) -> str:
        raise RuntimeError("vision failed")


class FakeOcrProvider:
    def read_image_text(self, image_url: str, context: ExtractionContext, image_index: int) -> str:
        return f"OCR:{image_index}:{image_url.rsplit('/', 1)[-1]}"


class FailingAsrProvider:
    def transcribe(self, post: SocialPost, context: ExtractionContext) -> str:
        raise RuntimeError("asr failed")


class SocialExtractorServiceTests(unittest.TestCase):
    def test_load_env_file_supports_plain_and_export_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "social-post-extractor.env"
            env_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "ASR_PROVIDER=bailian",
                        "export BAILIAN_API_KEY='sk-demo'",
                        'DASHSCOPE_API_KEY="sk-dashscope"',
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"ASR_PROVIDER": "existing"}, clear=True):
                load_env_file(env_path)

                self.assertEqual(os.environ["ASR_PROVIDER"], "existing")
                self.assertEqual(os.environ["BAILIAN_API_KEY"], "sk-demo")
                self.assertEqual(os.environ["DASHSCOPE_API_KEY"], "sk-dashscope")

    def test_explicit_empty_cleanup_registry_disables_env_cleanup_provider(self):
        post = SocialPost(
            platform="xiaohongshu",
            content_type="image_note",
            source_url="https://www.xiaohongshu.com/explore/demo",
            resolved_url="https://www.xiaohongshu.com/explore/demo",
            post_id="note-no-cleanup",
            title="测试不自动调用环境清理模型",
            body="正文段落",
            image_urls=[],
        )
        service = SocialExtractorService(
            platform_adapters=[FakePlatformAdapter(post)],
            asr_providers={},
            cleanup_providers={},
            vision_providers={},
            ocr_provider=FakeOcrProvider(),
        )

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict("os.environ", {"BAILIAN_API_KEY": "demo-key"}, clear=False),
        ):
            result = service.extract_social_post(
                "https://www.xiaohongshu.com/explore/demo",
                output_dir=tmpdir,
            )

        self.assertEqual(result["info"]["status"], "success")
        self.assertIsNone(result["info"]["clean_provider"])
        self.assertEqual(result["script_preview"], "正文段落")

    def test_defaults_prefer_bailian_simple_stack(self):
        self.assertEqual(DEFAULT_ASR_PROVIDER, "bailian")
        self.assertEqual(DEFAULT_ASR_MODEL, "paraformer-v2")
        self.assertEqual(default_model_for_provider("bailian", "vision"), "qwen3-vl-flash")
        self.assertEqual(default_model_for_provider("bailian", "cleanup"), "qwen-flash")

    def test_default_asr_registry_includes_bailian_and_doubao(self):
        service = SocialExtractorService(
            platform_adapters=[],
            cleanup_providers={},
            vision_providers={},
            ocr_provider=FakeOcrProvider(),
        )

        self.assertIn("bailian", service.asr_providers)
        self.assertIn("doubao", service.asr_providers)
        self.assertIn("volcengine_speech", service.asr_providers)
        self.assertIsInstance(service.asr_providers["bailian"], DashScopeASRProvider)

    def test_default_provider_resolution_prefers_bailian_for_vision_and_cleanup(self):
        service = SocialExtractorService(
            platform_adapters=[],
            cleanup_providers={},
            vision_providers={},
            ocr_provider=FakeOcrProvider(),
        )

        with patch.dict("os.environ", {"BAILIAN_API_KEY": "demo-key"}, clear=False):
            self.assertEqual(service._default_vision_provider(), "bailian")
            self.assertEqual(service._default_cleanup_provider(), "bailian")

    def test_info_uses_resolved_default_models_for_vision_and_cleanup(self):
        post = SocialPost(
            platform="xiaohongshu",
            content_type="image_note",
            source_url="https://www.xiaohongshu.com/demo",
            resolved_url="https://www.xiaohongshu.com/demo",
            post_id="xhs-demo",
            title="测试图文",
            body="正文",
            image_urls=["https://cdn.example.com/1.jpg"],
        )
        service = SocialExtractorService(
            platform_adapters=[FakePlatformAdapter(post)],
            cleanup_providers={"bailian": FakeCleanupProvider("整理后的脚本")},
            vision_providers={"bailian": FakeOcrProvider()},
            ocr_provider=FakeOcrProvider(),
        )

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict("os.environ", {"BAILIAN_API_KEY": "demo-key"}, clear=False),
        ):
            result = service.extract_social_post(
                "https://www.xiaohongshu.com/demo",
                output_dir=tmpdir,
            )

        self.assertEqual(result["info"]["vision_provider"], "bailian")
        self.assertEqual(result["info"]["vision_model"], "qwen3-vl-flash")
        self.assertEqual(result["info"]["clean_provider"], "bailian")
        self.assertEqual(result["info"]["clean_model"], "qwen-flash")

    def test_video_post_writes_script_and_info_files(self):
        post = SocialPost(
            platform="douyin",
            content_type="video",
            source_url="https://v.douyin.com/demo/",
            resolved_url="https://www.iesdouyin.com/share/video/123",
            post_id="123",
            title="测试抖音视频",
            body="视频简介",
            author_name="作者A",
            video_url="https://cdn.example.com/video.mp4",
            image_urls=["https://cdn.example.com/cover.jpg"],
        )
        service = SocialExtractorService(
            platform_adapters=[FakePlatformAdapter(post)],
            asr_providers={"siliconflow": FakeAsrProvider("原始转写内容")},
            cleanup_providers={"qwen": FakeCleanupProvider("整理后的脚本")},
            vision_providers={},
            ocr_provider=FakeOcrProvider(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = service.extract_social_post(
                "https://v.douyin.com/demo/",
                output_dir=tmpdir,
                asr_provider="siliconflow",
                clean_provider="qwen",
                clean_model="qwen-plus",
            )

            script_path = Path(result["script_path"])
            info_path = Path(result["info_path"])
            self.assertTrue(script_path.exists())
            self.assertTrue(info_path.exists())

            script_text = script_path.read_text(encoding="utf-8")
            self.assertIn("## 整理稿", script_text)
            self.assertIn("整理后的脚本", script_text)
            self.assertIn("## 原始转写", script_text)
            self.assertIn("原始转写内容", script_text)
            self.assertIn("## 原笔记正文", script_text)
            self.assertIn("视频简介", script_text)

            info = json.loads(info_path.read_text(encoding="utf-8"))
            self.assertEqual(info["platform"], "douyin")
            self.assertEqual(info["content_type"], "video")
            self.assertEqual(info["post_id"], "123")
            self.assertEqual(info["asr_provider"], "siliconflow")
            self.assertEqual(info["clean_provider"], "qwen")
            self.assertEqual(info["clean_model"], "qwen-plus")
            self.assertEqual(result["script_preview"], "整理后的脚本")

    def test_image_note_falls_back_to_ocr(self):
        post = SocialPost(
            platform="xiaohongshu",
            content_type="image_note",
            source_url="https://www.xiaohongshu.com/explore/demo",
            resolved_url="https://www.xiaohongshu.com/explore/demo?xsec_token=abc",
            post_id="note-1",
            title="测试图文笔记",
            body="正文内容",
            author_name="作者B",
            image_urls=[
                "https://img.example.com/1.jpg",
                "https://img.example.com/2.jpg",
            ],
        )
        service = SocialExtractorService(
            platform_adapters=[FakePlatformAdapter(post)],
            asr_providers={},
            cleanup_providers={},
            vision_providers={"qwen-vl": FailingVisionProvider()},
            ocr_provider=FakeOcrProvider(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = service.extract_social_post(
                "https://www.xiaohongshu.com/explore/demo",
                output_dir=tmpdir,
                vision_provider="qwen-vl",
            )

            script_text = Path(result["script_path"]).read_text(encoding="utf-8")
            self.assertIn("## 图片文字提取", script_text)
            self.assertIn("OCR:1:1.jpg", script_text)
            self.assertIn("OCR:2:2.jpg", script_text)
            self.assertIn("## 整理稿", script_text)
            self.assertEqual(result["info"]["status"], "success")

    def test_rule_based_cleanup_is_used_when_no_cleanup_provider(self):
        post = SocialPost(
            platform="xiaohongshu",
            content_type="image_note",
            source_url="https://www.xiaohongshu.com/explore/demo",
            resolved_url="https://www.xiaohongshu.com/explore/demo",
            post_id="note-2",
            title="测试规则整理",
            body="正文段落",
            image_urls=["https://img.example.com/one.jpg"],
        )
        service = SocialExtractorService(
            platform_adapters=[FakePlatformAdapter(post)],
            asr_providers={},
            cleanup_providers={},
            vision_providers={"qwen-vl": FailingVisionProvider()},
            ocr_provider=FakeOcrProvider(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = service.extract_social_post(
                "https://www.xiaohongshu.com/explore/demo",
                output_dir=tmpdir,
                vision_provider="qwen-vl",
            )

            self.assertIn("正文段落", result["script_preview"])
            self.assertIn("OCR:1:one.jpg", result["script_preview"])

    def test_video_asr_failure_raises_immediately(self):
        post = SocialPost(
            platform="douyin",
            content_type="video",
            source_url="https://v.douyin.com/demo/",
            resolved_url="https://www.iesdouyin.com/share/video/999",
            post_id="999",
            title="测试 ASR 失败",
            body="只有正文也要落盘",
            author_name="作者C",
            video_url="https://cdn.example.com/video.mp4",
        )
        service = SocialExtractorService(
            platform_adapters=[FakePlatformAdapter(post)],
            asr_providers={"siliconflow": FailingAsrProvider()},
            cleanup_providers={},
            vision_providers={},
            ocr_provider=FakeOcrProvider(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(RuntimeError, "视频转写失败: asr failed"):
                service.extract_social_post(
                    "https://v.douyin.com/demo/",
                    output_dir=tmpdir,
                    asr_provider="siliconflow",
                )

    def test_openai_compatible_asr_provider_uses_provider_specific_upload_endpoint(self):
        post = SocialPost(
            platform="douyin",
            content_type="video",
            source_url="https://v.douyin.com/demo/",
            resolved_url="https://www.iesdouyin.com/share/video/888",
            post_id="888",
            title="测试豆包 ASR",
            video_url="https://cdn.example.com/video.mp4",
        )
        context = ExtractionContext(
            asr_provider="doubao",
            asr_model="doubao-asr-demo",
        )
        provider = OpenAICompatibleASRProvider("doubao")

        with (
            patch.dict(
                "os.environ",
                {
                    "ARK_API_KEY": "ark-demo-key",
                    "ARK_ASR_URL": "https://ark.example.com/audio/transcriptions",
                },
                clear=False,
            ),
            patch("social_post_extractor_mcp.social_extractor.download_binary") as download_binary,
            patch("social_post_extractor_mcp.social_extractor.extract_audio") as extract_audio,
            patch("social_post_extractor_mcp.social_extractor.transcribe_audio_via_upload") as transcribe_audio_via_upload,
        ):
            download_binary.return_value = Path("/tmp/demo.mp4")
            extract_audio.return_value = Path("/tmp/demo.mp3")
            transcribe_audio_via_upload.return_value = "转写结果"

            transcript = provider.transcribe(post, context)

        self.assertEqual(transcript, "转写结果")
        transcribe_audio_via_upload.assert_called_once()
        kwargs = transcribe_audio_via_upload.call_args.kwargs
        self.assertEqual(kwargs["api_url"], "https://ark.example.com/audio/transcriptions")
        self.assertEqual(kwargs["api_key"], "ark-demo-key")
        self.assertEqual(kwargs["model"], "doubao-asr-demo")


class DashScopeASRProviderTests(unittest.TestCase):
    def test_short_videos_use_cloud_mirror_and_realtime_asr(self):
        post = SocialPost(
            platform="douyin",
            content_type="video",
            source_url="https://v.douyin.com/demo/",
            resolved_url="https://www.iesdouyin.com/share/video/777",
            post_id="777",
            title="测试抖音视频",
            duration_sec=20,
            video_url="https://cdn.example.com/video.mp4",
        )
        context = ExtractionContext(
            asr_provider="bailian",
            asr_model="paraformer-v2",
        )
        provider = DashScopeASRProvider()

        with (
            patch.dict("os.environ", {"BAILIAN_API_KEY": "demo-key"}, clear=False),
            patch("social_post_extractor_mcp.social_extractor.stream_remote_media_to_dashscope_oss") as mirror_media,
            patch("social_post_extractor_mcp.social_extractor.run_dashscope_multimodal_asr") as run_realtime,
            patch("social_post_extractor_mcp.social_extractor.run_dashscope_filetrans_task") as run_filetrans,
        ):
            mirror_media.return_value = "oss://dashscope-instant/demo/777.mp4"
            run_realtime.return_value = "云端短视频转写"

            transcript = provider.transcribe(post, context)

        self.assertEqual(transcript, "云端短视频转写")
        self.assertEqual(context.asr_model, "qwen3-asr-flash")
        mirror_media.assert_called_once()
        self.assertEqual(mirror_media.call_args.kwargs["source_url"], post.video_url)
        self.assertEqual(mirror_media.call_args.kwargs["model_name"], "qwen3-asr-flash")
        run_realtime.assert_called_once_with(
            oss_url="oss://dashscope-instant/demo/777.mp4",
            api_key="demo-key",
            model="qwen3-asr-flash",
        )
        run_filetrans.assert_not_called()

    def test_long_videos_use_cloud_mirror_and_filetrans(self):
        post = SocialPost(
            platform="douyin",
            content_type="video",
            source_url="https://v.douyin.com/demo/",
            resolved_url="https://www.iesdouyin.com/share/video/999",
            post_id="999",
            title="测试长视频",
            duration_sec=2036,
            video_url="https://cdn.example.com/long.mp4",
        )
        context = ExtractionContext(
            asr_provider="bailian",
            asr_model="paraformer-v2",
        )
        provider = DashScopeASRProvider()

        with (
            patch.dict("os.environ", {"BAILIAN_API_KEY": "demo-key"}, clear=False),
            patch("social_post_extractor_mcp.social_extractor.stream_remote_media_to_dashscope_oss") as mirror_media,
            patch("social_post_extractor_mcp.social_extractor.run_dashscope_multimodal_asr") as run_realtime,
            patch("social_post_extractor_mcp.social_extractor.run_dashscope_filetrans_task") as run_filetrans,
        ):
            mirror_media.return_value = "oss://dashscope-instant/demo/999.mp4"
            run_filetrans.return_value = "云端长视频转写"

            transcript = provider.transcribe(post, context)

        self.assertEqual(transcript, "云端长视频转写")
        self.assertEqual(context.asr_model, "qwen3-asr-flash-filetrans")
        mirror_media.assert_called_once()
        self.assertEqual(mirror_media.call_args.kwargs["source_url"], post.video_url)
        self.assertEqual(mirror_media.call_args.kwargs["model_name"], "qwen3-asr-flash-filetrans")
        run_filetrans.assert_called_once_with(
            oss_url="oss://dashscope-instant/demo/999.mp4",
            api_key="demo-key",
            model="qwen3-asr-flash-filetrans",
        )
        run_realtime.assert_not_called()


class ProviderConfigTests(unittest.TestCase):
    def test_provider_asr_config_reads_bailian_defaults(self):
        with patch.dict(
            "os.environ",
            {
                "BAILIAN_API_KEY": "bailian-demo-key",
            },
            clear=False,
        ):
            config = provider_asr_config("bailian")

        self.assertIsNotNone(config)
        self.assertEqual(config["api_key"], "bailian-demo-key")
        self.assertEqual(
            config["api_url"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/transcriptions",
        )

    def test_provider_volcengine_speech_config_reads_defaults(self):
        with patch.dict(
            "os.environ",
            {
                "VOLCENGINE_SPEECH_APP_ID": "123456789",
                "VOLCENGINE_SPEECH_ACCESS_TOKEN": "token-demo",
            },
            clear=False,
        ):
            config = provider_volcengine_speech_config()

        self.assertIsNotNone(config)
        self.assertEqual(config["app_id"], "123456789")
        self.assertEqual(config["access_token"], "token-demo")
        self.assertEqual(config["resource_id"], "volc.seedasr.sauc.duration")
        self.assertEqual(config["ws_url"], "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async")


class VolcengineSpeechProtocolTests(unittest.TestCase):
    def test_build_full_client_request_sets_expected_header(self):
        frame = build_volcengine_full_client_request(
            {
                "user": {"uid": "u-demo"},
                "audio": {"format": "wav", "rate": 16000, "bits": 16, "channel": 1},
                "request": {"model_name": "bigmodel"},
            }
        )

        self.assertEqual(frame[0], 0x11)
        self.assertEqual(frame[1], 0x10)
        self.assertEqual(frame[2], 0x11)
        self.assertEqual(frame[3], 0x00)

    def test_build_audio_request_marks_last_packet(self):
        non_final = build_volcengine_audio_request(b"abc", is_final=False)
        final = build_volcengine_audio_request(b"abc", is_final=True)

        self.assertEqual(non_final[0], 0x11)
        self.assertEqual(non_final[1], 0x20)
        self.assertEqual(non_final[2], 0x01)
        self.assertEqual(final[1], 0x22)

    def test_parse_server_message_supports_size_only_payload(self):
        payload = json.dumps({"result": {"text": "hello"}}).encode("utf-8")
        frame = bytes([0x11, 0x90, 0x10, 0x00]) + len(payload).to_bytes(4, "big") + payload

        parsed = parse_volcengine_server_message(frame)

        self.assertEqual(parsed["message_type"], 0x90)
        self.assertEqual(parsed["payload"]["result"]["text"], "hello")

    def test_parse_server_message_supports_sequence_and_payload_size(self):
        payload = json.dumps({"result": {"text": "hello-2"}}).encode("utf-8")
        frame = (
            bytes([0x11, 0x91, 0x10, 0x00])
            + (3).to_bytes(4, "big", signed=True)
            + len(payload).to_bytes(4, "big")
            + payload
        )

        parsed = parse_volcengine_server_message(frame)

        self.assertEqual(parsed["message_type"], 0x90)
        self.assertEqual(parsed["sequence"], 3)
        self.assertEqual(parsed["payload"]["result"]["text"], "hello-2")

    def test_build_request_payload_uses_pcm_for_streaming_audio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "demo.wav"
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * 320)

            payload = build_volcengine_request_payload(
                wav_path,
                ExtractionContext(asr_provider="volcengine_speech", asr_model="bigmodel"),
                "user-demo",
            )

        self.assertEqual(payload["audio"]["format"], "pcm")
        self.assertEqual(payload["audio"]["codec"], "raw")
        self.assertEqual(payload["audio"]["rate"], 16000)
        self.assertEqual(payload["audio"]["channel"], 1)


class XHSStateParserTests(unittest.TestCase):
    def test_extract_title_from_xhs_share_text(self):
        self.assertEqual(
            _extract_title_from_share_text(
                "Copy and open rednote to view the note\n"
                "香港人凭啥连续十年全球第一长寿？ http://xhslink.com/o/7qdk43ccqi0"
            ),
            "香港人凭啥连续十年全球第一长寿？",
        )
        self.assertEqual(
            _extract_title_from_share_text(
                "把文本复制好，来【小红书】看看这篇笔记吧！\n"
                "AGI 还有3年，保持健康，撑到2030年代初 http://xhslink.com/o/AroffmRfoET"
            ),
            "AGI 还有3年，保持健康，撑到2030年代初",
        )

    def test_extract_title_from_xhs_html_meta(self):
        self.assertEqual(
            _extract_html_title(
                '<html><head><meta property="og:title" content="香港人凭啥连续十年全球第一长寿？ #香港生活 - 小红书"></head></html>'
            ),
            "香港人凭啥连续十年全球第一长寿？",
        )

    def test_parse_html_extracts_video_metadata(self):
        html = """
        <html><body><script>
        window.__INITIAL_STATE__={"global":{"pwaAddDesktopPrompt":undefined},"note":{"noteDetailMap":{"abc123":{"note":{
        "noteId":"abc123",
        "xsecToken":"tok",
        "title":"视频标题",
        "desc":"视频正文",
        "type":"video",
        "time":123456,
        "user":{"nickname":"作者","userId":"user-1"},
        "imageList":[{"urlDefault":"http://img.example.com/cover.jpg"}],
        "video":{"media":{"stream":{"h264":[{"masterUrl":"http://video.example.com/a.mp4"}]}},"capa":{"duration":88}}
        }}}}}
        </script></body></html>
        """

        post = XHSStateParser.parse_html(
            html=html,
            source_url="https://xhslink.com/demo",
            resolved_url="https://www.xiaohongshu.com/explore/abc123?xsec_token=tok",
        )

        self.assertEqual(post.platform, "xiaohongshu")
        self.assertEqual(post.content_type, "video")
        self.assertEqual(post.post_id, "abc123")
        self.assertEqual(post.video_url, "https://video.example.com/a.mp4")
        self.assertEqual(post.cover_url, "https://img.example.com/cover.jpg")
        self.assertEqual(post.duration_sec, 88)

    def test_parse_html_uses_meta_title_when_note_title_is_missing(self):
        html = """
        <html><head><title>香港人凭啥连续十年全球第一长寿？ - 小红书</title></head><body><script>
        window.__INITIAL_STATE__={"note":{"noteDetailMap":{"abc123":{"note":{
        "noteId":"abc123",
        "title":"",
        "desc":"视频正文",
        "type":"video",
        "user":{"nickname":"作者","userId":"user-1"},
        "imageList":[{"urlDefault":"http://img.example.com/cover.jpg"}],
        "video":{"media":{"stream":{"h264":[{"masterUrl":"http://video.example.com/a.mp4"}]}}}
        }}}}}
        </script></body></html>
        """

        post = XHSStateParser.parse_html(
            html=html,
            source_url="https://xhslink.com/demo",
            resolved_url="https://www.xiaohongshu.com/explore/abc123?xsec_token=tok",
        )

        self.assertEqual(post.title, "香港人凭啥连续十年全球第一长寿？")

    def test_parse_html_extracts_structured_author_metrics_and_images(self):
        html = """
        <html><body><script>
        window.__INITIAL_STATE__={"note":{"noteDetailMap":{"abc123":{"note":{
        "noteId":"abc123",
        "title":"图文标题",
        "desc":"图文正文",
        "type":"normal",
        "time":123456,
        "user":{"nickname":"作者","userId":"user-1","redId":"red-1","avatar":"http://img.example.com/a.jpg","desc":"简介"},
        "interactInfo":{"likedCount":"12","collectedCount":"3","commentCount":"4","shareCount":"5"},
        "imageList":[{"urlDefault":"http://img.example.com/1.jpg"},{"urlPre":"http://img.example.com/2.jpg"}],
        "tagList":[{"name":"AI"}]
        }}}}}
        </script></body></html>
        """

        post = XHSStateParser.parse_html(
            html=html,
            source_url="https://xhslink.com/demo",
            resolved_url="https://www.xiaohongshu.com/explore/abc123?xsec_token=tok",
        )

        self.assertEqual(post.author_profile["name"], "作者")
        self.assertEqual(post.author_profile["handle"], "red-1")
        self.assertEqual(post.author_profile["avatar_url"], "https://img.example.com/a.jpg")
        self.assertEqual(post.public_metrics["likes"], "12")
        self.assertEqual(post.public_metrics["collects"], "3")
        self.assertEqual(post.public_metrics["comments"], "4")
        self.assertEqual(post.public_metrics["shares"], "5")
        self.assertEqual(
            post.media["image_urls"],
            ["https://img.example.com/1.jpg", "https://img.example.com/2.jpg"],
        )


class UnifiedOutputTests(unittest.TestCase):
    def test_build_info_dict_includes_unified_capture_sections(self):
        post = SocialPost(
            platform="bilibili",
            content_type="video",
            source_url="https://www.bilibili.com/video/BV123",
            resolved_url="https://www.bilibili.com/video/BV123",
            post_id="BV123",
            title="B站视频",
            body="简介",
            author_name="UP主",
            author_id="42",
            cover_url="https://i.example.com/cover.jpg",
            duration_sec=66,
            video_url=None,
            image_urls=["https://i.example.com/cover.jpg"],
            author_profile={
                "id": "42",
                "name": "UP主",
                "avatar_url": "https://i.example.com/avatar.jpg",
                "profile_url": "https://space.bilibili.com/42",
            },
            public_metrics={
                "views": 100,
                "likes": 9,
                "comments": 3,
                "shares": 2,
                "coins": 1,
                "favorites": 4,
                "danmaku": 5,
            },
            owner_metrics={"impressions": None, "trend": []},
            media={
                "cover_url": "https://i.example.com/cover.jpg",
                "image_urls": ["https://i.example.com/cover.jpg"],
                "video_url": None,
            },
        )

        info = build_info_dict(
            post,
            ExtractionContext(asr_provider="bailian", asr_model="qwen3-asr-flash"),
            status="success",
            errors=[],
            transcript_text="字幕内容",
            transcript_source="cloud_asr",
            image_analysis=["图片分析"],
        )

        self.assertEqual(info["author"]["profile_url"], "https://space.bilibili.com/42")
        self.assertEqual(info["public_metrics"]["views"], 100)
        self.assertEqual(info["owner_metrics"]["trend"], [])
        self.assertEqual(info["media"]["cover_url"], "https://i.example.com/cover.jpg")
        self.assertEqual(info["transcript"]["source"], "cloud_asr")
        self.assertEqual(info["transcript"]["text"], "字幕内容")
        self.assertEqual(info["image_analysis"], ["图片分析"])


class BilibiliPlatformAdapterTests(unittest.TestCase):
    def test_view_payload_maps_to_social_post(self):
        payload = {
            "code": 0,
            "data": {
                "bvid": "BV1demo",
                "aid": 123,
                "title": "访谈标题",
                "desc": "视频简介",
                "pic": "http://i.example.com/cover.jpg",
                "duration": 125,
                "pubdate": 1710000000,
                "owner": {"mid": 88, "name": "UP主", "face": "http://i.example.com/avatar.jpg"},
                "tname": "知识",
                "stat": {
                    "view": 1000,
                    "like": 100,
                    "reply": 20,
                    "share": 5,
                    "favorite": 30,
                    "coin": 6,
                    "danmaku": 8,
                },
                "pages": [{"page": 1, "cid": 456, "part": "P1", "duration": 125}],
            },
        }

        post = BilibiliPlatformAdapter.post_from_view_payload(
            payload,
            source_url="https://b23.tv/demo",
            resolved_url="https://www.bilibili.com/video/BV1demo",
        )

        self.assertEqual(post.platform, "bilibili")
        self.assertEqual(post.post_id, "BV1demo")
        self.assertEqual(post.title, "访谈标题")
        self.assertEqual(post.author_profile["profile_url"], "https://space.bilibili.com/88")
        self.assertEqual(post.public_metrics["views"], 1000)
        self.assertEqual(post.public_metrics["likes"], 100)
        self.assertEqual(post.public_metrics["comments"], 20)
        self.assertEqual(post.public_metrics["shares"], 5)
        self.assertEqual(post.public_metrics["favorites"], 30)
        self.assertEqual(post.public_metrics["coins"], 6)
        self.assertEqual(post.public_metrics["danmaku"], 8)
        self.assertEqual(post.extra["pages"][0]["cid"], 456)


class OwnerAnalyticsCommandProviderTests(unittest.TestCase):
    def test_builds_opencli_commands_for_owner_analytics(self):
        provider = OwnerAnalyticsCommandProvider(opencli_command="opencli", bb_browser_command="bb-browser")

        self.assertEqual(
            provider.build_command("xiaohongshu", "recent_posts", limit=3),
            ["opencli", "xiaohongshu", "creator-notes-summary", "--limit", "3", "-f", "json"],
        )
        self.assertEqual(
            provider.build_command("douyin", "post_detail", post_id="123"),
            ["opencli", "douyin", "stats", "123", "-f", "json"],
        )
        self.assertEqual(
            provider.build_command("douyin", "account_stats"),
            ["opencli", "douyin", "profile", "-f", "json"],
        )
        self.assertEqual(
            provider.build_command("bilibili", "post_detail", post_id="BV1demo"),
            ["bb-browser", "site", "bilibili/video", "BV1demo", "--json"],
        )
        self.assertEqual(
            provider.build_command("bilibili", "profile"),
            ["opencli", "bilibili", "me", "-f", "json"],
        )
        self.assertEqual(
            provider.build_command("bilibili", "search_user", account_name="AI杰瑞斯", limit=5),
            ["opencli", "bilibili", "search", "AI杰瑞斯", "--type", "user", "--limit", "5", "-f", "json"],
        )
        self.assertEqual(
            provider.build_command("bilibili", "recent_posts", account_id="123", limit=5),
            ["opencli", "bilibili", "user-videos", "123", "--limit", "5", "-f", "json"],
        )

    def test_parses_douyin_creator_profile_new_response_shape(self):
        rows = parse_douyin_creator_profile(
            {
                "status_code": 0,
                "user": {
                    "uid": "123",
                    "nickname": "AI 杰瑞斯",
                    "unique_id": "risingjerrys",
                    "follower_count": 2762,
                    "following_count": 34,
                    "aweme_count": 8,
                    "favoriting_count": 247,
                },
            }
        )

        self.assertEqual(rows[0]["nickname"], "AI 杰瑞斯")
        self.assertEqual(rows[0]["unique_id"], "risingjerrys")
        self.assertEqual(rows[0]["follower_count"], 2762)

    def test_parses_douyin_creator_items_preserving_string_ids_and_owner_metrics(self):
        payload = {
            "BaseResp": {"StatusCode": 0},
            "items": [
                {
                    "id": "7629447135930404130",
                    "description": "就在在刚刚，Claude 正式发布了opus-4.7！！！",
                    "create_time": "1776376800",
                    "metrics": {
                        "view_count": "4077",
                        "like_count": "65",
                        "comment_count": "2",
                        "share_count": "5",
                        "favorite_count": "17",
                        "avg_view_second": "22.372398",
                        "completion_rate": "0.014755",
                        "bounce_rate_2s": "0.286408",
                        "cover_show": "146",
                        "cover_click_rate": "0.952055",
                        "subscribe_count": "6",
                    },
                    "cover": {"url_list": ["http://img.example.com/cover.webp"]},
                }
            ],
        }

        rows = parse_douyin_creator_items(payload, limit=5)

        self.assertEqual(rows[0]["post_id"], "7629447135930404130")
        self.assertEqual(rows[0]["metrics"]["views"], "4077")
        self.assertEqual(rows[0]["metrics"]["favorites"], "17")
        self.assertEqual(rows[0]["metrics"]["impressions"], "146")
        self.assertEqual(rows[0]["metrics"]["followers_gained"], "6")
        self.assertEqual(rows[0]["cover_url"], "https://img.example.com/cover.webp")

    def test_parses_douyin_creator_post_detail_by_exact_id(self):
        payload = {
            "items": [
                {"id": "7629447135930404130", "description": "目标作品", "metrics": {"view_count": "4077"}},
                {"id": "7627446583856025601", "description": "另一个作品", "metrics": {"view_count": "1"}},
            ]
        }

        detail = parse_douyin_creator_post_detail(payload, post_id="7629447135930404130")

        self.assertEqual(detail["title"], "目标作品")
        self.assertEqual(detail["metrics"]["views"], "4077")

    def test_parses_douyin_creator_overview_with_labels_and_summary(self):
        overview = parse_douyin_creator_overview(
            {
                "data": {
                    "play": {"current_count": "123106", "last_period_incr": "44300", "option_list": []},
                    "digg": {"current_count": "6014", "last_period_incr": "-396", "option_list": []},
                }
            }
        )

        self.assertEqual(overview["summary"]["play"], "123106")
        self.assertEqual(overview["metrics"]["play"]["label"], "播放量")
        self.assertEqual(overview["metrics"]["digg"]["label"], "作品点赞")

    def test_douyin_owner_command_falls_back_to_same_origin_daemon(self):
        provider = OwnerAnalyticsCommandProvider(opencli_command="opencli", bb_browser_command="bb-browser")

        with (
            patch("social_post_extractor_mcp.social_extractor.subprocess.run") as run_command,
            patch.object(provider, "_run_douyin_creator_fallback") as fallback,
        ):
            run_command.return_value.returncode = 1
            run_command.return_value.stdout = ""
            run_command.return_value.stderr = "用户信息获取失败"
            fallback.return_value = {"status": "success", "source": "opencli_daemon_same_origin_fallback"}

            result = provider.run("douyin", "profile")

        self.assertEqual(result["source"], "opencli_daemon_same_origin_fallback")
        fallback.assert_called_once()

    def test_douyin_owner_command_falls_back_on_empty_success_result(self):
        provider = OwnerAnalyticsCommandProvider(opencli_command="opencli", bb_browser_command="bb-browser")

        with (
            patch("social_post_extractor_mcp.social_extractor.subprocess.run") as run_command,
            patch.object(provider, "_run_douyin_creator_fallback") as fallback,
        ):
            run_command.return_value.returncode = 0
            run_command.return_value.stdout = "[]"
            run_command.return_value.stderr = ""
            fallback.return_value = {"status": "success", "source": "opencli_daemon_same_origin_fallback"}

            result = provider.run("douyin", "recent_posts", limit=5)

        self.assertEqual(result["source"], "opencli_daemon_same_origin_fallback")
        fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
