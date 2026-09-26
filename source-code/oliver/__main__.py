"""Allow `python3 -m oliver ...` as a shortcut for `python3 -m oliver.main ...`."""

import sys

from oliver import main

if __name__ == '__main__':
    sys.exit(main.main(sys.argv[1:]))
