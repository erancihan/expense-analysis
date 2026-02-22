VENV := venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install dev test docker-build docker-up docker-down clean

## Create virtual environment (only if it doesn't exist)
$(VENV):
	python -m venv $(VENV)

## Install dependencies
install: venv
	$(PIP) install -r requirements.txt

## Run the application directly from command line
dev: install
	$(PYTHON) -m src.main

## Run in manual mode (step through emails one by one)
dev-manual: install
	$(PYTHON) -m src.main --manual

## Run tests
test: install
	$(PYTHON) -m src.test_verification

## Build Docker image
docker-build:
	docker-compose build

## Start Docker container in background
docker-up:
	docker-compose up -d

## Stop Docker container
docker-down:
	docker-compose down

## Remove virtual environment and cached files
clean:
	rm -rf $(VENV)
	find . -type d -name '__pycache__' -exec rm -rf {} +
