# Setup

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- A RICOH THETA S connected over USB
- A desktop display for the viewer example

```console
$ uv sync
```

## Configuration files

```console
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json5 config/node-config.json5
```

| File | Purpose |
| --- | --- |
| `config/zenoh-config.json5` | Zenoh mode, endpoints, and scouting |
| `config/node-config.json5` | Camera source, mount, lens geometry, outputs, and publisher QoS |

Both runtime files are ignored by Git. The node reads them from the current working directory, so start it from the repository root or pass `--zenoh-config` and `--node-config`.

## Live streaming mode

The THETA S is a webcam only in live streaming mode, as described in the official [THETA S user guide](https://theta-archive.ricoh360.com/support.theta360.com/en/manual/s/content/streaming/streaming_01.html):

1. Turn the camera off.
2. Press the shooting mode button and the power button together. The video icon and the `LIVE` icon light up.
3. Connect the camera with the supplied USB cable.

Linux then recognizes the camera as a UVC device that delivers 1280x720 MotionJPEG at 15 fps, with the two fisheye images side by side. In the normal mode the camera appears only as a still image device and no `/dev/video*` node is created.

The camera runs on its battery while streaming. The battery runs down when the USB port supplies less than 1.5 A, so power the camera from a port that supplies at least 1.5 A.

## Camera source

```console
$ ls -l /dev/v4l/by-id/
```

Set `source` to the `video-index0` link of the THETA S, or to an OpenCV camera index:

```json5
source: "/dev/v4l/by-id/usb-Ricoh_Company__Ltd._RICOH_THETA_S_00250209-video-index0",
```

## Lens geometry

`lenses` describes where each fisheye circle lies in the 1280x720 source frame. The example contains values measured on one THETA S. Other units differ by a few pixels, so check them on each camera.

| Key | Meaning |
| --- | --- |
| `center` | Pixel center of the circle |
| `radius` | Radius of the usable image circle, inside the dark ring of the lens barrel |
| `fov_deg` | Field of view that `radius` covers; the lens is modeled as equidistant |
| `rotation_deg` | Counterclockwise rotation of the image inside the circle |

`front` is one lens and `back` the opposite lens. When the THETA S stands upright, its image circles are rotated by about +90° (left circle) and −90° (right circle).

To check the geometry, start the node and turn a view to a seam with the viewer. The seams lie 90° to the left and right of the front lens. Adjust `center`, `fov_deg`, and `rotation_deg` until objects across a seam line up without gaps or duplicated regions.

## Mount orientation

`mount` is the orientation of the camera relative to the body frame (x forward, y left, z up). `roll_deg`, `pitch_deg`, and `yaw_deg` are applied in the order yaw, pitch, roll about the body axes (REP-103). The camera frame has x along the front lens and z toward the top of the camera.

- Positive `yaw_deg` turns the front lens to the left.
- Positive `pitch_deg` tilts the front lens down.
- Positive `roll_deg` tilts the top of the camera to the right.

Set `mount` so that a view facing yaw `0` looks forward with a level horizon.

## Zenoh connection

The example runs as a peer that listens on TCP port 7447 and uses multicast scouting. `0.0.0.0` accepts connections on every local IPv4 interface; remote peers connect to an address of this host, such as `tcp/192.168.1.20:7447`. To join an existing router instead, use `mode: "client"` and list the router in `connect.endpoints`. See the [Zenoh deployment guide](https://zenoh.io/docs/getting-started/deployment/).

## Development checks

```console
$ uv sync --all-groups
$ uv run ruff format --check .
$ uv run ruff check .
$ uv run pyright
$ uv run python -m unittest discover -v
```
