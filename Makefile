SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

.PHONY: help test fmt build

help:
	@printf '%s\n' \
	  'signal reminder bot application' \
	  '' \
	  '  make test   Run the application tests.' \
	  '  make fmt    Validate Python and shell syntax.' \
	  '  make build  Build the portable container image.'

test:
	@python3 -m unittest discover -s tests

fmt:
	@python3 -m compileall -q app main.py
	@git diff --check

build:
	@docker build -t signal-reminder-bot .
