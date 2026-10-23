output "service_url" {
  description = "3D ワーカーの URL。slack-agent-bot の modeling_worker_url に入れる"
  value       = google_cloud_run_v2_service.worker.uri
}

output "service_account_email" {
  description = "3D ワーカーが動く Service Account のメール"
  value       = google_service_account.worker.email
}

output "artifact_registry_repo" {
  description = "Docker image を push する Artifact Registry リポ URI"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.worker.repository_id}"
}

output "github_token_secret_id" {
  description = "GitHub トークンを入れる Secret Manager シークレット ID"
  value       = google_secret_manager_secret.github_token.secret_id
}
