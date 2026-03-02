# n8n Nextcloud ExApp - Build System

REGISTRY ?= ghcr.io/conductionnl
IMAGE_NAME ?= n8n-nextcloud
VERSION ?= 1.0.0

.PHONY: build push run test clean help

help:
	@echo "n8n Nextcloud ExApp"
	@echo ""
	@echo "Usage:"
	@echo "  make build    - Build Docker image"
	@echo "  make push     - Push to registry"
	@echo "  make run      - Run locally for testing"
	@echo "  make test     - Test endpoints"
	@echo "  make clean    - Remove local images"
	@echo ""
	@echo "Variables:"
	@echo "  REGISTRY=$(REGISTRY)"
	@echo "  VERSION=$(VERSION)"

build:
	docker build -t $(REGISTRY)/$(IMAGE_NAME):$(VERSION) -t $(REGISTRY)/$(IMAGE_NAME):latest .

push: build
	docker push $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
	docker push $(REGISTRY)/$(IMAGE_NAME):latest

run:
	docker run -it --rm \
		-e APP_ID=n8n \
		-e APP_SECRET=dev-secret \
		-e APP_HOST=0.0.0.0 \
		-e APP_PORT=23000 \
		-e APP_PERSISTENT_STORAGE=/data \
		-e NEXTCLOUD_URL=http://host.docker.internal:8080 \
		-p 23000:23000 \
		-p 5678:5678 \
		$(REGISTRY)/$(IMAGE_NAME):latest

test:
	@echo "Testing heartbeat endpoint..."
	@curl -s http://localhost:23000/heartbeat || echo "Container not running"
	@echo ""
	@echo "Testing n8n health..."
	@curl -s http://localhost:5678/healthz || echo "n8n not running"

clean:
	-docker rmi $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
	-docker rmi $(REGISTRY)/$(IMAGE_NAME):latest
