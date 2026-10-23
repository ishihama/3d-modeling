variable "project_id" {
  type        = string
  description = "デプロイ先 GCP プロジェクト ID（ずんだもんと同じプロジェクトを想定）"
}

variable "region" {
  type        = string
  description = "Cloud Run / Artifact Registry リージョン"
  default     = "asia-northeast1"
}

variable "service_name" {
  type        = string
  description = "Cloud Run サービス名 / Artifact Registry リポ名 / シークレット名の接頭辞"
  default     = "3d-worker"

  validation {
    # サービスアカウント ID（<service_name>-run）が 6〜30 文字に収まるように
    condition     = can(regex("^[a-z][a-z0-9-]{1,25}$", var.service_name))
    error_message = "service_name は小文字英数 + ハイフンのみ使用可 (2〜26 文字)"
  }
}

variable "image" {
  type        = string
  description = "Cloud Run にデプロイする Docker image の完全 URI (tag 含む)"
  # 例: asia-northeast1-docker.pkg.dev/my-project/3d-worker/worker:v1
}

variable "cpu_limit" {
  type        = string
  description = "1 インスタンスあたり CPU 上限（OCP の処理・層ごとのチェック・描画）"
  default     = "2"
}

variable "memory_limit" {
  type        = string
  description = "1 インスタンスあたりメモリ上限（/tmp の作業場所を含む）"
  default     = "4Gi"
}

variable "repo_remote" {
  type        = string
  description = "スレッドごとにクローンするリポジトリ（WORKER_REPO_REMOTE）"
  default     = "https://github.com/ishihama/3d-modeling.git"
}

variable "github_repo" {
  type        = string
  description = "PR を作るリポジトリ（WORKER_GITHUB_REPO。owner/repo 形式）"
  default     = "ishihama/3d-modeling"

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repo))
    error_message = "github_repo は 'owner/repo' 形式で指定"
  }
}

variable "invoker_service_accounts" {
  type        = list(string)
  description = "ワーカーを呼べるサービスアカウント（ずんだもんの Cloud Run SA。slack-agent-bot の output service_account_email）"
}
