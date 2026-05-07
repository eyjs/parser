import { marked } from 'marked'

// Configure marked for GFM with breaks
marked.setOptions({
  gfm: true,
  breaks: true,
})

/**
 * Render markdown string to HTML.
 */
export function renderMarkdown(md: string): string {
  try {
    return marked(md) as string
  } catch {
    return '<p>렌더링 오류</p>'
  }
}

/**
 * Strip YAML frontmatter from markdown.
 * Removes `---` delimited block at the start of the document.
 * Also converts `---` page separators to HTML comment separators.
 */
export function stripFrontMatter(md: string): string {
  if (!md.startsWith('---')) return md

  const endIdx = md.indexOf('\n---', 3)
  if (endIdx < 0) return md

  let cleaned = md.slice(endIdx + 4).replace(/^\n+/, '')
  // Replace remaining `---` page breaks with explicit separator
  cleaned = cleaned.replace(/\n---\n/g, '\n<!-- pagebreak -->\n')
  return cleaned
}
