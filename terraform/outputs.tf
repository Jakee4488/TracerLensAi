
output "artifact_registry_repo" {
  description = "The Artifact Registry repository name"
  value       = google_artifact_registry_repository.causal_repo.name
}

output "bigquery_dataset_id" {
  description = "The BigQuery dataset ID for observability logs"
  value       = google_bigquery_dataset.observability.dataset_id
}
