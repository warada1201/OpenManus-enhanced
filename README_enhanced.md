# OpenManus Enhanced Usage Guide

OpenManus Enhancedは、本家Manus AIに近づけるために強化された機能を持つエージェントです。
永続メモリ、自己修正、検証エージェント、コスト最適化などの機能が追加されています。

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

`config/config.toml` を作成し、LLMのAPIキーを設定してください。（`config/config.example.toml` をコピーして使用できます）

```toml
[llm]
model = "claude-3-5-sonnet-20241022"  # 推奨モデル
base_url = "https://api.anthropic.com/v1"
api_key = "sk-..."  # あなたのAPIキー
```

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

## 📂 データ保存場所

- **メモリデータベース**: `.openmanus_memory.db` (SQLite)
  - ここに過去のタスク履歴や学習データが保存されます。
- **ログ**: `logs/` ディレクトリに実行ログが保存されます。

## 🔍 トラブルシューティング

- **APIエラー**: `config.toml` のAPIキーが正しいか確認してください。
- **ブラウザエラー**: `playwright install` を実行したか確認してください。
- **トークン制限**: 非常に長いタスクの場合、`--max-steps` を調整するか、タスクを分割してください。
