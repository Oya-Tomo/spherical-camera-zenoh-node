import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pygame

from examples import viewer
from node import encode_view_direction, parse_view_direction

os.environ["SDL_VIDEODRIVER"] = "dummy"
INITIAL_YAW_DEG = 30.0


def key_down(key: int) -> list[pygame.event.Event]:
    return [pygame.event.Event(pygame.KEYDOWN, key=key)]


def image_sample(width: int, height: int) -> MagicMock:
    _, jpeg = cv2.imencode(".jpg", np.zeros((height, width, 3), dtype=np.uint8))
    sample = MagicMock()
    sample.payload.to_bytes.return_value = jpeg.tobytes()
    return sample


class ViewerRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = MagicMock()
        self.requested_payloads: list[bytes | None] = []
        self.session.get.side_effect = self.answer_direction_query
        subscriber = self.session.declare_subscriber.return_value.__enter__.return_value
        subscriber.try_recv.side_effect = [image_sample(64, 48)] + [None] * 100

    def answer_direction_query(
        self, key: str, *, payload: bytes | None, **_: object
    ) -> list[MagicMock]:
        self.requested_payloads.append(payload)
        yaw_deg = INITIAL_YAW_DEG if payload is None else parse_view_direction(payload)
        reply = MagicMock(err=None)
        reply.ok.payload.to_bytes.return_value = encode_view_direction(yaw_deg)
        return [reply]

    def run_viewer(
        self, events: list[list[pygame.event.Event]]
    ) -> tuple[MagicMock, MagicMock]:
        with (
            redirect_stdout(StringIO()),
            patch("examples.viewer.zenoh.open") as open_session,
            patch("examples.viewer.pygame.event.get", side_effect=events),
            patch(
                "examples.viewer.pygame.display.set_mode", wraps=pygame.display.set_mode
            ) as set_mode,
            patch("examples.viewer.pygame.font.Font") as font,
        ):
            open_session.return_value.__enter__.return_value = self.session
            font.return_value.render.return_value = pygame.Surface((1, 1))
            viewer.run(MagicMock(), "camera/view/sub")
        return set_mode, font.return_value.render

    def test_keys_turn_the_view_through_direction_queries(self) -> None:
        events = [
            [],
            key_down(pygame.K_LEFT),
            key_down(pygame.K_RIGHT),
            key_down(pygame.K_RIGHT),
            key_down(pygame.K_r),
            key_down(pygame.K_q),
        ]

        _, render = self.run_viewer(events)

        self.assertEqual(
            [
                None if payload is None else parse_view_direction(payload)
                for payload in self.requested_payloads
            ],
            [None, 35.0, 30.0, 25.0, 0.0],
        )
        self.assertEqual(
            {call.args[0] for call in self.session.get.call_args_list},
            {"camera/view/sub/direction"},
        )
        rendered_lines = [call.args[0] for call in render.call_args_list]
        self.assertIn("Yaw: +25.0 deg", rendered_lines)
        self.assertIn("Left / Right: turn", rendered_lines)

    def test_window_follows_the_received_image_and_closes(self) -> None:
        set_mode, _ = self.run_viewer([[], [pygame.event.Event(pygame.QUIT)]])

        self.assertEqual(set_mode.call_args.args[0], (64, 48))
        self.assertFalse(pygame.display.get_init())

    def test_unanswered_and_failed_direction_queries_stop_the_viewer(self) -> None:
        failed_reply = MagicMock(ok=None)
        failed_reply.err.payload.to_string.return_value = "bad yaw"
        for replies, message in (([], "no node answered"), ([failed_reply], "bad yaw")):
            self.session.get.side_effect = None
            self.session.get.return_value = replies
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                self.run_viewer([])


class ViewerMainTests(unittest.TestCase):
    def test_runtime_errors_exit_with_status_one(self) -> None:
        stderr = StringIO()
        with (
            redirect_stderr(stderr),
            patch("examples.viewer.run", side_effect=RuntimeError("no node")),
        ):
            exit_code = viewer.main(["camera/view/sub"])

        self.assertEqual(exit_code, 1)
        self.assertIn("[ERROR] no node", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
