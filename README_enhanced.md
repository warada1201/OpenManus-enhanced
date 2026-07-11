# OpenManus Enhanced Usage Guide

OpenManus Enhancedは、本家Manus AIに近づけるために強化された機能を持つエージェントです。
永続メモリ、自己修正、検証エージェント、コスト最適化などの機能が追加されています。

> **Note**: 本リポジトリは [FoundationAgents/OpenManus](https://github.com/FoundationAgents/OpenManus)（MITライセンス）の非公式フォークです。

> **⚠️ APIキーの取り扱い**: `config/config.toml` は `.gitignore` に登録されており、コミットされません。
> APIキーは必ず `config/config.toml` にのみ書き、`config/config.example*.toml`（テンプレート）には実キーを書かないでください。

## ✨ 主な機能

- **🧠 永続メモリ**: 過去の対話や成功したツール使用パターンを記憶し、学習します。
- **🔧 自己修正**: エラーが発生した場合、自動的に別の方法を試したり戦略を変更します。
- **✅ 自動検証**: タスク完了後に成果物を簡易チェックし、品質を保証します。
- **💰 コスト最適化**: コンテキストサイズを管理し、APIトークンの消費を抑えます。

## 🚀 セットアップ

### 1. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 2. Playwrightブラウザのインストール

ブラウザ自動化機能を使用するために必要です。

```bash
playwright install
```

### 3. 設定ファイル

`config/config.toml` を作成し、LLMのAPIキーを設定してください。
低コスト運用には DeepSeek を推奨します（`config/config.example-model-deepseek.toml` をコピーして使用できます）。

```toml
[llm]
api_type = "deepseek"
model = "deepseek-chat"                   # ツール呼び出し対応・低コスト
base_url = "https://api.deepseek.com/v1"
api_key = "sk-..."                        # https://platform.deepseek.com で取得
max_tokens = 8192
temperature = 0.0
```

他のプロバイダ（Anthropic / Azure / Ollama など）の設定例は `config/config.example*.toml` を参照してください。

## 🎮 実行方法

### 基本的な実行

対話モードで起動します。プロンプトの入力が求められます。

```bash
python run_enhanced.py
```

### プロンプトを指定して実行

コマンドライン引数でタスクを直接指定できます。

```bash
python run_enhanced.py --prompt "京都の天気とおすすめの観光スポットを調べて"
```

### オプション

- `--max-steps`: エージェントの最大実行ステップ数を指定します（デフォルト: 15）。

```bash
# 複雑なタスクのためにステップ数を増やす
python run_enhanced.py --max-steps 30 --prompt "..."
```

## 💰 DeepSeekでの低コスト運用

DeepSeek を使う場合、以下の仕組みでコストを抑えられます:

- **自動プロンプトキャッシュ**: DeepSeek は会話履歴の共通プレフィックスを自動でキャッシュし、
  キャッシュヒットした入力トークンは約1/10の単価になります。
  本エージェントはキャッシュが効くよう、コンテキスト削減の頻度を抑えた設計になっています。
- **コストの可視化**: 各APIコールのログにキャッシュヒット率が表示され、
  タスク完了時のサマリーに累計トークン数と概算コスト（USD）が表示されます。
- **画像入力について**: DeepSeek に vision モデルはないため、ブラウザのスクリーンショットは
  自動的に破棄され、テキスト情報のみで動作します（エラーにはなりません）。
  vision が必要な場合のみ `[llm.vision]` に別プロバイダのモデルを設定してください。

### さらにコストを下げるヒント

- `--max-steps` を小さくする（単純なタスクなら 10 以下で十分）
- `[llm]` の `max_input_tokens` を設定して1タスクあたりの入力トークン総量に上限を設ける
- 複雑な推論が必要なときだけ `deepseek-reasoner` に切り替える（通常は `deepseek-chat` で十分）

## 📂 データ保存場所

- **メモリデータベース**: `.openmanus_memory.db` (SQLite)
  - ここに過去のタスク履歴や学習データが保存されます。
- **ログ**: `logs/` ディレクトリに実行ログが保存されます。

## 🔍 トラブルシューティング

- **APIエラー**: `config.toml` のAPIキーが正しいか確認してください。
- **ブラウザエラー**: `playwright install` を実行したか確認してください。
- **トークン制限**: 非常に長いタスクの場合、`--max-steps` を調整するか、タスクを分割してください。
