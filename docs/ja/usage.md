# 使い方

## ノードの起動

```console
$ uv run node.py
```

`Ctrl-C`で停止します。設定が不正なとき、カメラを開けないとき、カメラの読み込みが10回連続で失敗したとき、ノードは非0 statusで終了します。抜けたカメラへの再接続は行いません。自動restartが必要な場合はprocess supervisorを使ってください。

## ノード設定

完全な例は[`config/node-config.example.json5`](../../config/node-config.example.json5)にあります。未知、欠落、重複、型が不正なfieldは起動時に拒否されます。`source`、`mount`、`lenses`は[セットアップ](setup.md)で説明しています。

| キー | 説明 |
| --- | --- |
| `zenoh_key_prefix` | すべてのchannelをpublishするconcreteなZenoh key |
| `equirectangular.size` | 2:1の出力`[width, height]` |
| `equirectangular.jpeg_quality` | 0〜100のJPEG品質 |
| `views.fov_deg` | 全viewの水平画角。180°未満 |
| `views.size` | 全viewの出力`[width, height]` |
| `views.jpeg_quality` | 0〜100のJPEG品質 |
| `views.channels[].view_key` | viewを表す一意な単一key segment |
| `views.channels[].initial_yaw_deg` | 起動時のviewの向き。省略時は`0` |
| `publisher.publish_frequency_hz` | 目標出力周波数。上限はカメラの15 fps |
| `publisher.congestion_control` | `drop`、`block`、`block_first` |
| `publisher.reliability` | `best_effort`または`reliable` |

`equirectangular`と`views`はどちらも省略できますが、少なくとも一方が必要です。取得した各frameは`publish_frequency_hz`の最も近い出力slotに割り当てられ、slotごとに1回だけpublishされます。そのため速いカメラは均等に間引かれ、遅れたframeがまとめて送られることもありません。

THETA Sの解像度は1°あたり約3pixelです。`views.fov_deg`を狭めると拡大されますが、細部は増えません。

## Zenoh interface

| キー | 種類 | Payload |
| --- | --- | --- |
| `{prefix}/equirectangular` | Publication | 360° x 180°のequirectangular JPEG（`image/jpeg`）。前方が水平中央 |
| `{prefix}/view/{view_key}` | Publication | 透視投影JPEG（`image/jpeg`）。向きを表すJSON attachment `{"yaw_deg": <number>}`付き |
| `{prefix}/view/{view_key}/direction` | Queryable | 下記参照 |

向きは胴体座標系で表します。yaw `0`が前方で、正のyawは左向きです。すべてのviewは水平方向を向きます。

direction keyへのqueryは次のように応答します。

- payloadなしの場合は、現在の向きをJSON（`application/json`）`{"yaw_deg": <number>}`で返します。
- JSON payload `{"yaw_deg": <number>}`付きの場合は、向きを設定し、`(-180, 180]`に正規化して適用した向きを返します。
- 不正なpayloadの場合は、理由を書いたerror reply（`text/plain`）を返し、向きは変わりません。

設定に成功した後に取得したframeは新しい向きを写し、画像のattachmentで確認できます。各viewは独立して制御され、最後のrequestが優先されます。自分専用の向きが必要なclientには別の`view_key`を割り当ててください。

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

| キー | 動作 |
| --- | --- |
| `Left` / `Right` | viewを5°回転。押し続けると回り続けます |
| `R` | 前方（yaw `0`）を向く |
| `Q` / `Esc` | 終了 |

viewerは起動時にノードから現在の向きを読み込むため、接続しただけでviewが動くことはありません。向きとキー操作はwindow上に文字で表示されます。`examples/viewer-zenoh-config.json5`は`tcp/127.0.0.1:7447`へ接続します。別hostへ接続する場合は`--zenoh-config`を指定してください。
