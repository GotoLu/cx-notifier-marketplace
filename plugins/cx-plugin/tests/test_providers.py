from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))

from cx_notify.config import ResolvedChannel  # noqa: E402
from cx_notify.providers import (  # noqa: E402
    ProviderError,
    _provider_accepted,
    feishu_signature,
    prepare_request,
    validate_webhook_url,
)
from cx_notify.runtime import make_test_event  # noqa: E402


class ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event = make_test_event("/tmp/demo")

    def test_feishu_payload_and_signature(self) -> None:
        channel = ResolvedChannel(
            name="feishu",
            type="feishu",
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/example",
            secret="secret",
            mention_all=True,
        )
        request = prepare_request(channel, self.event)
        payload = json.loads(request.body)
        self.assertEqual(payload["msg_type"], "text")
        text = payload["content"]["text"]
        mention = '<at user_id="all">所有人</at>'
        self.assertIn("Codex 待确认", text)
        self.assertTrue(text.endswith(f"\n{mention}"))
        self.assertEqual(text.splitlines()[-1], mention)
        self.assertEqual(text.count(mention), 1)
        self.assertIn("timestamp", payload)
        self.assertIn("sign", payload)
        self.assertEqual(request.headers["Idempotency-Key"], self.event.notification_id)
        # Deterministic vector protects the intentionally unusual Feishu empty-message HMAC.
        self.assertEqual(
            feishu_signature("secret", 1700000000),
            "fiWS2+gh28DOydAv7hzONH/mDn9+b1Y4Y5ivXWXy8vA=",
        )

    def test_feishu_mention_all_is_opt_in_and_dynamic_tags_are_neutralized(self) -> None:
        event = replace(
            self.event,
            title='Untrusted <at user_id="all">所有人</at>',
        )
        base = ResolvedChannel(
            name="feishu",
            type="feishu",
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/example",
        )
        without_mention = json.loads(prepare_request(base, event).body)["content"]["text"]
        self.assertNotIn("<at ", without_mention)
        self.assertIn('＜at user_id="all"＞所有人＜/at＞', without_mention)

        with_mention = replace(base, mention_all=True)
        text = json.loads(prepare_request(with_mention, event).body)["content"]["text"]
        mention = '<at user_id="all">所有人</at>'
        self.assertEqual(text.count(mention), 1)
        self.assertTrue(text.endswith(f"\n{mention}"))

    def test_wecom_payload(self) -> None:
        channel = ResolvedChannel(
            name="wecom",
            type="wecom",
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=example",
        )
        payload = json.loads(prepare_request(channel, self.event).body)
        self.assertEqual(payload["msgtype"], "text")
        self.assertIn("请返回 Codex", payload["text"]["content"])
        self.assertNotIn('user_id="all"', payload["text"]["content"])

    def test_generic_payload_is_canonical_and_bearer_is_header_only(self) -> None:
        channel = ResolvedChannel(
            name="generic",
            type="webhook",
            webhook_url="https://hooks.example.com/codex",
            bearer_token="top-secret-token",
        )
        request = prepare_request(channel, self.event)
        payload = json.loads(request.body)
        self.assertEqual(payload["schema"], "codex.notification.v1")
        self.assertEqual(request.headers["Authorization"], "Bearer top-secret-token")
        self.assertNotIn("top-secret-token", request.body.decode("utf-8"))

    def test_provider_hosts_and_schemes_are_pinned(self) -> None:
        with self.assertRaises(ProviderError):
            validate_webhook_url("https://open.feishu.cn.evil.test/hook", "feishu")
        with self.assertRaises(ProviderError):
            validate_webhook_url("https://example.com/hook", "wecom")
        with self.assertRaises(ProviderError):
            validate_webhook_url("http://example.com/hook", "webhook")
        self.assertEqual(
            validate_webhook_url(
                "http://127.0.0.1:9999/hook",
                "webhook",
                allow_insecure_localhost=True,
            ),
            "http://127.0.0.1:9999/hook",
        )

    def test_embedded_credentials_and_fragments_are_rejected(self) -> None:
        for url in (
            "https://user:pass@example.com/hook",
            "https://example.com/hook#secret",
            " https://example.com/hook",
            "https://example.com/hook\n",
            "https://example.com:invalid/hook",
            "file:///tmp/hook",
        ):
            with self.subTest(url=url), self.assertRaises(ProviderError):
                validate_webhook_url(url, "webhook")

    def test_synthetic_event_is_not_presented_as_real_approval(self) -> None:
        self.assertEqual(self.event.event, "test")
        self.assertEqual(self.event.project_name, "test")
        rendered = self.event.render_text()
        self.assertIn("配置测试", rendered)
        self.assertNotIn("权限审批", rendered)
        self.assertNotIn("/tmp/demo", rendered)

    def test_provider_business_codes_require_integer_zero(self) -> None:
        accepted_cases = (
            ("feishu", {"code": 0}),
            ("feishu", {"StatusCode": 0}),
            ("wecom", {"errcode": 0}),
        )
        rejected_cases = (
            ("feishu", {"code": 1}),
            ("feishu", {}),
            ("feishu", {"code": False}),
            ("feishu", {"code": "0"}),
            ("wecom", {"errcode": 1}),
            ("wecom", {}),
            ("wecom", {"errcode": False}),
            ("wecom", {"errcode": "0"}),
        )
        for channel_type, payload in accepted_cases:
            with self.subTest(channel_type=channel_type, payload=payload):
                result = _provider_accepted(
                    channel_type,
                    200,
                    json.dumps(payload).encode("utf-8"),
                )
                self.assertTrue(result.success)
        for channel_type, payload in rejected_cases:
            with self.subTest(channel_type=channel_type, payload=payload):
                result = _provider_accepted(
                    channel_type,
                    200,
                    json.dumps(payload).encode("utf-8"),
                )
                self.assertFalse(result.success)
        invalid = _provider_accepted("feishu", 200, b"not-json")
        self.assertFalse(invalid.success)
        self.assertEqual(invalid.diagnostic, "provider_response_invalid")


if __name__ == "__main__":
    unittest.main()
