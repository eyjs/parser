import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({
  gfm: true,
  breaks: true,
})

export function renderMarkdown(md: string): string {
  try {
    return DOMPurify.sanitize(marked(md) as string)
  } catch {
    return '<p>렌더링 오류</p>'
  }
}

export function stripFrontMatter(md: string): string {
  if (!md.startsWith('---')) return md

  const endIdx = md.indexOf('\n---', 3)
  if (endIdx < 0) return md

  let cleaned = md.slice(endIdx + 4).replace(/^\n+/, '')
  // Replace remaining `---` page breaks with explicit separator
  cleaned = cleaned.replace(/\n---\n/g, '\n<!-- pagebreak -->\n')
  return cleaned
}
