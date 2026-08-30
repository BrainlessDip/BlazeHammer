#!/usr/bin/env python
"""Legacy entry point — preserved for backwards compatibility.

Prefer the installed CLI::

    blaze-hammer run <URL> [options]

All old invocations keep working::

    python main.py https://example.com/api -n 200 -c 10 -m POST -pp
    python main.py --json-diff payload_example.json
"""

import sys

if __name__ == "__main__":
    from blaze_hammer.cli.main import main

    sys.exit(main())
