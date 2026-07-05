# Enable Required APIs
resource "google_project_service" "artifactregistry_api" {
  project = var.project_id
  service = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudfunctions_api" {
  project = var.project_id
  service = "cloudfunctions.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudrun_api" {
  project = var.project_id
  service = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "aiplatform_api" {
  project = var.project_id
  service = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

# Artifact Registry
resource "google_artifact_registry_repository" "causal_repo" {
  location      = var.region
  repository_id = var.causal_artifact_repo_name
  description   = "Docker repository for Causal MLOps components"
  format        = "DOCKER"
  depends_on    = [google_project_service.artifactregistry_api]
}

# BigQuery Dataset
resource "google_bigquery_dataset" "observability" {
  dataset_id                  = "tracerlens_observability"
  friendly_name               = "TracerLens Observability"
  description                 = "Dataset for agent execution traces"
  location                    = var.region
}

# BigQuery Table
resource "google_bigquery_table" "agent_traces" {
  dataset_id = google_bigquery_dataset.observability.dataset_id
  table_id   = "agent_execution_traces"

  schema = <<EOF
[
  {
    "name": "trace_id",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Unique trace identifier"
  },
  {
    "name": "prompt_size",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Size of the input prompt"
  },
  {
    "name": "tool_usage_count",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Number of tools used"
  },
  {
    "name": "loops_count",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Number of agent loops"
  },
  {
    "name": "total_tokens",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Total token consumption"
  },
  {
    "name": "timestamp",
    "type": "TIMESTAMP",
    "mode": "NULLABLE",
    "description": "Time of trace execution"
  }
]
EOF
}

# GCS Bucket for Artifacts
resource "google_storage_bucket" "causal_artifacts" {
  name                        = "${var.causal_artifacts_bucket}-${var.project_id}"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

# IAM for default compute service account to access BQ and GCS
# (Reusing data "google_project" "project" defined in iam.tf)

resource "google_project_iam_member" "compute_bq_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "compute_gcs_admin" {
  project = var.project_id
  role    = "roles/storage.admin"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

# (The actual deployment of Cloud Functions and Cloud Run would typically be handled via CI/CD,
# but we define the basic permissions needed here. If the user wants complete TF for the functions/run,
# we would need to upload zips and use google_cloudfunctions2_function / google_cloud_run_v2_service)
