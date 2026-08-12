import { ImageResponse } from '@vercel/og'
import { NextRequest } from 'next/server'

export const runtime = 'edge'

// Google Fonts URLs (TTF format)
const SPACE_GROTESK_BOLD_URL = 'https://fonts.gstatic.com/s/spacegrotesk/v22/V8mQoQDjQSkFtoMM3T6r8E7mF71Q-gOoraIAEj4PVksj.ttf'
const SYNE_BOLD_URL = 'https://fonts.gstatic.com/s/syne/v24/8vIS7w4qzmVxsWxjBZRjr0FKM_3fvj6k.ttf'

// Loudrr Score tiers — mirrors backend/app/services/tier.py thresholds.
// Score scale is 0-6000 but all tier bands live in 0..1000; anything above is GOAT.
function tierFor(score: number): string {
  if (score >= 1000) return 'GOAT'
  if (score >= 800) return 'OG'
  if (score >= 600) return 'LEGEND'
  if (score >= 400) return 'BASED'
  if (score >= 200) return 'DEGEN'
  if (score >= 100) return 'NORMIE'
  return 'ANON'
}

// Constellation slots — matches the Variant C mock (10 × 48px avatars,
// staggered baseline so they read as scattered stars, not a bar chart).
const SLOTS: { left: number; top: number }[] = [
  { left: 0,   top: 6  },
  { left: 52,  top: -2 },
  { left: 104, top: 10 },
  { left: 158, top: 2  },
  { left: 212, top: 8  },
  { left: 266, top: -4 },
  { left: 320, top: 12 },
  { left: 374, top: 4  },
  { left: 426, top: -2 },
  { left: 478, top: 9  },
]

