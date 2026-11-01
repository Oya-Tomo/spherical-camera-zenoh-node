"""Publish equirectangular and perspective views of a RICOH THETA S over Zenoh."""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from threading import Lock
from typing import Annotated, NoReturn

import cv2
import zenoh
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config import (
    DualFisheyeLenses,
    MountConfig,
    NodeConfig,
    ViewsConfig,
    load_node_config,
)
from projection import (
    RemapTable,
    build_remap_table,
    equirectangular_rays,
    perspective_rays,
)

JPEG_ENCODING = "image/jpeg"
JSON_ENCODING = "application/json"
TEXT_ENCODING = "text/plain"
DEFAULT_ZENOH_CONFIG_PATH = Path("config/zenoh-config.json5")
DEFAULT_NODE_CONFIG_PATH = Path("config/node-config.json5")
MAX_CONSECUTIVE_CAPTURE_FAILURES = 10
CAPTURE_RETRY_DELAY_SECONDS = 0.1
CONGESTION_CONTROLS = {
    "drop": zenoh.CongestionControl.DROP,
    "block": zenoh.CongestionControl.BLOCK,
    "block_first": zenoh.CongestionControl.BLOCK_FIRST,
}
RELIABILITIES = {
    "best_effort": zenoh.Reliability.BEST_EFFORT,
    "reliable": zenoh.Reliability.RELIABLE,
}


class ViewDirection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    yaw_deg: Annotated[float, Field(allow_inf_nan=False)]


def normalize_yaw_deg(yaw_deg: float) -> float:
    wrapped_yaw_deg = math.fmod(yaw_deg, 360.0)
    if wrapped_yaw_deg <= -180.0:
        return wrapped_yaw_deg + 360.0
    if wrapped_yaw_deg > 180.0:
        return wrapped_yaw_deg - 360.0
    return wrapped_yaw_deg


def encode_view_direction(yaw_deg: float) -> bytes:
    return ViewDirection(yaw_deg=yaw_deg).model_dump_json().encode()


def parse_view_direction(payload: bytes) -> float:
    try:
        return ViewDirection.model_validate_json(payload).yaw_deg
    except ValidationError as error:
        reasons = (
            f"{'.'.join(map(str, detail['loc'])) or 'payload'}: {detail['msg']}"
            for detail in error.errors(include_url=False)
        )
        raise ValueError("; ".join(reasons)) from error


def view_direction_key(view_image_key: str) -> str:
    return f"{view_image_key}/direction"


def encode_jpeg(image: cv2.typing.MatLike, quality: int) -> bytes:
    encoded, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not encoded:
        raise RuntimeError("failed to encode image as JPEG")
    return jpeg.tobytes()


class ViewChannel:
    def __init__(
        self,
        initial_yaw_deg: float,
        views: ViewsConfig,
        lenses: DualFisheyeLenses,
        mount: MountConfig,
        publisher: zenoh.Publisher,
        direction_key: str,
    ) -> None:
        self._views = views
        self._lenses = lenses
        self._mount = mount
        self._publisher = publisher
        self._direction_key = direction_key
        self._lock = Lock()
        self._yaw_deg = normalize_yaw_deg(initial_yaw_deg)
        self._remap_yaw_deg: float | None = None
        self._remap_table: RemapTable | None = None

    def answer_direction_query(self, query: zenoh.Query) -> None:
        if query.payload is None:
            with self._lock:
                yaw_deg = self._yaw_deg
        else:
            try:
                requested_yaw_deg = parse_view_direction(query.payload.to_bytes())
            except ValueError as error:
                query.reply_err(str(error), encoding=TEXT_ENCODING)
                return
            yaw_deg = normalize_yaw_deg(requested_yaw_deg)
            with self._lock:
                self._yaw_deg = yaw_deg
        query.reply(
            self._direction_key, encode_view_direction(yaw_deg), encoding=JSON_ENCODING
        )

    def publish(self, frame: cv2.typing.MatLike) -> None:
        with self._lock:
            yaw_deg = self._yaw_deg
        if self._remap_table is None or yaw_deg != self._remap_yaw_deg:
            self._remap_table = build_remap_table(
                perspective_rays(self._views.size, self._views.fov_deg, yaw_deg),
                self._lenses,
                self._mount,
            )
            self._remap_yaw_deg = yaw_deg
        self._publisher.put(
            encode_jpeg(self._remap_table.apply(frame), self._views.jpeg_quality),
            attachment=encode_view_direction(yaw_deg),
        )


