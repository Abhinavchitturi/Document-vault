"""Download Needle 3's files once (about 35 MB), so the vault can read facts with the
internet off afterwards:

    pip install cactus-needle
    python -m backend.extract.needle_setup

Needle's anonymous usage counts are switched off. After this, the vault uses Needle 3 for
expiry dates, amounts and numbers; check with: python -m backend.eval.facts_eval
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ["NEEDLE_TELEMETRY"] = "0"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ.pop("HF_HUB_OFFLINE", None)  # this one command may use the internet
    try:
        import needle
    except ImportError:
        sys.exit("Needle isn't installed. Run: pip install cactus-needle")
    from backend.extract.facts import SCHEMA

    print("Downloading Needle 3 (about 35 MB, once)...")
    answer = needle.extract("Electricity bill. Total amount payable: Rs. 2,486. Due Date: 18 September 2026.",
                            SCHEMA, strict=False)
    print(f"Needle 3 is ready. Test reading: {answer}")


if __name__ == "__main__":
    main()
