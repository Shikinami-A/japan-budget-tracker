# 日本地図の出典と利用条件

地図形状は Geolonia の japanese-prefectures / map-full.svg を加工しています。
予算の一次資料・自治体の現行境界や選挙区の確認根拠には使用しません。

- 著作者：Geolonia。Wikipedia「日本地図.svg」を基にした SVG。
- 上流：https://github.com/geolonia/japanese-prefectures
- 固定リビジョン：90c5b4b8260de058d3db61b3cb8bfb6f67a81f9a
- 元ファイル：https://raw.githubusercontent.com/geolonia/japanese-prefectures/90c5b4b8260de058d3db61b3cb8bfb6f67a81f9a/map-full.svg
- 利用条件：上流 README に記載の GNU Free Documentation License (GFDL)。ライセンス全文は同じディレクトリの COPYING (version 1.2)。地図データのライセンスはコードの MIT と別です。
- 上流の来歴：https://ja.wikipedia.org/wiki/ファイル:日本地図.svg
- 加工：SVG のポリゴン・パス・平行移動を抽出し、47都道府県のコードと名前を持つ japan.json に変換。動的な色分け・選択枠はアプリ側で付与。元の形状は維持し、沖縄の位置は上流による移動、一部離島は上流で省略されています。
- 再現：元ファイルを .cache/map/map-full.svg に保存し、python3 scripts/build_map.py。スクリプトは照合済み SHA-256 を検査し、通信せずに変換します。通常の予算ビルド・CI・画面表示に外部取得は不要です。

比較地図は制度・所管・会計・段階・範囲・両年度の対象期間・単位が一つの組み合わせのときだけ表示します。県名を直接掲載した行のみを使用し、市町村や広域管内の額は合算・配分しません。両年度の原本数値照合と「同範囲」の確認がある行だけ、前年比を色で示します。色は −10% から +10% までの連続グラデーションで、それ以上の差は同じ濃さ、表示値は実際の増減率です。

未取得・未収載・片年度非掲載・原本ダッシュ・前年ゼロ・参考比較・照合未完了・重複は斜線です。県別行が存在しない場合とフィルターに一致しない場合も明示し、予算ゼロや公表なしとは解釈しません。率の単純平均、異なる制度の総和、親総額と内訳の同時加算は行いません。
