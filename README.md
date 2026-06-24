# EEG Task Scheduler

EEG，画面キャプチャ，キーボード・マウス操作量，Notion Todo を 25 分程度の作業セッション単位で同期して記録するデスクトップアプリである．セッション中は EEG 指標，画面観察，入力活動を 30 秒程度のエピソードとして保存し，終了時に作業フェーズ，振り返り，次 Todo 候補へ集約する．

## できること

- ADS1299 + ESP32S3 の BLE EEG デバイスからサンプルを受け取り，theta，alpha，beta，engagement，workload などの特徴量を計算する．
- Electron から画面またはウィンドウをキャプチャし，Gemini が使える場合は作業内容の短い説明を生成する．
- Windows の低レベルフックでキーボード入力，クリック，スクロール，マウス移動量を集計する．
- Notion の Tasks データソースから未完了 Todo を取得し，セッション開始時に `In Progress` へ更新する．
- セッション終了時にレポートと次 Todo 候補を作り，必要なら Notion へ次タスクを作成する．

Gemini API キーや Notion 設定がない場合も，ローカルのフォールバック処理で起動可能である．

## 動作環境

Windows および macOS で動作する．

| 機能 | Windows | macOS |
| --- | --- | --- |
| Electron 起動・バックエンド自動起動 | ○ | ○ |
| セッション記録・EEG 計測 | ○ | ○ |
| グローバル入力監視（キー・マウス） | ○ | × |
| アクティブウィンドウ取得 | ○ | × |

グローバル入力監視とアクティブウィンドウ取得は PowerShell/Win32 API に依存しており，macOS では動作しない．主要機能（セッション記録，EEG 計測，画面キャプチャ，Notion 連携）は macOS でも利用できる．

## 起動

### 初回セットアップ

`web/dist` はリポジトリに含まれないため，初回は必ずフロントエンドのビルドが必要である．

```bash
uv sync --extra dev
npm install
npm run install:all
npm --prefix web run build
```

### 通常起動

```bash
npm start
```

Windows・macOS ともに同じコマンドで動作する．Electron 起動後にバックエンドが自動的に立ち上がり，ビルド済みの `web/dist` を配信する．

`npm start` は Electron デスクトップアプリを起動する．`EEG_BACKEND_URL` が未指定の場合，Electron 側が `uv run eeg-task-scheduler` で FastAPI バックエンドを `127.0.0.1:8766` 以降の空きポートに立ち上げる．

開発起動:

```powershell
npm run dev
```

`npm run dev` は FastAPI，Vite，Electron をまとめて起動する．バックエンドは `8766..8780` の空きポート，Vite は `http://127.0.0.1:5173` を使う．

## 履歴プレビュー

記録済みセッションの UI を確認する場合は，Vite の URL に `session_id` を付ける．

```text
http://127.0.0.1:5173/?session_id=20260623-132529-ebe052
```

`session_id` は `data/app.sqlite3` の `sessions.id`，または `captures/<session_id>/` のディレクトリ名である．履歴プレビューでは live 用の直近窓ではなく，セッション開始から終了までの全体を同じ時間軸で表示する．EEG 指標と操作量は 5 分窓の z-score 正規化後に `0..100` へ写像し，滑らかな曲線として描画する．フロー，通常，過負荷停止，逸脱停止のイベント帯も同じ時間軸で表示する．

グラフ下の赤いバーを動かすと，任意の時刻を確認できる．右端のカメラボタンを押すと，その時刻に最も近いキャプチャ画像と画面説明を表示する．画像表示は重くなりやすいため，デフォルトではオフである．説明欄は固定幅のまま内部スクロールできる．

停止:

```powershell
npm stop
```

`npm stop` はこのプロジェクトに紐づく Python，Node，Electron プロセスと，開発用ポートを掃除する．

## 環境変数

`.env` に必要な値を置く．

```env
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001

ADS1299_DEVICE_NAME=ADS1299_EEG_NUS
ADS1299_DEVICE_ADDRESS=
EEG_ELECTRODE_NAMES=C3,Cz,C4,,,,,

NOTION_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_TASKS_DATA_SOURCE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_PROJECTS_DATA_SOURCE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_VERSION=2025-09-03
```

主な設定:

