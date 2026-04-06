.PHONY: dev test lint daemon-install daemon-uninstall daemon-status menubar-install menubar-uninstall widget widget-install widget-uninstall openwebui-start openwebui-stop

PLIST_NAME := com.deadline-agent.plist
MENUBAR_PLIST := com.deadline-agent-menubar.plist
WIDGET_PLIST := com.deadline-agent-widget.plist
PLIST_DEST := $(HOME)/Library/LaunchAgents/$(PLIST_NAME)
MENUBAR_DEST := $(HOME)/Library/LaunchAgents/$(MENUBAR_PLIST)
WIDGET_DEST := $(HOME)/Library/LaunchAgents/$(WIDGET_PLIST)
VENV_PYTHON := $(shell which python)

dev:
	uvicorn deadline_agent.main:app --reload --host 0.0.0.0 --port 8000

test:
	python -m pytest --cov=deadline_agent --cov-report=term-missing

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/
	mypy src/

daemon-install:
	@mkdir -p $(HOME)/.deadline-agent/logs
	@sed -e 's|__VENV_PYTHON__|$(VENV_PYTHON)|g' \
	     -e 's|__PROJECT_DIR__|$(CURDIR)|g' \
	     -e 's|__HOME__|$(HOME)|g' \
	     support/$(PLIST_NAME) > $(PLIST_DEST)
	-launchctl bootout gui/$$(id -u) $(PLIST_DEST) 2>/dev/null
	launchctl bootstrap gui/$$(id -u) $(PLIST_DEST)
	@echo "Daemon installed and loaded."

daemon-uninstall:
	-launchctl bootout gui/$$(id -u) $(PLIST_DEST)
	-rm -f $(PLIST_DEST)
	@echo "Daemon unloaded and removed."

daemon-status:
	@launchctl list | grep deadline-agent || echo "Daemon is not loaded."

menubar-install:
	@mkdir -p $(HOME)/.deadline-agent/logs
	@sed -e 's|__VENV_PYTHON__|$(VENV_PYTHON)|g' \
	     -e 's|__PROJECT_DIR__|$(CURDIR)|g' \
	     -e 's|__HOME__|$(HOME)|g' \
	     support/$(MENUBAR_PLIST) > $(MENUBAR_DEST)
	-launchctl bootout gui/$$(id -u) $(MENUBAR_DEST) 2>/dev/null
	launchctl bootstrap gui/$$(id -u) $(MENUBAR_DEST)
	@echo "Menubar app installed and loaded."

menubar-uninstall:
	-launchctl bootout gui/$$(id -u) $(MENUBAR_DEST)
	-rm -f $(MENUBAR_DEST)
	@echo "Menubar app unloaded and removed."

widget:
	deadline-agent-widget

widget-install:
	@mkdir -p $(HOME)/.deadline-agent/logs
	@sed -e 's|__VENV_PYTHON__|$(VENV_PYTHON)|g' \
	     -e 's|__PROJECT_DIR__|$(CURDIR)|g' \
	     -e 's|__HOME__|$(HOME)|g' \
	     support/$(WIDGET_PLIST) > $(WIDGET_DEST)
	-launchctl bootout gui/$$(id -u) $(WIDGET_DEST) 2>/dev/null
	launchctl bootstrap gui/$$(id -u) $(WIDGET_DEST)
	@echo "Widget installed and loaded."

widget-uninstall:
	-launchctl bootout gui/$$(id -u) $(WIDGET_DEST)
	-rm -f $(WIDGET_DEST)
	@echo "Widget unloaded and removed."

openwebui-start:
	@echo "Starting Open WebUI on http://localhost:3000 ..."
	@echo "Make sure 'make dev' is running on port 8000 for the chat API."
	@echo "Ollama must be running on port 11434."
	docker run -d --name deadline-agent-webui \
		-p 3000:8080 \
		-e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
		-v open-webui-data:/app/backend/data \
		--add-host=host.docker.internal:host-gateway \
		--restart unless-stopped \
		ghcr.io/open-webui/open-webui:main
	@echo "Open WebUI running at http://localhost:3000"

openwebui-stop:
	-docker stop deadline-agent-webui
	-docker rm deadline-agent-webui
	@echo "Open WebUI stopped."
