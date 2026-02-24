.PHONY: install crawl extract debug help

help:
	@echo "canvas-transcriber — common targets"
	@echo ""
	@echo "  make install                 Install Python deps and Playwright browser"
	@echo "  make crawl URL=<url>         Crawl a Canvas /modules page"
	@echo "  make extract                 Extract transcripts from links_output.json"
	@echo "  make extract RETRY=1         Retry only previously failed videos"
	@echo "  make debug                   Deep-inspect first video (debug mode)"
	@echo ""
	@echo "Examples:"
	@echo "  make crawl URL=https://<school>.instructure.com/courses/<id>/modules"
	@echo "  make extract"

install:
	pip install -r requirements.txt
	playwright install chromium

crawl:
	@test -n "$(URL)" || (echo "Usage: make crawl URL=<canvas-modules-url>" && exit 1)
	python cli.py crawl-course "$(URL)"

extract:
	python cli.py extract-video $(if $(RETRY),--retry-failed,)

debug:
	python cli.py extract-video --debug
