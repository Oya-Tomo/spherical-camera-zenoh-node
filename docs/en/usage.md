# Usage

## Running the node

```console
$ uv run node.py
```

Press `Ctrl-C` to stop. The node exits with a nonzero status when the configuration is invalid, the camera cannot be opened, or the camera fails 10 consecutive reads. It does not reconnect to an unplugged camera; use a process supervisor when automatic restart is needed.

## Node configuration

See [`config/node-config.example.json5`](../../config/node-config.example.json5) for a complete example. Unknown, missing, duplicate, or mistyped fields are rejected at startup. [Setup](setup.md) explains `source`, `mount`, and `lenses`.

| Key | Description |
| --- | --- |
| `zenoh_key_prefix` | Concrete Zenoh key under which every channel is published |
| `equirectangular.size` | Output `[width, height]` with a 2:1 aspect ratio |
| `equirectangular.jpeg_quality` | JPEG quality from 0 to 100 |
| `views.fov_deg` | Horizontal field of view of every view, below 180° |
| `views.size` | Output `[width, height]` of every view |
| `views.jpeg_quality` | JPEG quality from 0 to 100 |
| `views.channels[].view_key` | Unique single key segment naming the view |
| `views.channels[].direction` | Direction of the view: the startup direction of a steerable view, the permanent direction of a fixed view; defaults to forward and level (`{yaw_deg: 0, pitch_deg: 0}`) for both kinds |
| `views.channels[].direction.yaw_deg` | Horizontal direction, `0` for forward and positive to the left; any finite value, normalized to `(-180, 180]` |
| `views.channels[].direction.pitch_deg` | Vertical direction, `0` for level and positive downward, from `-90` to `90` |
| `views.channels[].fixed` | `true` for a fixed view, `false` for a steerable view; defaults to `false` |
| `publisher.publish_frequency_hz` | Target output frequency, up to the camera's 15 fps |
| `publisher.congestion_control` | `drop`, `block`, or `block_first` |
| `publisher.reliability` | `best_effort` or `reliable` |

A steerable view starts at its `direction` and is turned through its direction queryable. A fixed view has no direction queryable and always depicts its `direction`. A `direction` object needs both `yaw_deg` and `pitch_deg` and uses the reference frame and signs of the direction queryable described below. Steerable and fixed views can be mixed freely in `channels`.

`equirectangular` and `views` are both optional, but at least one is required. Each captured frame is assigned to the nearest output slot of `publish_frequency_hz` and published once per slot, so a faster camera is downsampled evenly and late frames never cause a burst.

The THETA S resolves about 3 pixels per degree. A narrower `views.fov_deg` magnifies the image but adds no detail.

## Zenoh interface

| Key | Interaction | Payload |
| --- | --- | --- |
| `{prefix}/equirectangular` | Publication | 360° x 180° equirectangular JPEG (`image/jpeg`), forward at the horizontal center |
| `{prefix}/view/{view_key}` | Publication | Perspective JPEG (`image/jpeg`) in the current direction of a steerable view or the configured direction of a fixed view, with the JSON attachment `{"yaw_deg": <number>, "pitch_deg": <number>}` stating the direction it depicts |
| `{prefix}/view/{view_key}/direction` | Queryable | Steerable views only; see below |

A view direction is a yaw and a pitch in the reference frame (x forward, y left, z up), with the same signs as [`mount`](setup.md#mount-orientation):

- `yaw_deg` is `0` for forward and positive to the left. Any finite value is accepted and normalized to `(-180, 180]`.
- `pitch_deg` is `0` for level and positive downward. Any finite value is accepted and clamped to `[-90, 90]`.
- Views do not roll, so the horizon stays level in every view image.

A query on a direction key:

- without payload replies with the current direction, JSON (`application/json`) `{"yaw_deg": <number>, "pitch_deg": <number>}`;
- with the JSON payload `{"yaw_deg": <number>, "pitch_deg": <number>}` sets the direction and replies with the applied direction;
- with a malformed payload, such as one missing a field, with an extra field, or with a non-finite value, receives an error reply (`text/plain`) that states the reason, and the direction stays unchanged.

Frames captured after a successful set depict the new direction, which the image attachment confirms. Each view is controlled independently and the latest request wins, so give each client that needs its own direction a separate `view_key`.

A fixed view declares no direction queryable, so a query on its direction key receives no reply. Its frames carry the same attachment, which always states the configured direction.

```python
import json

import zenoh

with zenoh.open(zenoh.Config()) as session:
    replies = session.get(
        "spherical_camera/view/main/direction",
        payload=json.dumps({"yaw_deg": 90, "pitch_deg": 30}),
        encoding="application/json",
        timeout=1.0,
    )
    for reply in replies:
        result = reply.ok or reply.err
        print(result.payload.to_string())
```

## Viewer

```console
$ uv run --group example examples/viewer.py
```

| Option | Default |
| --- | --- |
| `--viewer-config FILE` | `examples/viewer-config.json5` |
| `--zenoh-config FILE` | `examples/viewer-zenoh-config.json5` |

The view is selected by the viewer configuration alone. See [`examples/viewer-config.example.json5`](../../examples/viewer-config.example.json5) for a complete example. Unknown, missing, duplicate, or mistyped fields are rejected at startup. [Setup](setup.md#zenoh-connection) explains the Zenoh connection of the viewer.

| Key | Description |
| --- | --- |
| `zenoh_key_prefix` | Concrete Zenoh key under which the node publishes; must match the node |
| `view_key` | Single key segment naming the view to display |
| `direction_step_deg` | Angle of one turn or tilt step, greater than `0` and at most `90` |

The viewer displays `{zenoh_key_prefix}/view/{view_key}` and decides at startup whether it is steerable or fixed:

| At startup | The viewer |
| --- | --- |
| The node answers on `{zenoh_key_prefix}/view/{view_key}/direction` | Displays the view and steers it with the keys through that key |
| The node publishes the view within 3 seconds but does not answer on the direction key | Displays the view as a fixed view |
| The node neither answers on the direction key nor publishes the view | Stops with an error that names the view |

| Key | Action |
| --- | --- |
| `Left` / `Right` | Turn the view by `direction_step_deg`; holding a key keeps turning |
| `Up` / `Down` | Tilt the view by `direction_step_deg` up to straight up or down; holding a key keeps tilting |
| `R` | Face forward and level (yaw `0`, pitch `0`) |
| `Q` / `Esc` | Quit |

For a steerable view, the viewer reads the current direction from the node at startup, so connecting does not move the view, and draws the current yaw, pitch, and key operations on the window.

For a fixed view, the turn, tilt, and face-forward keys do nothing and the viewer sends no direction request. The window shows the yaw and pitch stated by the attachment of the displayed frame, states that the view is fixed, and lists only the quit keys.
