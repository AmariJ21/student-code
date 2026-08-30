#!/usr/bin/env python3
"""Entry point: python main.py <ingest|update|backtest|analyze|report|optimize> ..."""

from ctbacktest.cli.main import cli

if __name__ == "__main__":
    cli()
