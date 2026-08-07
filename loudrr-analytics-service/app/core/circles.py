"""Loudrr Circles — our own taxonomy over the vendors' messy category labels.

TwitterScore ships 10 categories, Sorsa ships 3. Neither is ours, and neither is a good
product surface: "Venture Capitals" vs "VC/Fund Team" vs "Angels" are three names for the same
idea, and Sorsa's "Influencers" quietly bundles press with KOLs.

Five circles, decided with the founder (2026-07-16):

  INVESTOR    — the people writing cheques: Angels, Venture Capitals, VC/Fund Team, Sorsa VCs
  BUILDER     — the people shipping: Founders, Projects, Project Team, Sorsa Projects
  INFLUENCER  — KOLs ONLY. In web3 "influencer" reads as "KOL", so press/PR must not sit here
                or the label lies (founder's call, and correct).
  INFRA       — the rails: Exchanges, Auditors, wallet teams. Not investors, not builders,
                not talking heads.
  MEDIA       — press/PR outlets. Small (~343) but semantically distinct from a KOL; folding
                it into INFLUENCER would misrepresent both.

An account can hold several vendor categories, so it can belong to several circles — that's
intentional (a founder who angel-invests is genuinely both).
"""
from __future__ import annotations

from enum import Enum


class Circle(str, Enum):
    INVESTOR = "investor"
    BUILDER = "builder"
    INFLUENCER = "influencer"
    INFRA = "infra"
    MEDIA = "media"


# Display labels for the UI ("Circle" is the product word; internal code uses the enum).
CIRCLE_LABEL: dict[Circle, str] = {
    Circle.INVESTOR: "Investor Circle",
    Circle.BUILDER: "Builder Circle",
    Circle.INFLUENCER: "Influencer Circle",
    Circle.INFRA: "Infra Circle",
    Circle.MEDIA: "Media Circle",
}

# Vendor label (lowercased, trimmed) -> circle. Covers TwitterScore's 10 observed categories
# and Sorsa's 3. Unknown labels map to nothing rather than being force-fitted — a wrong circle
# is worse than no circle.
_VENDOR_TO_CIRCLE: dict[str, Circle] = {
    # --- investors ---
    "angels": Circle.INVESTOR,
    "venture capitals": Circle.INVESTOR,
    "vc/fund team": Circle.INVESTOR,
    "vc": Circle.INVESTOR,                      # sorsa
    "venture_capitals": Circle.INVESTOR,        # sorsa api field style
    # --- builders ---
    "founders": Circle.BUILDER,
    "projects": Circle.BUILDER,
    "project team": Circle.BUILDER,
    # --- influencers (KOLs only) ---
    "influencers": Circle.INFLUENCER,
    # --- infra ---
    "exchanges": Circle.INFRA,
    "auditors": Circle.INFRA,
    "wallets": Circle.INFRA,
    "wallet team": Circle.INFRA,
    # --- media ---
    "media": Circle.MEDIA,
}


def circle_for(vendor_category: str) -> Circle | None:
    """One vendor label -> its circle, or None if we don't recognise it."""
    if not vendor_category:
        return None
    return _VENDOR_TO_CIRCLE.get(vendor_category.strip().lower())


def circles_for(categories: str | None) -> list[Circle]:
    """A stored `categories` string ("Angels, Project Team") -> the circles it belongs to.

    Order is stable (INVESTOR, BUILDER, INFLUENCER, INFRA, MEDIA) so UI output doesn't jitter
    between requests. Unrecognised labels are dropped, never guessed at.
    """
    if not categories:
        return []
    found = {c for part in categories.split(",") if (c := circle_for(part)) is not None}
    return [c for c in Circle if c in found]


def labels_for(categories: str | None) -> list[str]:
    """Display labels for the UI, e.g. ["Investor Circle", "Builder Circle"]."""
    return [CIRCLE_LABEL[c] for c in circles_for(categories)]
