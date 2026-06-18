output "gke_cluster_name" {
  description = "The name of the created GKE cluster"
  value       = google_container_cluster.primary.name
}

output "gke_cluster_endpoint" {
  description = "The endpoint of the GKE cluster"
  value       = google_container_cluster.primary.endpoint
}

output "artifact_registry_repo" {
  description = "The Artifact Registry repository name"
  value       = google_artifact_registry_repository.agent_repo.name
}

output "bigquery_dataset_id" {
  description = "The BigQuery dataset ID for logs"
  value       = google_bigquery_dataset.agent_logs.dataset_id
}

output "app_service_account_email" {
  description = "The email of the GCP Service Account used by the GKE application"
  value       = google_service_account.app_sa.email
}
