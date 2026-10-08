# 調査用クラウド環境の通信許可

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

タスク内で環境の適用済み許可と実際の取得結果を確認します。収載済みの政府・自治体原本については、次のスクリプトで最初の1件を取得できます。

```sh
python3 scripts/fetch_sources.py --limit 1
```

成功時は`downloaded`、失敗時は`blocked_or_failed`と表示し、原本はGit対象外の`.cache/originals/`へ保存します。`failure_category: proxy_connect_denied`と`http_status: 403`が出た場合は、プロキシのCONNECT段階で拒否されています。これは接続先サーバーから返る`http_error`とは区別します。診断には認証情報・生のプロキシ例外・ヘッダーを記録しません。原本の取得だけで数値照合が完了したとは扱いません。党のページはこの取得スクリプトの対象外です。

EnterpriseワークスペースのAgent Securityによる制限が表示され、編集できない場合は管理者による設定が必要です。公式手順では、環境の通信許可とワークスペースの制限の両方が適用されます。
