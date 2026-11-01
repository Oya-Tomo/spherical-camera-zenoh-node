"""Display a spherical camera view over Zenoh and turn it with the keyboard."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import pygame
import zenoh

from node import (
    JSON_ENCODING,
    encode_view_direction,
    normalize_yaw_deg,
    parse_view_direction,
    view_direction_key,
)

DEFAULT_ZENOH_CONFIG_PATH = Path(__file__).with_name("viewer-zenoh-config.json5")
WINDOW_TITLE = "spherical-camera-zenoh-node"
INITIAL_WINDOW_SIZE = (640, 480)
DIRECTION_QUERY_TIMEOUT_SECONDS = 1.0
YAW_STEP_DEG = 5.0
KEY_REPEAT_DELAY_MS = 250
KEY_REPEAT_INTERVAL_MS = 50
EVENT_POLL_INTERVAL_MS = 10
FONT_SIZE = 24
TEXT_MARGIN = 8
TEXT_COLOR = (255, 255, 255)
TEXT_BACKGROUND_COLOR = (0, 0, 0)
KEY_HELP_LINES = ("Left / Right: turn", "R: face forward", "Q / Esc: quit")
YAW_STEPS_BY_KEY = {pygame.K_LEFT: YAW_STEP_DEG, pygame.K_RIGHT: -YAW_STEP_DEG}
QUIT_KEYS = {pygame.K_q, pygame.K_ESCAPE}


def run(zenoh_config: zenoh.Config, view_image_key: str) -> None:
    zenoh.init_log_from_env_or("error")
    direction_key = view_direction_key(view_image_key)
    with (
        zenoh.open(zenoh_config) as session,
        session.declare_subscriber(
            view_image_key, zenoh.handlers.RingChannel(1)
        ) as subscriber,
    ):

        def request_direction(payload: bytes | None) -> float:
            replies = session.get(
                direction_key,
                payload=payload,
                encoding=None if payload is None else JSON_ENCODING,
                timeout=DIRECTION_QUERY_TIMEOUT_SECONDS,
            )
            for reply in replies:
                if reply.ok is not None:
                    return parse_view_direction(reply.ok.payload.to_bytes())
                if reply.err is not None:
                    raise RuntimeError(
                        f"direction request failed: {reply.err.payload.to_string()}"
                    )
            raise RuntimeError(f"no node answered on '{direction_key}'")

        yaw_deg = request_direction(None)
        print(f"[INFO] Showing '{view_image_key}', turning via '{direction_key}'.")

        pygame.display.init()
        pygame.font.init()
        try:
            pygame.display.set_caption(WINDOW_TITLE)
            screen = pygame.display.set_mode(INITIAL_WINDOW_SIZE)
            pygame.key.set_repeat(KEY_REPEAT_DELAY_MS, KEY_REPEAT_INTERVAL_MS)
            font = pygame.font.Font(None, FONT_SIZE)
            image_surface: pygame.Surface | None = None
            needs_redraw = True
            while True:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT or (
                        event.type == pygame.KEYDOWN and event.key in QUIT_KEYS
                    ):
                        return
                    if event.type != pygame.KEYDOWN:
                        continue
                    if event.key == pygame.K_r:
                        yaw_deg = request_direction(encode_view_direction(0.0))
                        needs_redraw = True
                    elif (yaw_step_deg := YAW_STEPS_BY_KEY.get(event.key)) is not None:
                        target_yaw_deg = normalize_yaw_deg(yaw_deg + yaw_step_deg)
                        yaw_deg = request_direction(
                            encode_view_direction(target_yaw_deg)
                        )
                        needs_redraw = True

                sample = subscriber.try_recv()
                if sample is not None:
                    jpeg = np.frombuffer(sample.payload.to_bytes(), dtype=np.uint8)
                    image = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
                    if image is not None:
                        height, width = image.shape[:2]
                        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                        image_surface = pygame.image.frombytes(
                            rgb.tobytes(), (width, height), "RGB"
                        )
                        if screen.get_size() != (width, height):
                            screen = pygame.display.set_mode((width, height))
                        needs_redraw = True

                if needs_redraw:
                    screen.fill(TEXT_BACKGROUND_COLOR)
                    if image_surface is not None:
                        screen.blit(image_surface, (0, 0))
                    text_y = TEXT_MARGIN
                    for line in (f"Yaw: {yaw_deg:+.1f} deg", *KEY_HELP_LINES):
                        text = font.render(
                            line, True, TEXT_COLOR, TEXT_BACKGROUND_COLOR
                        )
                        screen.blit(text, (TEXT_MARGIN, text_y))
                        text_y += text.get_height()
                    pygame.display.flip()
                    needs_redraw = False
                pygame.time.wait(EVENT_POLL_INTERVAL_MS)
        finally:
            pygame.font.quit()
            pygame.display.quit()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Display a spherical camera view and turn it with the keyboard.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "view_image_key",
        help="Zenoh key of a view, such as spherical_camera/view/operator.",
    )
    parser.add_argument(
        "--zenoh-config",
        type=Path,
        default=DEFAULT_ZENOH_CONFIG_PATH,
        metavar="FILE",
        help="Zenoh JSON5 configuration file.",
    )
    args = parser.parse_args(argv)

    try:
        run(zenoh.Config.from_file(args.zenoh_config), args.view_image_key)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
    except (
        RuntimeError,
        ValueError,
        OSError,
        cv2.error,
        pygame.error,
        zenoh.ZError,
    ) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
