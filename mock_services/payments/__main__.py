"""Run the mock provider: uv run python -m mock_services.payments"""

import uvicorn

from mock_services.payments.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=9001, log_level="info")


if __name__ == "__main__":
    main()
