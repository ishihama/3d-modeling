# 3D ワーカー（worker/）を Cloud Run に置く。設計・安全面は docs/worker.md。
# slack-agent-bot の terraform/ にそろえた構成。backend は既定のローカル（個人・社内用途）。

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  required_apis = [
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
  ]

  # GitHub トークンはシークレットのボリュームでファイルとして渡す（環境変数にしない。docs/worker.md の安全面 4）
  token_dir  = "/secrets"
  token_file = "github-token"
}

resource "google_project_service" "required" {
  for_each = toset(local.required_apis)

  project = var.project_id
  service = each.value

  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "worker" {
  project       = var.project_id
  location      = var.region
  repository_id = var.service_name
  format        = "DOCKER"
  description   = "Docker image repo for ${var.service_name}"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "${var.service_name}-run"
  display_name = "Service account for Cloud Run service ${var.service_name}"

  depends_on = [google_project_service.required]
}

# 値は Terraform 管理外（tfstate に残さない）。初回に:
#   echo -n "github_pat_xxx" | gcloud secrets versions add 3d-worker-github-token --data-file=- --project=<project>
# このリポジトリだけに Contents と Pull requests の読み書きを許した fine-grained トークン
resource "google_secret_manager_secret" "github_token" {
  project   = var.project_id
  secret_id = "${var.service_name}-github-token"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "worker_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.github_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_cloud_run_v2_service" "worker" {
  project  = var.project_id
  location = var.region
  name     = var.service_name

  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.worker.email
    # sandbox ユーザーへの切り替え・/proc を使ったプロセスの停止が普通の Linux と同じに動くように第 2 世代にする
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"

    # 成果物（out/）がインスタンスのローカルにあるので 1 台に固定する（docs/worker.md の「状態の持ち方」）
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    # 同時実行はワーカー内で 1 つずつにしているが、待っている間もリクエストは受けられるようにする
    max_instance_request_concurrency = 8

    # e2e は slim-bin（8 パーツ）で 1〜2 分。ボットの待ち時間（660 秒）より長くする
    timeout = "900s"

    volumes {
      name = "github-token"
      secret {
        secret       = google_secret_manager_secret.github_token.secret_id
        default_mode = 256 # 0400: root（ワーカー）だけが読める。sandbox ユーザーからは読めない
        items {
          version = "latest"
          path    = local.token_file
        }
      }
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit # /tmp（作業場所）もメモリから取られる
        }
        cpu_idle = true
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "WORKER_REPO_REMOTE"
        value = var.repo_remote
      }
      env {
        name  = "WORKER_GITHUB_REPO"
        value = var.github_repo
      }
      env {
        name  = "WORKER_PUSH"
        value = "1"
      }
      env {
        name  = "WORKER_GITHUB_TOKEN_FILE"
        value = "${local.token_dir}/${local.token_file}"
      }

      volume_mounts {
        name       = "github-token"
        mount_path = local.token_dir
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        timeout_seconds       = 3
        failure_threshold     = 20
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.worker_accessor,
  ]
}

# 外部からは呼べない（allUsers には付けない）。ずんだもんのサービスアカウントだけが呼べる
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  for_each = toset(var.invoker_service_accounts)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value}"
}
