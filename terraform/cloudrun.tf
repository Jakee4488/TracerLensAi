resource "google_cloud_run_service" "tracerlensai_app" {
  name     = "tracerlensai-app"
  location = var.region

  template {
    spec {
      service_account_name = google_service_account.app_sa.email
      containers {
        image = "us-docker.pkg.dev/cloudrun/container/hello" # Dummy image for initial provisioning
        
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

  lifecycle {
    ignore_changes = [
      template[0].spec[0].containers[0].image,
    ]
  }
}

resource "google_cloud_run_service_iam_member" "public_access" {
  location = google_cloud_run_service.tracerlensai_app.location
  project  = google_cloud_run_service.tracerlensai_app.project
  service  = google_cloud_run_service.tracerlensai_app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
