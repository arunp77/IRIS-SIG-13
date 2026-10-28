# Shortcuts for Linux and macOS. Everything is done by tasks.py, which also works on Windows:
#   python tasks.py setup      (instead of: make setup)
# Use another interpreter for the setup with:  make setup PY=python3.12

PY ?= python3

.PHONY: help setup raw fetch reproduce demo test harvest validate qa ui clean

help:
	@$(PY) tasks.py

setup raw fetch reproduce demo test harvest validate qa ui clean:
	@$(PY) tasks.py $@
