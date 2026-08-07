import { ImageResponse } from '@vercel/og'
import { NextRequest } from 'next/server'

export const runtime = 'edge'

// Google Fonts URLs (TTF format)
const SPACE_GROTESK_BOLD_URL = 'https://fonts.gstatic.com/s/spacegrotesk/v22/V8mQoQDjQSkFtoMM3T6r8E7mF71Q-gOoraIAEj4PVksj.ttf'
const SYNE_BOLD_URL = 'https://fonts.gstatic.com/s/syne/v24/8vIS7w4qzmVxsWxjBZRjr0FKM_3fvj6k.ttf'

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
            padding: '48px 60px',
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
              marginBottom: '16px',
              marginTop: '40px',
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
              marginBottom: '24px',
              zIndex: 1,
            }}
          >
            <span
              style={{
                fontSize: '56px',
                fontWeight: 700,
                color: '#ffffff',
                fontFamily: 'Space Grotesk',
                letterSpacing: '-1.5px',
              }}
            >
              Congrats, you&#39;re in!
            </span>
          </div>

          {/* Profile section - Glassy pill */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '28px',
              padding: '28px 44px',
              background: 'linear-gradient(135deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0.06) 100%)',
              borderRadius: '24px',
              border: '1px solid rgba(255, 255, 255, 0.2)',
              zIndex: 1,
            }}
          >
            {/* Avatar */}
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
            </div>

            {/* X username + approved tag */}
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
                gap: '6px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <svg width={26} height={26} viewBox="0 0 24 24" fill="#ffffff">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                </svg>
                <span style={{ fontSize: '30px', fontWeight: 600, color: '#ffffff', fontFamily: 'Space Grotesk' }}>
                  @{xUsername}
                </span>
              </div>
              <span style={{ fontSize: '18px', color: '#f95400', fontFamily: 'Space Grotesk' }}>
                approved
              </span>
            </div>
          </div>

          {/* Full-width divider line above bottom text */}
          <div
            style={{
              position: 'absolute',
              bottom: '130px',
              left: 0,
              width: '972px',
              height: '2px',
              background: 'linear-gradient(90deg, transparent 0%, rgba(249, 84, 0, 0.9) 50%, transparent 100%)',
              zIndex: 1,
            }}
          />

          {/* Bottom section */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: 'auto', gap: '8px', zIndex: 1 }}>
            <span style={{ fontSize: '17px', color: 'rgba(255,255,255,0.55)', fontFamily: 'Space Grotesk' }}>
              Tap Open Loudrr to get started
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
