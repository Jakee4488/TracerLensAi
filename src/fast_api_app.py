# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import os
from collections.abc import AsyncIterator

import google.auth
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.cloud import logging as google_cloud_logging

from src.agent import adk_app, root_agent
from src.app_utils import services
from src.app_utils.a2a import attach_a2a_routes
from src.app_utils.reasoning_engine_adapter import (
    attach_reasoning_engine_routes,
)
from src.app_utils.telemetry import (
    setup_agent_engine_telemetry,
    setup_telemetry,
)
from src.app_utils.typing import Feedback

load_dotenv()


class WrongServiceDeployment(RuntimeError):
    """This agent image is running on the service that should host the site."""


# Set only on the proxy service (see proxy/access.py): the signing key for the
# access gate and the admin dashboard password. Neither has any meaning to this
# agent, so finding one here means this container was deployed over the site.
_PROXY_ONLY_ENV = ("ACCESS_SIGNING_SECRET", "ADMIN_TOKEN")


def _refuse_if_deployed_over_the_site() -> None:
    """Fail this revision at boot rather than silently replacing the website.

    Two images are built from this repo: Dockerfile.proxy runs proxy.main and
    is the public site (it is also the only one that bakes in ui/dist), while
    the root Dockerfile runs this module and is the agent. A bare
    `gcloud run deploy --source .` builds the *root* Dockerfile, so it happily
    ships the agent to whichever service it is pointed at.

    That is not a hypothetical: it took tracerlensai.com down after the image
    landed on the site's service. Nothing caught it, because this app answers
    /health perfectly well — the startup probe passed, the revision took 100%
    of traffic, and the site's own routes simply 404'd from then on.

    Raising here turns that silent swap into a failed revision: Cloud Run keeps
    serving the previous one, and the deploy reports the error instead of the
    visitors discovering it. Deploy through deploy_to_gcp.sh, which builds each
    image against the service that expects it.

    Only enforced on a managed runtime. Locally these variables are ordinary
    things to have in a shell or .env while working on the proxy, and refusing
    to boot there would punish the wrong situation entirely.
    """
    # Cloud Run / Functions / App Engine set these themselves, so this needs no
    # configuration of its own — a variable you must remember to set cannot be
    # what catches the variable you forgot. Duplicated from proxy/access.py's
    # is_managed_runtime() rather than imported: proxy/ is not copied into this
    # image, so importing from it would fail exactly where this must work.
    managed = bool(
        os.getenv("K_SERVICE") or os.getenv("GAE_ENV") or os.getenv("FUNCTION_TARGET")
    )
    if not managed or os.getenv("ALLOW_AGENT_ON_PROXY_SERVICE"):
        return

    found = [name for name in _PROXY_ONLY_ENV if os.getenv(name)]
    if not found:
        return

    raise WrongServiceDeployment(
        f"Refusing to start: {', '.join(found)} is set, which belongs to the "
        f"proxy service that serves the public site — this is the agent image "
        f"(src.fast_api_app), built from the root Dockerfile. Deploying it here "
        f"replaces the website with an API that has none of its routes. Build "
        f"the site from Dockerfile.proxy (deploy_to_gcp.sh does this), and note "
        f"that a bare `gcloud run deploy --source .` will always build the root "
        f"Dockerfile. To override deliberately, set "
        f"ALLOW_AGENT_ON_PROXY_SERVICE=1."
    )


# Before anything expensive: this is a configuration mistake, and it should cost
# a failed import rather than a resolved credential and a built agent.
_refuse_if_deployed_over_the_site()

setup_telemetry()
# Must run before get_fast_api_app to set the tracer provider resource.
setup_agent_engine_telemetry()
# Called for its side effect: fail fast at import if ADC cannot be resolved,
# rather than on the first request. The returned values are unused.
google.auth.default()
logging_client = google_cloud_logging.Client()
logger = logging_client.logger(__name__)
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runner for the A2A path, sharing the same session/artifact services as the
    # adk_api and reasoning_engine paths (see services.py). Imported here so the
    # agent is built after env/telemetry setup.
    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    # Shared by the A2A path and the reasoning_engine adapter routes.
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=False,
    lifespan=lifespan,
)
app.title = "my-test-agent"
app.description = "API for interacting with the Agent my-test-agent"


# Proxy routes so the Vertex AI Console Playground (reasoning_engine SDK) can
# talk to this agent alongside the native adk_api routes.
attach_reasoning_engine_routes(app)


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback.

    Args:
        feedback: The feedback data to log

    Returns:
        Success message
    """
    logger.log_struct(feedback.model_dump(), severity="INFO")
    return {"status": "success"}


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
