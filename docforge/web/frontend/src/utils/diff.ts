import { diffLines, diffWords, createPatch } from 'diff'
import { html as diff2html } from 'diff2html'

export interface DiffLine {
  type: 'added' | 'removed' | 'unchanged'
  content: string
}

/**
 * Compute a line-by-line diff and return colored lines.
 */
export function computeLineDiff(oldText: string, newText: string): DiffLine[] {
  const changes = diffLines(oldText, newText)
  const result: DiffLine[] = []

  for (const change of changes) {
    const lines = change.value.split('\n')
    // Remove trailing empty string from split
    if (lines[lines.length - 1] === '') lines.pop()

    for (const line of lines) {
      if (change.added) {
        result.push({ type: 'added', content: line })
      } else if (change.removed) {
        result.push({ type: 'removed', content: line })
      } else {
        result.push({ type: 'unchanged', content: line })
      }
    }
  }

  return result
}

/**
 * Compute word-level diff for inline display.
 */
export function computeWordDiff(oldText: string, newText: string): DiffLine[] {
  const changes = diffWords(oldText, newText)
  const result: DiffLine[] = []

  for (const change of changes) {
    if (change.added) {
      result.push({ type: 'added', content: change.value })
    } else if (change.removed) {
      result.push({ type: 'removed', content: change.value })
    } else {
      result.push({ type: 'unchanged', content: change.value })
    }
  }

  return result
}

/**
 * Render a unified diff string as HTML using diff2html.
 */
export function renderDiffHtml(
  oldText: string,
  newText: string,
  outputFormat: 'side-by-side' | 'line-by-line' = 'side-by-side',
): string {
  const unifiedDiff = createPatch('document', oldText, newText, '', '', {
    context: 3,
  })

  return diff2html(unifiedDiff, {
    drawFileList: false,
    matching: 'lines',
    outputFormat: outputFormat === 'side-by-side' ? 'side-by-side' : 'line-by-line',
  })
}
