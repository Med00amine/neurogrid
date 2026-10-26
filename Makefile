.PHONY: setup up down logs db migrate admin doctor test test-backend test-frontend e2e build backup restore ps

setup:            ## first time: create .env with generated secrets
	./scripts/setup.sh
build:            ## build the image
	docker compose build
up:               ## start everything: http://localhost:8000
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f --tail=100 api worker
ps:
	docker compose ps
db:               ## development: database only
	docker compose up -d db
migrate:          ## apply database migrations (development)
	cd backend && . .venv/bin/activate && alembic upgrade head
admin:            ## create a user inside the running container: make admin EMAIL=you@example.com
	docker compose exec api python -m app.cli create-admin --email $(EMAIL)
doctor:           ## configuration checklist (never prints secrets)
	docker compose exec api python -m app.cli doctor
test-backend:     ## needs `make db`
	cd backend && . .venv/bin/activate && python -m pytest
test-frontend:
	cd frontend && npm run typecheck && npm test
e2e:              ## browser tests (needs `make db`, `cd frontend && npm run build`)
	cd frontend && npx playwright test
test: test-backend test-frontend e2e
backup:
	./scripts/backup.sh
restore:          ## make restore FILE=backups/neurogrid-....dump
	./scripts/restore.sh $(FILE)
