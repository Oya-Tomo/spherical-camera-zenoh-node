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
| `views.channels[].direction` | viewの向き。steerable viewでは起動時の向き、fixed viewでは常に使う向き。どちらも省略時は前方の水平（`{yaw_deg: 0, pitch_deg: 0}`） |
| `views.channels[].direction.yaw_deg` | 水平方向の向き。前方が`0`で左向きが正。任意の有限値を受け付け、`(-180, 180]`に正規化 |
| `views.channels[].direction.pitch_deg` | 上下方向の向き。水平が`0`で下向きが正。`-90`〜`90` |
| `views.channels[].fixed` | fixed viewなら`true`、steerable viewなら`false`。省略時は`false` |
| `publisher.publish_frequency_hz` | 目標出力周波数。上限はカメラの15 fps |
| `publisher.congestion_control` | `drop`、`block`、`block_first` |
| `publisher.reliability` | `best_effort`または`reliable` |

steerable viewは`direction`の向きで起動し、direction queryableで向きを変えられます。fixed viewにはdirection queryableがなく、常に`direction`の向きを写します。`direction`には`yaw_deg`と`pitch_deg`の両方が必要で、基準座標系と符号は後述のdirection queryableと同じです。`channels`ではsteerable viewとfixed viewを自由に混在できます。

`equirectangular`と`views`はどちらも省略できますが、少なくとも一方が必要です。取得した各frameは`publish_frequency_hz`の最も近い出力slotに割り当てられ、slotごとに1回だけpublishされます。そのため速いカメラは均等に間引かれ、遅れたframeがまとめて送られることもありません。

THETA Sの解像度は1°あたり約3pixelです。`views.fov_deg`を狭めると拡大されますが、細部は増えません。

## Zenoh interface

| キー | 種類 | Payload |
| --- | --- | --- |
| `{prefix}/equirectangular` | Publication | 360° x 180°のequirectangular JPEG（`image/jpeg`）。前方が水平中央 |
| `{prefix}/view/{view_key}` | Publication | 透視投影JPEG（`image/jpeg`）。steerable viewは現在の向き、fixed viewは設定した向きを写し、写っている向きを表すJSON attachment `{"yaw_deg": <number>, "pitch_deg": <number>}`付き |
| `{prefix}/view/{view_key}/direction` | Queryable | steerable viewのみ。下記参照 |

viewの向きは、基準座標系（x前、y左、z上）でのyawとpitchで表します。符号は[`mount`](setup.md#取り付け向き)と同じです。

- `yaw_deg`は前方が`0`で、左向きが正です。任意の有限値を受け付け、`(-180, 180]`に正規化します。
- `pitch_deg`は水平が`0`で、下向きが正です。任意の有限値を受け付け、範囲外の値は`-90`または`90`にします。
- viewはrollしないため、どのview画像でも水平線は傾きません。

direction keyへのqueryは次のように応答します。

- payloadなしの場合は、現在の向きをJSON（`application/json`）`{"yaw_deg": <number>, "pitch_deg": <number>}`で返します。
- JSON payload `{"yaw_deg": <number>, "pitch_deg": <number>}`付きの場合は、向きを設定し、適用した向きを返します。
- fieldの欠落、余分なfield、有限でない値など、不正なpayloadの場合は、理由を書いたerror reply（`text/plain`）を返し、向きは変わりません。

設定に成功した後に取得したframeは新しい向きを写し、画像のattachmentで確認できます。各viewは独立して制御され、最後のrequestが優先されます。自分専用の向きが必要なclientには別の`view_key`を割り当ててください。

fixed viewはdirection queryableを宣言しないため、そのdirection keyへのqueryにはreplyが返りません。fixed viewのframeにも同じattachmentが付き、常に設定した向きを表します。

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

| オプション | 既定値 |
| --- | --- |
| `--viewer-config FILE` | `examples/viewer-config.json5` |
| `--zenoh-config FILE` | `examples/viewer-zenoh-config.json5` |

表示するviewはviewer設定だけで決まります。完全な例は[`examples/viewer-config.example.json5`](../../examples/viewer-config.example.json5)にあります。未知、欠落、重複、型が不正なfieldは起動時に拒否されます。viewerのZenoh接続は[セットアップ](setup.md#zenohの接続)で説明しています。

| キー | 説明 |
| --- | --- |
| `zenoh_key_prefix` | ノードがpublishするconcreteなZenoh key。ノードの設定と一致させます |
| `view_key` | 表示するviewを表す単一key segment |
| `direction_step_deg` | 回転または傾きの1 stepの角度。`0`より大きく`90`以下 |

viewerは`{zenoh_key_prefix}/view/{view_key}`を表示し、起動時にsteerable viewかfixed viewかを判定します。

| 起動時の状態 | viewerの動作 |
| --- | --- |
| ノードが`{zenoh_key_prefix}/view/{view_key}/direction`に応答する | viewを表示し、そのkeyを通してキーで向きを変える |
| ノードはdirection keyに応答しないが、3秒以内にviewをpublishする | viewをfixed viewとして表示する |
| ノードがdirection keyに応答せず、viewもpublishしない | viewのkeyを含むerrorで停止する |

| キー | 動作 |
| --- | --- |
| `Left` / `Right` | viewを左右に`direction_step_deg`だけ回転。押し続けると回り続けます |
| `Up` / `Down` | viewを上下に`direction_step_deg`だけ傾ける。真上または真下まで傾き、押し続けると傾き続けます |
| `R` | 前方を水平に向く（yaw `0`、pitch `0`） |
| `Q` / `Esc` | 終了 |

steerable viewでは、viewerは起動時にノードから現在の向きを読み込むため、接続しただけでviewが動くことはありません。現在のyaw、pitchとキー操作はwindow上に文字で表示されます。

fixed viewでは、回転、傾き、前方を向くキーは何もせず、viewerは向きのrequestを送りません。windowには表示中のframeのattachmentが表すyawとpitch、fixed viewであること、終了キーだけが表示されます。
