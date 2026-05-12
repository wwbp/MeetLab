import asyncio
import json
import os
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from livekit import api, rtc


def _json_request(
    base_url: str,
    path: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> Tuple[int, str, Optional[Dict[str, Any]]]:
    url = f"{base_url.rstrip('/')}{path}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = urllib.request.Request(url, data=payload, method=method, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            return response.status, text, json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8")
        parsed = None
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        return error.code, text, parsed


async def _json_request_async(
    base_url: str,
    path: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
) -> Tuple[int, str, Optional[Dict[str, Any]]]:
    return await asyncio.to_thread(_json_request, base_url, path, method, body)


def _mock_user_token(room_name: str, identity: str, api_key: str, api_secret: str) -> str:
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("mock-user")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(minutes=60))
        .to_jwt()
    )


class TestBotLongevityMinimal(unittest.IsolatedAsyncioTestCase):
    async def test_measure_time_until_bot_drops(self) -> None:
        if os.getenv("RUN_BOT_LONGEVITY_TEST", "").strip() != "1":
            self.skipTest("Set RUN_BOT_LONGEVITY_TEST=1 to run this long test")

        base_url = os.getenv("CONCIERGE_BASE_URL", "http://web-client:3000")
        livekit_url = os.getenv("LIVEKIT_URL", "ws://transport-server:7880")
        api_key = os.getenv("LIVEKIT_API_KEY")
        api_secret = os.getenv("LIVEKIT_API_SECRET")
        self.assertTrue(api_key, "LIVEKIT_API_KEY must be set")
        self.assertTrue(api_secret, "LIVEKIT_API_SECRET must be set")

        max_seconds = int(os.getenv("BOT_LONGEVITY_MAX_SECONDS", "1200"))
        poll_seconds = float(os.getenv("BOT_LONGEVITY_POLL_SECONDS", "5"))
        message_seconds = float(os.getenv("BOT_LONGEVITY_MESSAGE_SECONDS", "10"))

        room_name = f"longevity-{int(time.time())}-{uuid4().hex[:8]}"
        mock_identity = f"mock_user_{uuid4().hex[:8]}"
        encoded_room = urllib.parse.quote(room_name, safe="")

        room = rtc.Room()
        bot_identity: Optional[str] = None
        dropped_after: Optional[float] = None
        events_tail: Any = None
        room_created = False
        start = time.monotonic()
        next_message = start

        try:
            status, text, _ = await _json_request_async(
                base_url, "/api/concierge/rooms", method="POST", body={"name": room_name}
            )
            self.assertEqual(status, 201, f"room create failed: {status} {text}")
            room_created = True

            status, text, data = await _json_request_async(
                base_url, f"/api/concierge/rooms/{encoded_room}/bots", method="POST"
            )
            self.assertEqual(status, 200, f"bot start failed: {status} {text}")
            bot_identity = (data or {}).get("request", {}).get("botIdentity")
            self.assertTrue(bot_identity, f"missing bot identity: {text}")

            token = _mock_user_token(room_name, mock_identity, api_key or "", api_secret or "")
            await room.connect(livekit_url, token)

            while True:
                now = time.monotonic()
                elapsed = now - start
                if elapsed > max_seconds:
                    break

                if now >= next_message:
                    message = {
                        "timestamp": int(time.time() * 1000),
                        "message": f"health_ping_{int(elapsed)}",
                    }
                    await room.local_participant.publish_data(json.dumps(message))
                    next_message = now + message_seconds

                status, text, data = await _json_request_async(
                    base_url, f"/api/concierge/rooms/{encoded_room}/bots", method="GET"
                )
                self.assertEqual(status, 200, f"bots read failed: {status} {text}")
                bots = (data or {}).get("bots", []) if isinstance(data, dict) else []
                bot_present = any(
                    isinstance(entry, dict) and entry.get("identity") == bot_identity for entry in bots
                )
                if not bot_present:
                    dropped_after = elapsed
                    break

                await asyncio.sleep(poll_seconds)

            status, text, data = await _json_request_async(base_url, "/api/concierge/events?limit=30")
            if status == 200 and isinstance(data, dict):
                events_tail = data.get("events")

        finally:
            if room.connection_state != rtc.ConnectionState.CONN_DISCONNECTED:
                await room.disconnect()
            if room_created:
                await _json_request_async(base_url, f"/api/concierge/rooms/{encoded_room}", method="DELETE")

        if dropped_after is None:
            print(f"Bot stayed connected for {max_seconds}s (no drop seen). room={room_name}")
            return

        self.fail(
            f"Bot dropped after {dropped_after:.1f}s. room={room_name} bot={bot_identity} "
            f"events_tail={json.dumps(events_tail, default=str)[:1600]}"
        )

