import type { Metadata } from 'next'

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://loudrr.com'
const BOT_APP_URL = 'https://t.me/loudrr_bot/app'

// Server-only: the backend serves the STORED card for handles that are on
// the waitlist (GET /waitlist/card/<handle>/) — the same score the applicant
// sees in the mini-app. Never the legacy analytics service.
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || ''

type PublicCard = {
  x_username: string
  score: number | null
  tier: string | null
  followers: string[]
  followers_count: number
  referral_code: string
}

const HANDLE_RE = /^[A-Za-z0-9_]{1,15}$/

// null = not on the waitlist (or backend unreachable): the page then makes no
// "@handle joined" claim and shows a plain invite instead.
async function loadCard(username: string): Promise<PublicCard | null> {
  if (!BACKEND_ORIGIN || !HANDLE_RE.test(username)) return null
  try {
    const res = await fetch(
      `${BACKEND_ORIGIN.replace(/\/$/, '')}/waitlist/card/${encodeURIComponent(username)}/`,
      { next: { revalidate: 60 }, signal: AbortSignal.timeout(4000) },
    )
    if (!res.ok) return null
    return (await res.json()) as PublicCard
  } catch {
    return null
  }
}

function buildCardUrl(base: string, username: string, card: PublicCard | null): string {
  const p = new URLSearchParams({ username })
  if (card) {
    if (typeof card.score === 'number') p.set('score', String(Math.floor(card.score)))
    if (card.tier) p.set('tier', card.tier)
    if (card.followers.length) {
      p.set('followers', card.followers.join(','))
      p.set('followersCount', String(card.followers_count))
    }
  }
  return `${base}/api/cards/waitlist?${p.toString()}`
}

// the join link carries the sharer's referral code, so a signup from their
// post is credited to them
function joinUrl(card: PublicCard | null): string {
  return card?.referral_code ? `${BOT_APP_URL}?startapp=ref_${card.referral_code}` : BOT_APP_URL
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ username: string }>
}): Promise<Metadata> {
  const { username } = await params
  const card = await loadCard(username)
  const description = 'Loudrr is a karma-based attention marketplace. Earn karma by engaging with posts. Spend karma to get engagement on yours.'

  if (!card) {
    return {
      title: 'Join the Loudrr waitlist',
      description,
      openGraph: { title: 'Join the Loudrr waitlist', description, type: 'website' },
    }
  }

  const handle = card.x_username
  const cardUrl = buildCardUrl(SITE_URL, handle, card)
  return {
    title: `@${handle} joined the Loudrr waitlist`,
    description,
    openGraph: {
      title: `@${handle} joined the Loudrr waitlist`,
      description: 'Join the waitlist for Loudrr - earn karma by engaging.',
      type: 'website',
      images: [
        {
          url: cardUrl,
          width: 1012,
          height: 638,
          alt: `@${handle} on the Loudrr waitlist`,
        },
      ],
    },
    twitter: {
      card: 'summary_large_image',
      title: `@${handle} joined the Loudrr waitlist`,
      description: 'Join the waitlist for Loudrr - earn karma by engaging.',
      images: [cardUrl],
    },
  }
}

export default async function WaitlistSharePage({
  params,
}: {
  params: Promise<{ username: string }>
}) {
  const { username } = await params
  const card = await loadCard(username)

  return (
    <div
      style={{
        minHeight: '100vh',
        background: '#0a0a0a',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
      }}
    >
      {/* Card image — only for handles actually on the waitlist */}
      {card && (
        <img
          src={buildCardUrl('', card.x_username, card)}
          alt={`@${card.x_username} on the Loudrr waitlist`}
          style={{
            width: '100%',
            maxWidth: '506px',
            borderRadius: '16px',
            border: '1px solid rgba(249, 84, 0, 0.3)',
            marginBottom: '32px',
          }}
        />
      )}

      {/* CTA */}
      <a
        href={joinUrl(card)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '8px',
          padding: '16px 32px',
          background: '#f95400',
          color: '#fff',
          borderRadius: '12px',
          fontSize: '18px',
          fontWeight: 700,
          textDecoration: 'none',
          fontFamily: 'var(--font-syne), sans-serif',
        }}
      >
        Join Loudrr on Telegram
      </a>
    </div>
  )
}
