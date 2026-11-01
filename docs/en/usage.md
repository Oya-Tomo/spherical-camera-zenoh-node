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
| `views.channels[].initial_yaw_deg` | Direction of the view at startup; defaults to `0` |
| `publisher.publish_frequency_hz` | Target output frequency, up to the camera's 15 fps |
| `publisher.congestion_control` | `drop`, `block`, or `block_first` |
| `publisher.reliability` | `best_effort` or `reliable` |

`equirectangular` and `views` are both optional, but at least one is required. Each captured frame is assigned to the nearest output slot of `publish_frequency_hz` and published once per slot, so a faster camera is downsampled evenly and late frames never cause a burst.

The THETA S resolves about 3 pixels per degree. A narrower `views.fov_deg` magnifies the image but adds no detail.

## Zenoh interface

| Key | Interaction | Payload |
| --- | --- | --- |
| `{prefix}/equirectangular` | Publication | 360° x 180° equirectangular JPEG (`image/jpeg`), forward at the horizontal center |
| `{prefix}/view/{view_key}` | Publication | Perspective JPEG (`image/jpeg`) with the JSON attachment `{"yaw_deg": <number>}` stating its direction |
| `{prefix}/view/{view_key}/direction` | Queryable | See below |

Directions use the body frame: yaw `0` is forward and positive yaw turns left. Every view looks horizontally.

A query on a direction key:

- without payload replies with the current direction, JSON (`application/json`) `{"yaw_deg": <number>}`;
- with the JSON payload `{"yaw_deg": <number>}` sets the direction and replies with the applied direction, normalized to `(-180, 180]`;
- with a malformed payload receives an error reply (`text/plain`) that states the reason, and the direction stays unchanged.

Frames captured after a successful set depict the new direction, which the image attachment confirms. Each view is controlled independently and the latest request wins, so give each client that needs its own direction a separate `view_key`.

```python
import json

import zenoh

with zenoh.open(zenoh.Config()) as session:
    replies = session.get(
        "spherical_camera/view/main/direction",
        payload=json.dumps({"yaw_deg": 90}),
        encoding="application/json",
        timeout=1.0,
    )
    for reply in replies:
        result = reply.ok or reply.err
        print(result.payload.to_string())
```

## Viewer

```console
$ uv run --group example examples/viewer.py spherical_camera/view/sub
```

| Key | Action |
| --- | --- |
| `Left` / `Right` | Turn the view by 5°; holding a key keeps turning |
| `R` | Face forward (yaw `0`) |
| `Q` / `Esc` | Quit |

The viewer reads the current direction from the node at startup, so connecting does not move the view, and draws the direction and the key operations on the window. `examples/viewer-zenoh-config.json5` connects to `tcp/127.0.0.1:7447`; pass `--zenoh-config` to reach another host.
