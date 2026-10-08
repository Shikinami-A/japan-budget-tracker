# 調査用クラウド環境の通信許可

## 2026年10月9日の確認結果

環境の許可ホストには下記の公式サイトが含まれ、`/etc/codex/network-policy.json`にも設定されています。初回の環境観測は適用状態`unknown`でした。その後、環境状態ツールで仕様5・現在のHTTPポリシーが`enforced`と確認できました。過去のunknown観測は履歴として保持し、当時の試行へ適用済みラベルを遡及しません。

通常サンドボックスの通信はプロキシ接続エラー（HTTP応答なし）。コマンドにネットワーク権限を付けた実行では、既存のHTTP(S)プロキシとTLS検証を維持したまま財務省HTTP 200、GitHubのwork取得、財務省・総務省・農水省・厚労省・防衛省・国交省の原本取得が成功しました。プロキシ解除・直接接続・別の取得経路は使っていません。

前回はchisou.go.jp/CASの採択候補8URLがHTTP 404、資源エネルギー庁はCONNECT 403でした。今回の環境設定・`/etc/codex/network-policy.json`には`www.enecho.meti.go.jp`の追加を確認しています。初回unknown状態と、その後の仕様5・enforced状態でそれぞれ一巡しました。両状態とも、追加許可後の事業概要・評価報告・制度入口・トップ・index.htmlと経産省予算概要候補はHTTP応答403でした。CONNECT拒否とは別に記録し、403の発生主体は未確定。プロキシ解除やTLS検証無効化、別経路での原本取得に置き換えていません。

仕様5・enforced状態でも内閣府の候補8URLは404継続。計22URLを一巡し、20件は404、内閣府政策入口・トップ2件は取得成功。現行政策入口は取得成功し、従来のchisou掲載先を案内していました。追加の原本は復興庁・国交省・防衛省・栃木県選管・参政党・公明党で取得成功。取得不能の資料を非掲載・非公表と認定せず、他の調査を継続しています。

静岡2026の旧想定`index-22.pdf`の403は現行の公式索引でURL差し替えを確認し、`index-49.pdf`で取得しました。ホストの許可不足と、個別URLの変更を区別します。原本の取得記録は`data/`の照合JSON、原本バイナリはGit除外の`.cache/originals/`に保存しています。

## 環境を設定する手順

2026年10月8日に確認した[OpenAIの公式手順](https://learn.chatgpt.com/docs/environments/cloud-environments)に基づきます。GitHubや調査サイトの公開設定ではなく、原本を取得するクラウド環境の設定です。

1. Codexの **Settings → Codex Cloud → Environments** を開きます。
2. `Shikinami-A/japan-budget-tracker` を使用する環境の **… → Edit** を選びます。
3. **Internet access** で **Allow Codex to access internet** をオンにします。
4. **Allow domains** は現在の **Package managers** を維持し、**Additional allowed domains** に下記のホスト名を登録します。URLの`https://`やパスは含めません。
5. 保存して **Republish** を選びます。未公開の環境では **Publish** と表示されます。
6. 更新した環境を選んで新しいクラウドタスクを開始し、通信を確認します。既存タスクへの自動反映を前提にしません。

新しいタスクでは次の依頼文で、既存の成果物から調査を継続できます。

```text
Shikinami-A/japan-budget-tracker のドラフトPR #1・workブランチから、2025/2026年度の全省庁予算調査を継続してください。まず通信許可の反映と財務省原本の取得を確認し、docs/RESEARCH_STATUS.mdの残件を進めてください。公開リポジトリのセキュリティ対策と、出典・党籍の不一致を明示する方針を維持してください。
```

## 政府・自治体の原本と国会名簿

収載済み出典と、次の調査に必要な省庁の入口を含みます。ワイルドカードの対応は確認できていないため、個別のホスト名で指定します。リダイレクト等で別のホストが必要になったら、実際の取得先を確認して追加します。

```text
www.mof.go.jp
www.bb.mof.go.jp
www.cas.go.jp
www.cao.go.jp
www.chisou.go.jp
www.digital.go.jp
www.reconstruction.go.jp
www.soumu.go.jp
www.moj.go.jp
www.mofa.go.jp
www.mext.go.jp
www.mhlw.go.jp
www.maff.go.jp
www.meti.go.jp
www.enecho.meti.go.jp
www.mlit.go.jp
www.env.go.jp
www.mod.go.jp
www.cfa.go.jp
www.shugiin.go.jp
www.sangiin.go.jp
www.pref.aichi.jp
www.pref.tochigi.lg.jp
```

## 所属党の一次資料

党籍の未照合分と既存の確認記録を照合するための公式サイトです。環境の通信許可への追加は、ブラウザー画面からこれらのサイトへ自動通信する変更ではありません。

```text
www.jimin.jp
www.jimin-aichi.or.jp
sanseito-aichi.com
o-ishin.jp
new-kokumin.jp
craj.jp
cdp-japan.jp
sanseito.jp
www.jcp.or.jp
reiwa-shinsengumi.com
hoshuto.jp
team-mir.ai
www.komei.or.jp
```

## 反映の確認

上の`www.enecho.meti.go.jp`は仕様5の適用済み許可に含まれますが、HTTP 403を解消できていません。次の福島県の事業計画・進捗・交付決定原本には、追加候補`www.pref.fukushima.lg.jp`が必要です。現在の許可にはなく、接続を試みていません。候補掲載先は`https://www.pref.fukushima.lg.jp/sec/11015e/kasokukahama.html`。追加後も取得・年度・対象範囲をそれぞれ検証します。

タスク内で環境の適用済み許可と実際の取得結果を確認します。収載済みの政府・自治体原本については、次のスクリプトで最初の1件を取得できます。

```sh
python3 scripts/fetch_sources.py --limit 1
```

成功時は`downloaded`、失敗時は`blocked_or_failed`と表示し、原本はGit対象外の`.cache/originals/`へ保存します。`failure_category: proxy_connect_denied`と`http_status: 403`はプロキシのCONNECT段階の拒否。HTTP応答の`http_error`と区別します。診断には認証情報・生のプロキシ例外・ヘッダーを記録しません。原本の取得だけで数値照合が完了したとは扱いません。党のページはこの取得スクリプトの対象外です。

`http_error`はHTTP応答を受けたという失敗分類であり、ゲートウェイと接続先のどちらが応答したかを単独では確定しません。資源エネルギー庁の専用確認は`python3 scripts/verify_meti_regional_originals.py --fetch --write --policy-state enforced`（環境ツールで適用済みを確認した場合のみ指定）。失敗履歴を保持して継続し、通常ビルド・CIは外部取得しません。

EnterpriseワークスペースのAgent Securityによる制限が表示され、編集できない場合は管理者による設定が必要です。公式手順では、環境の通信許可とワークスペースの制限の両方が適用されます。