- `GEMINI_API_KEY`: 画面説明，エピソード要約，レポート要約，埋め込み生成に使う．未設定時はローカルの定型文で継続する．
- `ADS1299_DEVICE_NAME`: BLE スキャンで探す EEG デバイス名である．
- `ADS1299_DEVICE_ADDRESS`: 指定した場合は BLE アドレスを優先して接続する．
- `EEG_ELECTRODE_NAMES`: CH1 から CH8 の電極名である．空欄のチャンネルは全体 EEG 指標から除外される．デフォルトは `CH1=C3, CH2=Cz, CH3=C4` である．
- `NOTION_API_KEY`: Notion integration token である．
- `NOTION_TASKS_DATA_SOURCE_ID`: Todo を読む Tasks データソース ID である．
- `NOTION_PROJECTS_DATA_SOURCE_ID`: 次 Todo 作成時に Project relation を引き継ぐための Projects データソース ID である．

`F3` と `F4` が `EEG_ELECTRODE_NAMES` に含まれる場合だけ，FAA 風の approach / avoidance 指標を表示する．

## Notion 連携

Notion から Todo を読む場合は，Notion Developers で内部 integration を作り，対象の Tasks データソースに connection として追加する．Projects relation も使う場合は，Projects データソースにも同じ integration を共有する．

