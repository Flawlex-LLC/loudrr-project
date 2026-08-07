import type { Metadata } from 'next'

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://loudrr.com'
const BOT_USERNAME = 'loudrr_bot'

export async function generateMetadata({
  params,
}: {
  params: Promise<{ username: string }>
}): Promise<Metadata> {
  const { username } = await params
  const cardUrl = `${SITE_URL}/api/cards/waitlist?username=${encodeURIComponent(username)}`

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
  const cardUrl = `/api/cards/waitlist?username=${encodeURIComponent(username)}`
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
