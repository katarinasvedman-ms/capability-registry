import os

import uvicorn

if __name__ == "__main__":
    module = {"gateway": "gateway.app", "consumer": "consumer.app", "mock": "mocks.secured"}[
        os.environ["SERVICE"]
    ]
    uvicorn.run(f"{module}:app", host="0.0.0.0", port=8088, timeout_graceful_shutdown=20)
