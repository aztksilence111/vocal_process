from __future__ import annotations

import sys

from audio_processor.gui import main as gui_main


if __name__ == "__main__":
    if len(sys.argv) > 1:
        from audio_processor.cli import main as cli_main

        raise SystemExit(cli_main(sys.argv[1:]))

    raise SystemExit(gui_main())