def publish_frames(
    capture: cv2.VideoCapture,
    publishers: Sequence[Callable[[cv2.typing.MatLike], None]],
    *,
    frequency_hz: float,
    clock: Callable[[], float] = time.monotonic,
) -> NoReturn:
    started_at: float | None = None
    last_published_slot = -1
    consecutive_failures = 0
    while True:
        success, frame = capture.read()
        if not success:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_CAPTURE_FAILURES:
                raise RuntimeError(
                    f"camera read failed {consecutive_failures} consecutive times"
                )
            time.sleep(CAPTURE_RETRY_DELAY_SECONDS)
            continue
        consecutive_failures = 0

        captured_at = clock()
        if started_at is None:
            started_at = captured_at
        slot = math.floor((captured_at - started_at) * frequency_hz + 0.5)
        if slot <= last_published_slot:
            continue
        last_published_slot = slot
        for publish in publishers:
            publish(frame)


def run(zenoh_config: zenoh.Config, node_config: NodeConfig) -> NoReturn:
    zenoh.init_log_from_env_or("error")
    prefix = node_config.zenoh_key_prefix
    publisher_config = node_config.publisher

    print("[INFO] Open Zenoh session...")
    with zenoh.open(zenoh_config) as session, ExitStack() as resources:

        def declare_image_publisher(key_expression: str) -> zenoh.Publisher:
            return resources.enter_context(
                session.declare_publisher(
                    key_expression,
                    encoding=JPEG_ENCODING,
                    congestion_control=CONGESTION_CONTROLS[
                        publisher_config.congestion_control
                    ],
                    reliability=RELIABILITIES[publisher_config.reliability],
                )
            )

        print(f"[INFO] Open camera {node_config.source!r}...")
        capture = cv2.VideoCapture(node_config.source)
        resources.callback(capture.release)
        if not capture.isOpened():
            raise RuntimeError(f"could not open camera {node_config.source!r}")

        publishers: list[Callable[[cv2.typing.MatLike], None]] = []
        if (equirectangular := node_config.equirectangular) is not None:
            equirectangular_key = f"{prefix}/equirectangular"
            equirectangular_publisher = declare_image_publisher(equirectangular_key)
            equirectangular_table = build_remap_table(
                equirectangular_rays(equirectangular.size),
                node_config.lenses,
                node_config.mount,
            )
            jpeg_quality = equirectangular.jpeg_quality

            def publish_equirectangular(frame: cv2.typing.MatLike) -> None:
                image = equirectangular_table.apply(frame)
                equirectangular_publisher.put(encode_jpeg(image, jpeg_quality))

            publishers.append(publish_equirectangular)
            print(
                f"[INFO] Publishing equirectangular images on '{equirectangular_key}'."
            )
        if (views := node_config.views) is not None:
            for view in views.channels:
                image_key = f"{prefix}/view/{view.view_key}"
                direction_key = view_direction_key(image_key)
                view_channel = ViewChannel(
                    view.initial_yaw_deg,
                    views,
                    node_config.lenses,
                    node_config.mount,
                    declare_image_publisher(image_key),
                    direction_key,
                )
                resources.enter_context(
                    session.declare_queryable(
                        direction_key, view_channel.answer_direction_query
                    )
                )
                publishers.append(view_channel.publish)
                print(
                    f"[INFO] Publishing view images on '{image_key}' "
                    f"with direction control on '{direction_key}'."
                )

        print(
            f"[INFO] Publishing at {publisher_config.publish_frequency_hz:g} Hz. "
            "Press CTRL-C to quit."
        )
        publish_frames(
            capture, publishers, frequency_hz=publisher_config.publish_frequency_hz
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spherical-camera-zenoh-node",
        description="Capture a RICOH THETA S and publish its views over Zenoh.",
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
        "--node-config",
        type=Path,
        default=DEFAULT_NODE_CONFIG_PATH,
        metavar="FILE",
        help="Node JSON5 configuration file.",
    )
    args = parser.parse_args(argv)

    try:
        run(
            zenoh.Config.from_file(args.zenoh_config),
            load_node_config(args.node_config),
        )
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
    except (RuntimeError, ValueError, OSError, cv2.error, zenoh.ZError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
