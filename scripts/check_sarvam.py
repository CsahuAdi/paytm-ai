"""One tiny text-lid call (about Rs 0.001) to see whether Sarvam has credit: python scripts/check_sarvam.py"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sarvamai import AsyncSarvamAI  # noqa: E402

from app import config  # noqa: E402


async def main() -> None:
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY, timeout=15)
    try:
        r = await client.text.identify_language(input="मेरा ऑर्डर कहाँ है?")
        print(f"OK Sarvam is working (text-lid -> {r.language_code})")
    except Exception as e:  # noqa: BLE001
        print(f"DOWN {getattr(e, 'status_code', '')} {getattr(e, 'body', e)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
