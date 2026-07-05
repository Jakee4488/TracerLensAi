variable "project_id" {
  description = "The GCP project ID"
  type        = string
  default     = "enterprise-agent-demo"
}

variable "region" {
  description = "The default GCP region"
  type        = string
  default     = "europe-west2"
}

variable "cluster_name" {
  description = "Name of the GKE Autopilot cluster"
  type        = string
  default     = "agentic-orchestrator-cluster"
}

variable "network_name" {
  description = "The name of the VPC network"
  type        = string
  default     = "agent-vpc"
}

variable "github_repo" {
  description = "GitHub repository for Workload Identity Federation (format: owner/repo)"
  type        = string
  default     = "my-org/enterprise-agent-support"
}

variable "causal_artifact_repo_name" {
  description = "Artifact Registry name for Causal MLOps"
  type        = string
  default     = "causal-mlops-repo"
}

variable "causal_artifacts_bucket" {
  description = "GCS bucket name for Causal MLOps artifacts"
  type        = string
  default     = "tracerlens-causal-artifacts"
}
