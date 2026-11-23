"""Display a spherical camera view over Zenoh and steer it unless it is fixed."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import pygame
import zenoh

from config import ViewerConfig, load_config
from node import (
    JSON_ENCODING,
    ViewDirection,
    parse_view_direction,
    view_direction_key,
    view_image_key,
)

DEFAULT_ZENOH_CONFIG_PATH = Path("examples/viewer-zenoh-config.json5")
DEFAULT_VIEWER_CONFIG_PATH = Path("examples/viewer-config.json5")
WINDOW_TITLE = "spherical-camera-zenoh-node"
INITIAL_WINDOW_SIZE = (640, 480)
DIRECTION_QUERY_TIMEOUT_SECONDS = 1.0
VIEW_IMAGE_TIMEOUT_SECONDS = 3.0
VIEW_IMAGE_POLL_INTERVAL_SECONDS = 0.05
KEY_REPEAT_DELAY_MS = 250
KEY_REPEAT_INTERVAL_MS = 50
EVENT_POLL_INTERVAL_MS = 10
FONT_SIZE = 24
TEXT_MARGIN = 8
TEXT_COLOR = (255, 255, 255)
TEXT_BACKGROUND_COLOR = (0, 0, 0)
QUIT_HELP_LINE = "Q / Esc: quit"
STEERABLE_VIEW_LINES = (
    "Left / Right: turn",
    "Up / Down: tilt",
    "R: face forward and level",
    QUIT_HELP_LINE,
)
FIXED_VIEW_LINES = ("Fixed view", QUIT_HELP_LINE)
DIRECTION_STEPS_BY_KEY = {
    pygame.K_LEFT: (1, 0),
    pygame.K_RIGHT: (-1, 0),
    pygame.K_UP: (0, -1),
    pygame.K_DOWN: (0, 1),
}
QUIT_KEYS = {pygame.K_q, pygame.K_ESCAPE}


def run(zenoh_config: zenoh.Config, viewer_config: ViewerConfig) -> None:
    zenoh.init_log_from_env_or("error")
    prefix = viewer_config.zenoh_key_prefix
    image_key = view_image_key(prefix, viewer_config.view_key)
    direction_key = view_direction_key(prefix, viewer_config.view_key)
    with (
        zenoh.open(zenoh_config) as session,
        session.declare_subscriber(
            image_key, zenoh.handlers.RingChannel(1)
        ) as subscriber,
    ):

        def request_direction(target: ViewDirection | None) -> ViewDirection | None:
            payload = None if target is None else target.model_dump_json()
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
            return None

        def attached_direction(sample: zenoh.Sample) -> ViewDirection:
            if sample.attachment is None:
                raise RuntimeError(f"'{image_key}' published a frame without direction")
            return parse_view_direction(sample.attachment.to_bytes())

        direction = request_direction(None)
        steerable = direction is not None
        sample: zenoh.Sample | None = None
        if direction is None:
            deadline = time.monotonic() + VIEW_IMAGE_TIMEOUT_SECONDS
            while (sample := subscriber.try_recv()) is None:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"no node answered on '{direction_key}' "
                        f"or published '{image_key}'"
                    )
                time.sleep(VIEW_IMAGE_POLL_INTERVAL_SECONDS)
            direction = attached_direction(sample)
            print(f"[INFO] Showing fixed view '{image_key}'.")
        else:
            print(f"[INFO] Showing '{image_key}', steering via '{direction_key}'.")
        view_lines = STEERABLE_VIEW_LINES if steerable else FIXED_VIEW_LINES

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
                if sample is not None:
                    jpeg = np.frombuffer(sample.payload.to_bytes(), dtype=np.uint8)
                    image = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
                    if image is not None:
                        if not steerable:
                            direction = attached_direction(sample)
                        height, width = image.shape[:2]
                        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                        image_surface = pygame.image.frombytes(
                            rgb.tobytes(), (width, height), "RGB"
                        )
                        if screen.get_size() != (width, height):
                            screen = pygame.display.set_mode((width, height))
                        needs_redraw = True

                for event in pygame.event.get():
                    if event.type == pygame.QUIT or (
                        event.type == pygame.KEYDOWN and event.key in QUIT_KEYS
                    ):
                        return
                    if event.type != pygame.KEYDOWN or not steerable:
                        continue
                    if event.key == pygame.K_r:
                        target = ViewDirection(yaw_deg=0.0, pitch_deg=0.0)
                    elif event.key in DIRECTION_STEPS_BY_KEY:
                        yaw_steps, pitch_steps = DIRECTION_STEPS_BY_KEY[event.key]
                        step_deg = viewer_config.direction_step_deg
                        target = ViewDirection(
                            yaw_deg=direction.yaw_deg + yaw_steps * step_deg,
                            pitch_deg=direction.pitch_deg + pitch_steps * step_deg,
                        )
                    else:
                        continue
                    direction = request_direction(target)
                    if direction is None:
                        raise RuntimeError(f"no node answered on '{direction_key}'")
                    needs_redraw = True

                if needs_redraw:
                    screen.fill(TEXT_BACKGROUND_COLOR)
                    if image_surface is not None:
                        screen.blit(image_surface, (0, 0))
                    text_y = TEXT_MARGIN
                    for line in (
                        f"Yaw: {direction.yaw_deg:+.1f} deg",
                        f"Pitch: {direction.pitch_deg:+.1f} deg",
                        *view_lines,
                    ):
                        text = font.render(
                            line, True, TEXT_COLOR, TEXT_BACKGROUND_COLOR
                        )
                        screen.blit(text, (TEXT_MARGIN, text_y))
                        text_y += text.get_height()
                    pygame.display.flip()
                    needs_redraw = False
                pygame.time.wait(EVENT_POLL_INTERVAL_MS)
                sample = subscriber.try_recv()
        finally:
            pygame.font.quit()
            pygame.display.quit()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Display a spherical camera view and steer it unless it is fixed.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--zenoh-config",
        type=Path,
        default=DEFAULT_ZENOH_CONFIG_PATH,
        metavar="FILE",
        help="Zenoh JSON5 configuration file.",
    )
    parser.add_argument(
        "--viewer-config",
        type=Path,
        default=DEFAULT_VIEWER_CONFIG_PATH,
        metavar="FILE",
        help="Viewer JSON5 configuration file.",
    )
    args = parser.parse_args(argv)

    try:
        run(
            zenoh.Config.from_file(args.zenoh_config),
            load_config(args.viewer_config, ViewerConfig),
        )
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
