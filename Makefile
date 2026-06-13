COMPOSE := docker compose

.PHONY: help up down delete wipe logs ps

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up: ## Start the Docker Compose application (detached)
	$(COMPOSE) up -d

down: ## Stop and remove containers (keeps the neo4j_data volume)
	$(COMPOSE) down

delete: ## Stop and remove containers AND delete volumes (wipes data)
	$(COMPOSE) down -v

wipe: ## Full teardown: containers, volumes, orphans, and built images
	$(COMPOSE) down -v --remove-orphans --rmi local

logs: ## Tail logs from running services
	$(COMPOSE) logs -f

ps: ## Show status of services
	$(COMPOSE) ps