export async function GET(request: NextRequest) {
  // Load fonts in parallel
  const [syneFontData, spaceGroteskFontData] = await Promise.all([
    fetch(SYNE_BOLD_URL).then(res => res.arrayBuffer()),
    fetch(SPACE_GROTESK_BOLD_URL).then(res => res.arrayBuffer()),
  ])

  // Build logo URL from request origin (works in dev + prod)
  const url = new URL(request.url)
  const LOGO_URL = `${url.origin}/loudrr-icon-small.png`

  const { searchParams } = url
  const xUsername = searchParams.get('username') || 'user'
  const displayName = searchParams.get('displayName') || xUsername
  const telegramUsername = searchParams.get('telegram') || ''

  // New Variant C params — all optional so old callers still render a valid card.
  const scoreRaw = searchParams.get('score')
  const scoreNum = scoreRaw !== null && scoreRaw !== '' ? Number(scoreRaw) : NaN
  const hasScore = Number.isFinite(scoreNum)
  const scoreLabel = hasScore ? Math.round(scoreNum).toLocaleString('en-US') : ''
  const tierLabel = (searchParams.get('tier') || (hasScore ? tierFor(scoreNum) : '')).toUpperCase()

  // Comma-separated X usernames of top smart followers. We proxy avatars
  // through unavatar.io/x/<u> — deterministic per username, cached,
  // no need to plumb image URLs through the analytics API.
  const followersRaw = searchParams.get('followers') || ''
  const followerUsernames = followersRaw
    .split(',')
    .map(s => s.trim().replace(/^@/, ''))
    .filter(Boolean)
    .slice(0, 10)
  const followersCount = Number(searchParams.get('followersCount') || followerUsernames.length) || 0

  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#000000',
          padding: '16px 20px',
        }}
      >
        {/* Card with clean credit-card shape */}
        <div
          style={{
            width: '100%',
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'linear-gradient(180deg, #0e0e10 0%, #08080a 100%)',
            borderRadius: '24px',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            position: 'relative',
            overflow: 'hidden',
            padding: '40px 60px 32px',
          }}
        >
          {/* Ambient glow - top */}
          <div
            style={{
              position: 'absolute',
              top: '-100px',
              left: '20%',
              width: '600px',
              height: '500px',
              background: 'radial-gradient(ellipse at center, rgba(249, 84, 0, 0.10) 0%, transparent 60%)',
            }}
          />

          {/* Ambient glow - bottom */}
          <div
            style={{
              position: 'absolute',
              bottom: '-100px',
              right: '15%',
              width: '700px',
              height: '500px',
              background: 'radial-gradient(ellipse at center, rgba(249, 84, 0, 0.07) 0%, transparent 60%)',
            }}
          />

          {/* Card inner gradient overlay */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '972px',
              height: '606px',
              background: 'radial-gradient(ellipse 80% 50% at 50% 0%, rgba(249, 84, 0, 0.06) 0%, transparent 50%)',
            }}
          />

          {/* Halftone dots */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '972px',
              height: '606px',
              backgroundImage: `url("data:image/svg+xml,%3Csvg width='6' height='6' xmlns='http://www.w3.org/2000/svg'%3E%3Ccircle cx='3' cy='3' r='0.8' fill='%23ffffff' fill-opacity='0.08'/%3E%3C/svg%3E")`,
              backgroundSize: '6px 6px',
            }}
          />

          {/* Glossy shine */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '972px',
              height: '606px',
              background: 'linear-gradient(110deg, transparent 0%, rgba(255,255,255,0.04) 15%, rgba(255,255,255,0.10) 35%, rgba(255,255,255,0.14) 50%, rgba(255,255,255,0.10) 65%, rgba(255,255,255,0.04) 85%, transparent 100%)',
            }}
          />

          {/* Top edge highlight */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '972px',
              height: '1px',
              background: 'linear-gradient(90deg, transparent 10%, rgba(255,255,255,0.15) 50%, transparent 90%)',
            }}
          />

          {/* Brand header with logo */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '14px',
              marginBottom: '14px',
              marginTop: '20px',
              zIndex: 1,
            }}
          >
            <img
              src={LOGO_URL}
              width={56}
              height={56}
              style={{
                objectFit: 'contain',
              }}
            />
            <span
              style={{
                fontSize: '36px',
                fontWeight: 700,
                color: '#f95400',
                fontFamily: 'Syne',
                letterSpacing: '-0.5px',
              }}
            >
              Loudrr
            </span>
          </div>

          {/* Main title */}
          <div
            style={{
              display: 'flex',
              marginBottom: '22px',
              zIndex: 1,
            }}
          >
            <span
              style={{
                fontSize: '52px',
                fontWeight: 700,
                color: '#ffffff',
                fontFamily: 'Space Grotesk',
                letterSpacing: '-1.5px',
              }}
            >
              You&#39;re on the Waitlist!
            </span>
          </div>

          {/* Profile pill + tier chip (Variant C: chip pinned like a merit sticker) */}
          <div
            style={{
              display: 'flex',
              position: 'relative',
              zIndex: 1,
              marginBottom: '22px',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '28px',
                padding: '24px 44px',
                background: 'linear-gradient(135deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0.06) 100%)',
                borderRadius: '24px',
                border: '1px solid rgba(255, 255, 255, 0.2)',
              }}
            >
              {/* Avatar — real X profile pic via unavatar proxy. Satori will
                  render the letter fallback (background + span) if the fetch
                  fails; the <img> layered on top hides it when successful. */}
              <div
                style={{
                  width: '80px',
                  height: '80px',
                  borderRadius: '50%',
                  background: 'rgba(255, 255, 255, 0.15)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                  position: 'relative',
                  overflow: 'hidden',
                  border: '2px solid rgba(255, 255, 255, 0.15)',
                }}
              >
                <span
                  style={{
                    fontSize: '34px',
                    fontWeight: 700,
                    color: '#ffffff',
                    fontFamily: 'Space Grotesk',
                  }}
                >
                  {displayName[0]?.toUpperCase() || 'U'}
                </span>
                <img
                  src={`https://unavatar.io/x/${xUsername}?fallback=false`}
                  width={80}
                  height={80}
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '80px',
                    height: '80px',
                    objectFit: 'cover',
                  }}
                />
              </div>

              {/* X username — verified because X OAuth is the first step of
                  the waitlist flow now, so we no longer need the '(to be verified)'
                  disclaimer. */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                }}
              >
                <svg width={26} height={26} viewBox="0 0 24 24" fill="#ffffff">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                </svg>
                <span style={{ fontSize: '32px', fontWeight: 700, color: '#ffffff', fontFamily: 'Space Grotesk', letterSpacing: '-0.5px' }}>
                  @{xUsername}
                </span>
              </div>
            </div>

            {/* Tier chip — only when we have a real score to show */}
            {hasScore && (
              <div
                style={{
                  position: 'absolute',
                  top: '-14px',
                  right: '-18px',
                  display: 'flex',
                  alignItems: 'baseline',
                  gap: '10px',
                  padding: '10px 18px',
                  background: 'linear-gradient(135deg, #ff7a2b 0%, #f95400 100%)',
                  borderRadius: '999px',
                  border: '1px solid rgba(255,255,255,0.2)',
                  boxShadow: '0 6px 24px rgba(249,84,0,0.55)',
                }}
              >
                <span
                  style={{
                    fontSize: '28px',
                    fontWeight: 700,
                    color: '#0a0a0a',
                    fontFamily: 'Syne',
                    letterSpacing: '-0.5px',
                    lineHeight: 1,
                  }}
                >
                  {scoreLabel}
                </span>
                <span
                  style={{
                    fontSize: '12px',
                    fontWeight: 700,
                    color: 'rgba(0,0,0,0.75)',
                    fontFamily: 'Space Grotesk',
                    letterSpacing: '2px',
                  }}
                >
                  {tierLabel}
                </span>
              </div>
            )}
          </div>

          {/* Follower constellation — top smart followers as scattered stars */}
          {followerUsernames.length > 0 && (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                zIndex: 1,
                marginBottom: '4px',
              }}
            >
              <div
                style={{
                  position: 'relative',
                  display: 'flex',
                  width: '526px',
                  height: '60px',
                }}
              >
                {followerUsernames.map((u, i) => {
                  const slot = SLOTS[i] || SLOTS[SLOTS.length - 1]
                  return (
                    <img
                      key={u}
                      src={`https://unavatar.io/x/${u}`}
                      width={48}
                      height={48}
                      style={{
                        position: 'absolute',
                        left: `${slot.left}px`,
                        top: `${slot.top + 6}px`,
                        width: '48px',
                        height: '48px',
                        borderRadius: '50%',
                        border: '2px solid rgba(10,10,10,0.9)',
                        boxShadow: '0 0 14px rgba(249,84,0,0.35)',
                        objectFit: 'cover',
                      }}
                    />
                  )
                })}
              </div>
              <span
                style={{
                  marginTop: '10px',
                  fontSize: '16px',
                  color: 'rgba(255,255,255,0.55)',
                  fontFamily: 'Space Grotesk',
                  letterSpacing: '0.2px',
                }}
              >
                Followed by {followersCount || followerUsernames.length} smart {(followersCount || followerUsernames.length) === 1 ? 'account' : 'accounts'}
              </span>
            </div>
          )}

          {/* Full-width divider line above bottom text */}
          <div
            style={{
              position: 'absolute',
              bottom: '110px',
              left: 0,
              width: '972px',
              height: '2px',
              background: 'linear-gradient(90deg, transparent 0%, rgba(249, 84, 0, 0.9) 50%, transparent 100%)',
              zIndex: 1,
            }}
          />

          {/* Bottom section */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: 'auto', gap: '6px', zIndex: 1 }}>
            <span style={{ fontSize: '17px', color: 'rgba(255,255,255,0.55)', fontFamily: 'Space Grotesk' }}>
              We'll notify you here when you get access
            </span>
            <span
              style={{
                fontSize: '22px',
                fontWeight: 700,
                color: '#f95400',
                fontFamily: 'Syne',
                letterSpacing: '1.5px',
              }}
            >
              Go Loudrr
            </span>
          </div>
        </div>
      </div>
    ),
    {
      width: 1012,
      height: 638,
      fonts: [
        {
          name: 'Syne',
          data: syneFontData,
          style: 'normal',
          weight: 700,
        },
        {
          name: 'Space Grotesk',
          data: spaceGroteskFontData,
          style: 'normal',
          weight: 700,
        },
      ],
    }
  )
}
