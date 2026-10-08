# セキュリティ方針

## 公開する範囲

公開リポジトリとPagesの成果物には、公表された予算・配分額、公的議員情報、出典と調査方法だけを含めます。非公開資料、ログイン情報、APIキー、PAT、Cookie、内部URL、調査に不要な個人の連絡先を含めません。公開サイトでは`public/`だけを配信し、ソース取得キャッシュやローカル検証結果を含めません。

政治家の党名・会派・資料日を区別し、未照合の値や推測を事実として公表しないことも、改ざんや誤った結合による被害を抑えるための要件です。

## 実装上の防御

| 対象 | 対策 |
| --- | --- |
| データ由来のXSS | 出典・議員名などは`textContent`で挿入。HTMLとして解釈せず、`eval`を不使用 |
| 危険なリンク | HTTPSのみ。URL内の認証情報を拒否。別タブは`noopener noreferrer` |
| スクリプト供給網 | 本体は外部依存ゼロ。CDN・外部フォント・解析スクリプト不使用 |
| ブラウザー通信 | CSPは`default-src 'none'`、スクリプト・CSS・JSON取得を同一オリジンに限定 |
| CSV数式 | 先頭の空白・制御文字を含めて`= + - @`を無害化し、引用符をエスケープ |
| ファイル取得 | 公式ホストを限定。HTTPS・TLS検証を維持。リダイレクトも再検査。サイズ・回数・待ち時間を制限 |
| キャッシュ | URLのハッシュでファイル名を作成し、URLのパスをローカルの保存先として使わない |
| PRのCI | `pull_request`と読み取り権限だけ。秘密情報・デプロイ権限なし。`pull_request_target`不使用 |
| Actions | 第三者Actionを完全なコミットSHAで固定。認証をチェックアウト後に保持しない |
| Pages | `main`での手動起動。公開ジョブだけに`pages: write`・OIDCを付与し、`public/`だけをアップロード |

`data.json`を変更するPRもコード変更と同様にレビューしてください。表の値、地域名、比較段階、出典URL、政党の根拠、公表日と取得日を確認します。ハッシュは内容変更の検知に使えますが、出典の真正性や数値の正しさを保証するものではありません。

## リポジトリの設定

- Secret scanningとPush protectionを有効にする。
- `main`への変更にレビューと`Validate public budget data`の成功を要求する。
- リポジトリ全体の既定Actions権限は読み取りにする。
- PagesのSourceをGitHub Actionsにし、公開が意図したものか確認して手動起動する。
- パブリックPRに秘密情報を渡さない。外部資料取得を、権限のあるCIで自動実行しない。
- Actionの更新では公式リリースとコミットSHAを確認する。固定SHAの一覧はワークフローに記載。

これらの設定はこの変更で自動的に有効化したとは扱いません。

## HTTPヘッダーを設定できるホストの場合

次のヘッダーを追加します。GitHub Pagesでは任意のレスポンスヘッダーをリポジトリから設定できないため、実装済みなのはHTMLのCSPメタとreferrerメタです。`frame-ancestors`はCSPメタでは効きません。

```text
Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; object-src 'none'; form-action 'none'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

サーバーが公開するMIME型を正しく設定してください。公開時はHTTPSを使用します。ローカル検証用のPythonサーバーをインターネットに公開する運用は想定していません。

## 脆弱性・漏えいを発見した場合

リポジトリのPrivate vulnerability reportingが有効なら、Securityタブから非公開で報告してください。公開Issueにトークン、攻撃に利用できる秘密情報、未修正の具体的な攻撃手順を掲載しないでください。

認証情報の漏えいは、まず失効・再発行して影響を確認します。Git履歴やログからの削除だけで漏えい済みの認証情報が無効になったとは扱いません。
