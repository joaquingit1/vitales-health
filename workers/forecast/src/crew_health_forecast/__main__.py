import argparse

import uvicorn

from crew_health_forecast.adapters.chronos_backend import ChronosBackend
from crew_health_forecast.adapters.settings import WorkerSettings
from crew_health_forecast.api.app import create_app
from crew_health_forecast.application.policy import load_policy
from crew_health_forecast.application.service import ForecastService


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline experimental trend worker")
    parser.add_argument("--container-network", action="store_true")
    args = parser.parse_args()
    settings = WorkerSettings.from_env()
    settings.validate_bind(args.container_network)
    policy = load_policy(settings.policy_path)
    backend = ChronosBackend.load(settings.weights_dir, policy)
    service = ForecastService(policy, backend, settings.max_inflight)
    uvicorn.run(
        create_app(service, settings, settings.load_token()),
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
