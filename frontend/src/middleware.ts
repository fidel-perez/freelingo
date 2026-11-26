import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { buildCsp } from '@/lib/csp'
import { SUPPORTED_LOCALES, type Locale } from '@/lib/locales'

// Routes that require authentication — anything else passes through so unknown
// URLs reach Next.js's 404 handler instead of being redirected to /login.
const PROTECTED_ROUTES = [
  '/admin',
  '/assessment',
  '/billing',
  '/chat',
  '/conversation',
  '/dashboard',
  '/faq',
  '/feedback',
  '/flashcards',
  '/games',
  '/grammar',
  '/grammar-practice',
  '/situations',
  '/lesson',
  '/listening',
  '/onboarding',
  '/phrasebook',
  '/plan',
  '/progress',
  '/reading',
  '/settings',
  '/vocabulary',
]

function detectLocale(req: NextRequest): Locale {
  // 1. Cookie already set — respect user choice
  const cookie = req.cookies.get('NEXT_LOCALE')?.value
  if (cookie && (SUPPORTED_LOCALES as readonly string[]).includes(cookie)) {
    return cookie as Locale
  }

  // 2. Parse Accept-Language header with q-weight sorting
  const accept = req.headers.get('accept-language') ?? ''
  const parsed = accept
    .split(',')
    .map((part) => {
      const [langTag, ...params] = part.trim().split(';')
      const qParam = params.find((p) => p.trim().startsWith('q='))
      const q = qParam ? parseFloat(qParam.split('=')[1]) : 1.0
      const lang = langTag.trim().split(/[-_]/)[0].toLowerCase()
      return { lang, q: isNaN(q) ? 0 : q }
    })
    .filter(({ q }) => q > 0 && q <= 1)
    .sort((a, b) => b.q - a.q)

  for (const { lang } of parsed) {
    if ((SUPPORTED_LOCALES as readonly string[]).includes(lang)) {
      return lang as Locale
    }
  }

  // 3. Default to English
  return 'en'
}

function withCsp(response: NextResponse): NextResponse {
  if (process.env.NODE_ENV === 'production') {
    response.headers.set(
      'Content-Security-Policy',
      buildCsp(process.env.IMAGE_MODEL_BASE_URL ?? '')
    )
  }
  return response
}

// Paths the locale and auth logic never applied to; they only get the policy.
const POLICY_ONLY =
  /^\/(_next|api)(\/|$)|^\/favicon\.ico$|\.(?:svg|png|jpg|jpeg|gif|webp|ico)$/

export function middleware(req: NextRequest) {
  return withCsp(handle(req))
}

function handle(req: NextRequest) {
  if (POLICY_ONLY.test(req.nextUrl.pathname)) return NextResponse.next()

  // Detect locale first so we can forward it as a request header.
  // This is needed because request.ts reads incoming request headers/cookies;
  // a cookie set only on the response is not visible on that same request cycle.
  const locale = detectLocale(req)

  const hasRefreshToken = req.cookies.has('refresh_token')
  const isProtected = PROTECTED_ROUTES.some((r) =>
    req.nextUrl.pathname.startsWith(r)
  )

  if (!hasRefreshToken && isProtected) {
    const response = NextResponse.redirect(new URL('/login', req.url))
    if (!req.cookies.has('NEXT_LOCALE')) {
      response.cookies.set('NEXT_LOCALE', locale, {
        path: '/',
        sameSite: 'lax',
        secure: process.env.NODE_ENV === 'production',
        maxAge: 60 * 60 * 24 * 365,
      })
    }
    return response
  }

  // Inject locale as a request header so request.ts picks it up immediately
  // (even on the very first visit when no NEXT_LOCALE cookie exists yet)
  const requestHeaders = new Headers(req.headers)
  requestHeaders.set('x-next-locale', locale)

  const response = NextResponse.next({ request: { headers: requestHeaders } })

  // Persist as cookie for subsequent requests
  if (!req.cookies.has('NEXT_LOCALE')) {
    response.cookies.set('NEXT_LOCALE', locale, {
      path: '/',
      sameSite: 'lax',
      secure: process.env.NODE_ENV === 'production',
      maxAge: 60 * 60 * 24 * 365,
    })
  }

  return response
}

export const config = {
  matcher: ['/:path*'],
}