確認用 API:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/todos/notion
```

アプリでは，Notion から選んだ Todo でセッションを開始すると，そのページの `Status` を `In Progress` に更新する．セッション終了後に「Notionへ反映」を押すと，生成された次 Todo 候補を同じ Project に紐づく Tasks として作成し，元タスクにはレポートコメントを追加する．

## データ保存先

- `data/app.sqlite3`: セッション，EEG ウィンドウ，画面観察，入力活動，エピソード，イベント，レポートを保存する．
- `data/chroma/`: Chroma が使える場合の RAG 用永続データである．
- `captures/<session_id>/`: セッション中のスクリーンショット PNG である．
- `recordings/`: 予約済みの記録用ディレクトリである．

これらは `.gitignore` で追跡対象外にしている．

## リポジトリ構造

| パス | 役割 |
| --- | --- |
| `package.json` | ルートの npm scripts である．Electron 起動，開発起動，ビルド，テストをまとめている． |
| `pyproject.toml` | Python パッケージ定義である．FastAPI，MNE，Bleak，Chroma，Gemini SDK などの依存を管理する． |
| `playwright.config.ts` | UI テスト設定である．Vite dev server を起動して Chromium で検証する． |
| `desktop\main.js` | Electron main process である．FastAPI バックエンドの起動，BrowserWindow 作成，画面キャプチャ，アクティブウィンドウ取得，Windows へのキー・マウス入力を `SetWindowsHookEx()` API で監視する処理を持つ． |
| `desktop\preload.js` | Electron preload である．`contextBridge` で `window.eegDesktop` を公開し，React 側から画面キャプチャや入力活動読み取りを呼べるようにする． |
| `desktop\package.json` | Electron 側の依存と起動スクリプトである． |
| `web\src\main.tsx` | React UI 本体である．Todo 選択，セッション開始・停止，BLE 接続，定期画面キャプチャ，30 秒エピソード送信，レポート表示，Notion 反映ボタンを扱う． |
| `web\src\api.ts` | FastAPI への typed client である．タイムアウト，API trace，runtime token 照合，レスポンスサイズ制限をまとめている． |
| `web\src\types.ts` | フロントエンドで使う API レスポンス型である． |
| `web\src\styles.css` | React UI のスタイルである． |
| `backend\eeg_task_scheduler\app.py` | FastAPI アプリの入口である．REST API，CORS，静的配信，BLE 接続，セッション操作，Notion 反映，キャプチャ配信をまとめている． |
| `backend\eeg_task_scheduler\config.py` | `.env` とデフォルト値から設定を組み立てる．保存ディレクトリや EEG 電極名の解釈もここにある． |
| `backend\eeg_task_scheduler\db.py` | SQLite schema と薄い query helper である．セッション，EEG，画面観察，入力活動，エピソード，フェーズ，レポート，RAG チャンクのテーブルを作る． |
| `backend\eeg_task_scheduler\session.py` | セッションの中心ロジックである．開始・停止，EEG ウィンドウ保存，画面観察保存，入力活動保存，エピソード生成，レポート生成，現在状態の集約を担当する． |
| `backend\eeg_task_scheduler\classifier.py` | EEG 指標と入力活動から，通常，フロー，停滞，過負荷停滞などの状態ラベルを決める． |
| `backend\eeg_task_scheduler\gemini_client.py` | Gemini 連携である．画面説明，エピソード要約，セッションレポート，作業フェーズ要約，埋め込み生成を担当し，未設定時はフォールバックを返す． |
| `backend\eeg_task_scheduler\notion_client.py` | Notion API client である．未完了タスク取得，タスク作成，Status 更新，コメント追加，data source ID 解決を行う． |
| `backend\eeg_task_scheduler\rag.py` | RAG 用ストアである．SQLite に必ず記録し，Chroma が使える場合は永続 collection にも upsert する． |
| `backend\eeg_task_scheduler\eeg\ble.py` | ADS1299 BLE client である．Bleak でデバイス探索，NUS characteristic 通知購読，ストリーミング開始・停止，欠落サンプル検出，受信状態管理を行う． |
| `backend\eeg_task_scheduler\eeg\protocol.py` | BLE で受け取ったバイト列を `DeviceConfig` と `EegSample` に分解する packet parser である． |
| `backend\eeg_task_scheduler\eeg\features.py` | EEG 特徴量計算である．MNE の `RawArray` と `psd_array_welch` を使い，バンドパワー，engagement，workload，signal quality を計算する． |
| `scripts\dev.ps1` | 開発起動用スクリプトである．空きバックエンドポートを探し，FastAPI，Vite，Electron を `concurrently` で起動する． |
| `scripts\stop.ps1` | 停止用スクリプトである．このプロジェクトのプロセスと開発用ポートを掃除する． |
| `tests\*.py` | Python 側の単体テストである．設定，DB，RAG，EEG 特徴量，BLE スキャン，packet parser，Notion client，セッション処理を検証する． |
| `tests\ui\*.spec.ts` | Playwright UI テストである．Web UI と Electron 経由の操作を検証する． |

## 主な API

| API | 内容 |
| --- | --- |
| `GET /api/status` | BLE，Gemini，キャプチャ，DB，RAG，Notion の状態を返す． |
| `GET /api/runtime` | Electron が正しいバックエンドへ接続しているか確認するための protocol，pid，runtime token を返す． |
| `GET /api/todos/initial` | Notion の未完了 Todo，またはローカルの前回 Todo を初期値として返す． |
| `GET /api/todos/notion` | Notion の未完了 Tasks を取得する． |
| `POST /api/ble/connect` | ADS1299 BLE デバイスへ接続し，ストリーミングを開始する． |
| `POST /api/session/start` | Todo と設定を保存し，セッションを開始する． |
| `GET /api/session/{session_id}` | 記録済みセッションを履歴プレビュー用に取得する．EEG，入力活動，イベント，画面観察をセッション全体分返す． |
| `POST /api/observations/screen` | base64 PNG を保存し，画面観察として登録する． |
| `POST /api/episodes` | 入力活動，アクティブウィンドウ，画面観察，直近 EEG 指標から 1 エピソードを登録する． |
| `POST /api/session/stop` | セッションを終了し，フェーズとレポートを生成する． |
| `POST /api/reports/{report_id}/apply-notion` | レポートから次 Todo を Notion に作成し，元タスクへコメントする． |

## 開発確認

Python テスト:

```powershell
uv run pytest -q
```

Web と Electron のビルド確認:

```powershell
npm run build
```

UI テスト:

```powershell
npm run test:ui
```

まとめて確認:

```powershell
npm run test:all
```

## 注意

- Windows のグローバル入力監視は `desktop\main.js` の PowerShell/C# スクリプトで動く．管理者権限やセキュリティ設定により取得できない場合がある．
- 画面キャプチャの内容は Gemini に送信される可能性がある．`gemini_client.py` には簡易的な private 判定があるが，機密画面ではキャプチャ対象を慎重に選ぶ必要がある．
- `data/`，`captures/`，`recordings/` はローカル実行データである．必要に応じてバックアップや削除を行う．
