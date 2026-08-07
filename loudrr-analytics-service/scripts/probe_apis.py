"""Live preflight before any real crawl. SPENDS a few cents on twitterapi.io.

Checks, in order:
  * twitterapi.io key works (one /user/info call)
  * wallet balance (/oapi/my/info) — funded? enough for 20 QPS (>=50k credits)?
  * Sorsa key quota status (for calibration readiness)

    python -m scripts.probe_apis

Run this ONCE the twitterapi.io wallet is funded. The crawl path is profiles-only
(no bulk following-IDs endpoint exists in the verified catalog); --pilot then
measures the true per-member cost off the balance delta.
"""
import asyncio

from app.clients.sorsa import SorsaClient, SorsaQuotaError
from app.clients.twitterapi import TwitterAPIClient
from app.core.config import settings


async def main() -> None:
    print("=== twitterapi.io ===")
    if not settings.twitterapi_io_key:
        print("  ! TWITTERAPI_IO_KEY not set")
    else:
        tw = TwitterAPIClient()
        prof = await tw.get_user_info("elonmusk")
        print(f"  /user/info ok: id={prof and prof.get('id')}")
        try:
            credits = await tw.total_credits()
            qps = "20 QPS unlocked" if credits >= 50_000 else "throttled (<50k credits)"
            print(f"  wallet balance: {credits:,} credits (${credits / 100_000:.2f}) -> {qps}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! balance read failed: {e}")
        print("  crawl mode = profiles ($0.01/1k); --pilot measures true cost")
        print(f"  probe spend: ${tw.usd_spent:.4f}")

    print("=== sorsa (calibration) ===")
    if not settings.sorsa_key:
        print("  ! SORSA_KEY not set")
    else:
        sorsa = SorsaClient()
        try:
            print(f"  key usage: {await sorsa.key_usage_info()}")
            # LINCHPIN: does /top-followers expose a usable per-account score? (their
            # docs contradict themselves — this settles it before we trust the labels)
            probe = await sorsa.probe_top_followers_score("cz_binance")
            print(f"  top-followers score field usable: {probe['usable']} "
                  f"(value={probe.get('score_field_value')}, fields={probe.get('all_fields')})")
            # SCALE: API /score vs dashboard thousands-score for the same account
            api_score = await sorsa.score("cz_binance")
            dash = await sorsa.dashboard_score("cz_binance")
            print(f"  cz_binance: API /score={api_score}  dashboard={dash}")
            print("  -> use these to fix the API<->dashboard scale mapping in calibration")
        except SorsaQuotaError as e:
            print(f"  ! key out of quota -> top up before calibration: {e}")


if __name__ == "__main__":
    asyncio.run(main())
