"""Build the gated promotion candidate list from discovered + enriched profiles.
Gate = rank by unified_score (quality-weighted), drop dead/bots, soft floor >=30 elite. Local CSVs only."""
import csv

FLOOR = 30          # soft min distinct elite followers (anti single-whale-fluke)
BOT_FOLLOWING = 50000


def load(path, key="user_id"):
    return {r[key]: r for r in csv.DictReader(open(path, encoding="utf-8"))}


def main():
    disc = list(csv.DictReader(open("data/exports/discovered_accounts.csv", encoding="utf-8")))
    prof = load("data/exports/profiles_enriched.csv")

    kept, dropped = [], {"dead": 0, "bot": 0, "floor": 0, "no_profile": 0}
    for d in disc:
        uid = d["user_id"]
        p = prof.get(uid)
        ef = int(d["elite_followers"])
        if not p:
            dropped["no_profile"] += 1; continue
        if p.get("dead") == "1":
            dropped["dead"] += 1; continue
        following = int(p["following"]) if p.get("following", "").isdigit() else 0
        if following > BOT_FOLLOWING:
            dropped["bot"] += 1; continue
        if ef < FLOOR:
            dropped["floor"] += 1; continue
        kept.append({
            "user_id": uid, "username": d["username"], "unified_score": float(d["unified_score"]),
            "elite_followers": ef, "following": following,
            "x_followers": int(p["followers"]) if p.get("followers", "").isdigit() else 0,
            "crypto_flag": p.get("crypto_flag", ""), "blue": p.get("blue_verified", ""),
            "sorsa": d["sorsa"], "twitterscore": d["twitterscore"],
        })

    kept.sort(key=lambda r: -r["unified_score"])
    with open("data/exports/gated_promotion.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        cols = ["rank", "user_id", "username", "unified_score", "unified_div5", "elite_followers",
                "following", "x_followers", "crypto_flag", "blue", "sorsa", "twitterscore"]
        w.writerow(cols)
        for i, r in enumerate(kept, 1):
            w.writerow([i, r["user_id"], r["username"], round(r["unified_score"]),
                        round(r["unified_score"] / 5), r["elite_followers"], r["following"],
                        r["x_followers"], r["crypto_flag"], r["blue"], r["sorsa"], r["twitterscore"]])

    print(f"discovered considered : {len(disc):,}")
    print(f"  dropped dead        : {dropped['dead']}")
    print(f"  dropped bot(>50k fol): {dropped['bot']}")
    print(f"  dropped <{FLOOR} elite : {dropped['floor']}")
    print(f"  dropped no-profile   : {dropped['no_profile']}")
    print(f"KEPT (gated promotion) : {len(kept):,}  -> data/exports/gated_promotion.csv\n")

    print("depth buckets — pick where to cut (quality holds, vendor coverage thins):")
    print(f"{'cut@':>7}{'unified/5':>11}{'elite_fol':>11}{'%crypto':>9}{'%hasSorsa':>11}")
    for cut in [5000, 10000, 15000, 20000, 25000, len(kept)]:
        if cut > len(kept):
            continue
        seg = kept[max(0, cut - 250):cut]
        u = sum(x["unified_score"] for x in seg) / len(seg) / 5
        ef = sum(x["elite_followers"] for x in seg) / len(seg)
        cr = 100 * sum(1 for x in seg if x["crypto_flag"] == "1") / len(seg)
        hs = 100 * sum(1 for x in seg if x["sorsa"] != "NIL") / len(seg)
        print(f"{cut:>7}{u:>11.0f}{ef:>11.0f}{cr:>8.0f}%{hs:>10.0f}%")


main()
