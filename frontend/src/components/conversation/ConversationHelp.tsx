'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'

const BUTTON =
  'border-fl-border text-fl-muted-1 hover:border-fl-border-2 hover:text-fl-fg inline-flex min-h-10 min-w-10 items-center justify-center border px-3 font-mono text-xs tracking-widest uppercase transition-colors'

// Every feature page of the app menu, with the nav label key of each.
const PAGES = [
  ['/plan', 'myPlan'],
  ['/progress', 'progress'],
  ['/assessment', 'assessment'],
  ['/chat', 'tutor'],
  ['/conversation', 'conversation'],
  ['/listening', 'listening'],
  ['/reading', 'reading'],
  ['/flashcards', 'flashcards'],
  ['/games', 'games'],
  ['/grammar-practice', 'practiceGrammar'],
  ['/situations', 'situations'],
  ['/grammar', 'grammar'],
  ['/vocabulary', 'vocabulary'],
  ['/phrasebook', 'phrasebook'],
  ['/settings', 'settings'],
] as const

/** Flashcards link and a help button that opens a panel explaining each feature. */
export default function ConversationHelp() {
  const t = useTranslations('conversation')
  const tNav = useTranslations('nav')
  const [open, setOpen] = useState(false)
  const items = t.raw('helpItems') as { title: string; body: string }[]

  return (
    <div className="w-full">
      <div className="flex flex-wrap gap-2">
        <Link href="/flashcards" className={BUTTON}>
          {t('flashcardsLink')}
        </Link>
        <button
          type="button"
          className={BUTTON}
          aria-expanded={open}
          aria-controls="conversation-help"
          onClick={() => setOpen((value) => !value)}
        >
          {t('helpButton')}
        </button>
      </div>
      {open && (
        <section
          id="conversation-help"
          aria-label={t('helpTitle')}
          className="border-fl-border bg-fl-surface mt-3 max-h-[60vh] space-y-3 overflow-y-auto border p-4 text-sm"
        >
          <h2 className="text-fl-fg font-sans text-base font-semibold">
            {t('helpTitle')}
          </h2>
          {items.map((item) => (
            <div key={item.title}>
              <h3 className="text-fl-fg font-sans font-medium">{item.title}</h3>
              <p className="text-fl-muted-1">{item.body}</p>
            </div>
          ))}
          <h3 className="text-fl-fg font-sans font-medium">
            {t('helpPagesTitle')}
          </h3>
          <ul className="space-y-1">
            {PAGES.map(([href, label]) => (
              <li key={href}>
                <Link
                  href={href}
                  className="text-fl-accent flex min-h-10 items-center underline"
                >
                  {tNav(label)}
                </Link>
                <span className="text-fl-muted-1 block">
                  {t(`helpPages.${label}`)}
                </span>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className={BUTTON}
            onClick={() => setOpen(false)}
          >
            {t('helpClose')}
          </button>
        </section>
      )}
    </div>
  )
}
