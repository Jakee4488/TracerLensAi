resource "google_cloud_run_service" "tracerlensai_app" {
  name     = "tracerlensai-app"
  location = var.region

  template {
    spec {
      containers {
        image = "gcr.io/${var.project_id}/tracerlensai-app:latest"
        
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "GOOGLE_CLOUD_REGION"
          value = var.region
        }
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }
}

resource "google_cloud_run_service_iam_member" "public_access" {
  location = google_cloud_run_service.tracerlensai_app.location
  project  = google_cloud_run_service.tracerlensai_app.project
  service  = google_cloud_run_service.tracerlensai_app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
