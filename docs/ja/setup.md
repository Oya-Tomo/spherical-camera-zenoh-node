# セットアップ

## 必要なもの

- Python 3.13と[uv](https://docs.astral.sh/uv/)
- USBで接続したRICOH THETA S
- viewer exampleを使う場合はdesktop display

```console
$ uv sync
```

## 設定ファイル

```console
$ cp config/zenoh-config.example.json5 config/zenoh-config.json5
$ cp config/node-config.example.json5 config/node-config.json5
```

| ファイル | 用途 |
| --- | --- |
| `config/zenoh-config.json5` | Zenohのmode、endpoint、scouting |
| `config/node-config.json5` | カメラsource、取り付け向き、レンズ形状、出力、publisher QoS |

どちらの実行時ファイルもGitの管理対象外です。ノードはcurrent working directoryから読み込むため、リポジトリrootで起動するか、`--zenoh-config`と`--node-config`を指定してください。

## ライブストリーミングモード

THETA Sはライブストリーミングモードのときだけwebカメラになります。手順は公式の[THETA Sユーザーガイド](https://theta-archive.ricoh360.com/support.theta360.com/en/manual/s/content/streaming/streaming_01.html)のとおりです。

1. カメラの電源を切ります。
2. 撮影モードボタンと電源ボタンを同時に押して電源を入れます。ビデオアイコンと`LIVE`アイコンが点灯します。
3. 付属のUSBケーブルで接続します。

LinuxはカメラをMotionJPEG 1280x720・15 fpsのUVC deviceとして認識し、2つの魚眼画像が左右に並んだframeが届きます。通常モードで接続した場合は静止画deviceとしてだけ認識され、`/dev/video*`は作られません。

配信中のカメラはバッテリーでも動作します。USBの供給電力が1.5A未満だとバッテリーが減っていくため、1.5A以上を供給できるportから給電してください。

## カメラsource

```console
$ ls -l /dev/v4l/by-id/
```

`source`にはTHETA Sの`video-index0` link、またはOpenCVのカメラindexを設定します。

```json5
source: "/dev/v4l/by-id/usb-Ricoh_Company__Ltd._RICOH_THETA_S_00250209-video-index0",
```

## レンズ形状

`lenses`は、1280x720のsource frame内で各魚眼円がどこにあるかを表します。exampleには1台のTHETA Sで測った値が入っています。個体によって数pixel異なるため、カメラごとに確認してください。

| キー | 意味 |
| --- | --- |
| `center` | 円の中心pixel |
| `radius` | 使える画像円の半径。外周にあるレンズ鏡筒の暗い輪の内側まで |
| `fov_deg` | `radius`が覆う画角。レンズは等距離射影としてモデル化します |
| `rotation_deg` | 円の中の画像の反時計回りの回転 |

`front`は片方のレンズ、`back`は反対側のレンズです。THETA Sを縦に立てると、画像円は左が約+90°、右が約−90°回転しています。

確認するには、ノードを起動してviewerで視線を継ぎ目に向けます。継ぎ目はfrontレンズの左右90°の方向にあります。継ぎ目をまたぐ物体が隙間や重複なくつながるまで、`center`、`fov_deg`、`rotation_deg`を調整します。

## 取り付け向き

`mount`は、基準座標系（x前、y左、z上）に対するカメラの向きです。`roll_deg`、`pitch_deg`、`yaw_deg`は、基準座標系の軸まわりにyaw、pitch、rollの順で適用します（REP-103）。カメラ座標系はfrontレンズの方向がx、カメラの上方向がzです。

- `yaw_deg`が正だとfrontレンズは左を向きます。
- `pitch_deg`が正だとfrontレンズは下を向きます。
- `roll_deg`が正だとカメラの上側は右に傾きます。

yaw `0`、pitch `0`のviewが前方を水平に向くよう`mount`を設定します。

## Zenohの接続

exampleはTCP port 7447でlistenし、multicast scoutingを使うpeerとして動作します。`0.0.0.0`はすべてのローカルIPv4 interfaceで接続を受け付ける指定です。相手側はこのhostに到達できるaddress（例: `tcp/192.168.1.20:7447`）へ接続します。既存のrouterへ接続する場合は`mode: "client"`にし、`connect.endpoints`にrouterを指定します。詳しくは[Zenoh deployment guide](https://zenoh.io/docs/getting-started/deployment/)を参照してください。

## 開発時の検証

```console
$ uv sync --all-groups
$ uv run ruff format --check .
$ uv run ruff check .
$ uv run pyright
$ uv run python -m unittest discover -v
```
