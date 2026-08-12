import type { Metadata } from 'next'

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://loudrr.com'
const BOT_USERNAME = 'loudrr_bot'

// Server-only. The analytics API is gated by X-API-Key; we call it in
// generateMetadata (server-side) so the key never reaches the browser.
const ANALYTICS_URL = process.env.LOUDRR_ANALYTICS_URL || ''
const ANALYTICS_KEY = process.env.LOUDRR_ANALYTICS_KEY || ''

type Enrichment = {
  score?: number
  followers: string[]
  followersCount?: number
}

// Best-effort enrichment. Any failure returns the empty shape — the card
// route treats every new param as optional, so the card still renders.
async function loadEnrichment(username: string): Promise<Enrichment> {
  if (!ANALYTICS_URL) return { followers: [] }
  const headers = ANALYTICS_KEY ? { 'X-API-Key': ANALYTICS_KEY } : undefined
  const base = ANALYTICS_URL.replace(/\/$/, '')
  const qs = `userName=${encodeURIComponent(username)}`

  const [profileRes, followersRes] = await Promise.allSettled([
    fetch(`${base}/v1/profile?${qs}`, { headers, next: { revalidate: 300 } }),
    fetch(`${base}/v1/top-followers?${qs}&k=10`, { headers, next: { revalidate: 300 } }),
  ])

  let score: number | undefined
  if (profileRes.status === 'fulfilled' && profileRes.value.ok) {
    const d = (await profileRes.value.json().catch(() => ({}))) as {
      found?: boolean
      score?: number
    }
    if (d.found && typeof d.score === 'number') score = d.score
  }

  let followers: string[] = []
  if (followersRes.status === 'fulfilled' && followersRes.value.ok) {
    const d = (await followersRes.value.json().catch(() => ({}))) as {
      users?: { username?: string }[]
    }
    followers = (d.users || [])
      .map((u) => (u.username || '').trim())
      .filter(Boolean)
      .slice(0, 10)
  }

  return { score, followers, followersCount: followers.length || undefined }
}

function buildCardUrl(base: string, username: string, e: Enrichment): string {
  const p = new URLSearchParams({ username })
  if (typeof e.score === 'number') p.set('score', String(Math.round(e.score)))
  if (e.followers.length) p.set('followers', e.followers.join(','))
  if (typeof e.followersCount === 'number') p.set('followersCount', String(e.followersCount))
  return `${base}/api/cards/waitlist?${p.toString()}`
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ username: string }>
}): Promise<Metadata> {
  const { username } = await params
  const enrichment = await loadEnrichment(username)
  const cardUrl = buildCardUrl(SITE_URL, username, enrichment)

  return {
    title: `@${username} joined the Loudrr waitlist`,
    description: 'Loudrr is a karma-based attention marketplace. Earn karma by engaging with posts. Spend karma to get engagement on yours.',
    openGraph: {
      title: `@${username} joined the Loudrr waitlist`,
      description: 'Join the waitlist for Loudrr - earn karma by engaging.',
      type: 'website',
      images: [
        {
          url: cardUrl,
          width: 1012,
          height: 638,
          alt: `@${username} on the Loudrr waitlist`,
        },
      ],
    },
    twitter: {
      card: 'summary_large_image',
      title: `@${username} joined the Loudrr waitlist`,
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
  const enrichment = await loadEnrichment(username)
  const cardUrl = buildCardUrl('', username, enrichment)
  const botLink = `https://t.me/${BOT_USERNAME}`

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
      {/* Card image */}
      <img
        src={cardUrl}
        alt={`@${username} on the Loudrr waitlist`}
        style={{
          width: '100%',
          maxWidth: '506px',
          borderRadius: '16px',
          border: '1px solid rgba(249, 84, 0, 0.3)',
          marginBottom: '32px',
        }}
      />

      {/* CTA */}
      <a
        href={botLink}
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
