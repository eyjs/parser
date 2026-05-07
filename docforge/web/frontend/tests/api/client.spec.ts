import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  uploadFiles,
  getParseResult,
  getParseState,
  getActiveTasks,
  getHistory,
  deleteHistory,
  saveMarkdown,
  getQueueStatus,
  cancelTask,
  getVersions,
  getDiff,
  getParseStatusUrl,
  getExportUrl,
  ApiClientError,
} from '@/api/client'

const mockFetch = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

function jsonResponse(data: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve({ success: true, data }),
  })
}

function noContentResponse() {
  return Promise.resolve({
    ok: true,
    status: 204,
    json: () => Promise.reject(new Error('No content')),
  })
}

describe('API Client', () => {
  describe('uploadFiles', () => {
    it('sends files as FormData', async () => {
      const file = new File(['content'], 'test.pdf', { type: 'application/pdf' })
      mockFetch.mockReturnValueOnce(jsonResponse({ task_id: 'abc', task_ids: ['abc'] }))

      const result = await uploadFiles([file])

      expect(mockFetch).toHaveBeenCalledTimes(1)
      const [url, options] = mockFetch.mock.calls[0]
      expect(url).toBe('/api/parse')
      expect(options.method).toBe('POST')
      expect(options.body).toBeInstanceOf(FormData)
      expect(result.task_id).toBe('abc')
    })
  })

  describe('getParseResult', () => {
    it('fetches parse result', async () => {
      const mockResult = {
        task_id: 'task-1',
        filename: 'test.pdf',
        markdown: '# Hello',
        metadata: {},
        stats: {},
        completed_at: '2026-01-01',
        pdf_path: '/path/to/pdf',
      }
      mockFetch.mockReturnValueOnce(jsonResponse(mockResult))

      const result = await getParseResult('task-1')

      expect(mockFetch).toHaveBeenCalledWith('/api/parse/task-1/result', expect.any(Object))
      expect(result.filename).toBe('test.pdf')
      expect(result.markdown).toBe('# Hello')
    })
  })

  describe('getParseState', () => {
    it('fetches parse state', async () => {
      mockFetch.mockReturnValueOnce(jsonResponse({
        task_id: 'task-1',
        status: 'running',
        pct: 50,
      }))

      const result = await getParseState('task-1')
      expect(result.status).toBe('running')
      expect(result.pct).toBe(50)
    })
  })

  describe('getActiveTasks', () => {
    it('returns array of active tasks', async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([
        { task_id: 't1', status: 'running' },
        { task_id: 't2', status: 'queued' },
      ]))

      const result = await getActiveTasks()
      expect(result).toHaveLength(2)
    })
  })

  describe('getHistory', () => {
    it('fetches history list', async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([
        { task_id: 'h1', filename: 'a.pdf', status: 'done' },
      ]))

      const result = await getHistory()
      expect(result[0].task_id).toBe('h1')
    })
  })

  describe('deleteHistory', () => {
    it('sends DELETE request', async () => {
      mockFetch.mockReturnValueOnce(noContentResponse())

      await deleteHistory('task-1')

      const [url, options] = mockFetch.mock.calls[0]
      expect(url).toBe('/api/history/task-1')
      expect(options.method).toBe('DELETE')
    })
  })

  describe('saveMarkdown', () => {
    it('sends POST with markdown body', async () => {
      mockFetch.mockReturnValueOnce(noContentResponse())

      await saveMarkdown('task-1', '# Content')

      const [url, options] = mockFetch.mock.calls[0]
      expect(url).toBe('/api/save/task-1')
      expect(options.method).toBe('POST')
      expect(JSON.parse(options.body)).toEqual({ markdown: '# Content' })
    })
  })

  describe('getQueueStatus', () => {
    it('fetches queue status', async () => {
      mockFetch.mockReturnValueOnce(jsonResponse({
        running: 1,
        queued: 2,
        workers: 4,
      }))

      const result = await getQueueStatus()
      expect(result.running).toBe(1)
      expect(result.workers).toBe(4)
    })
  })

  describe('cancelTask', () => {
    it('sends POST cancel request', async () => {
      mockFetch.mockReturnValueOnce(noContentResponse())

      await cancelTask('task-1')

      const [url, options] = mockFetch.mock.calls[0]
      expect(url).toBe('/api/parse/task-1/cancel')
      expect(options.method).toBe('POST')
    })
  })

  describe('getVersions', () => {
    it('fetches version list', async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([
        { name: 'v1', path: '/v1', size: 100 },
      ]))

      const result = await getVersions('task-1')
      expect(result[0].name).toBe('v1')
    })
  })

  describe('getDiff', () => {
    it('fetches diff with query params', async () => {
      mockFetch.mockReturnValueOnce(jsonResponse({
        v1: 'v1',
        v2: 'v2',
        diff: '--- diff ---',
        has_changes: true,
      }))

      const result = await getDiff('task-1', 'v1', 'v2')

      expect(mockFetch.mock.calls[0][0]).toContain('v1=v1')
      expect(mockFetch.mock.calls[0][0]).toContain('v2=v2')
      expect(result.has_changes).toBe(true)
    })
  })

  describe('error handling', () => {
    it('throws ApiClientError on HTTP error with JSON body', async () => {
      mockFetch.mockReturnValueOnce(
        Promise.resolve({
          ok: false,
          status: 404,
          json: () => Promise.resolve({ error: { code: 'NOT_FOUND', message: 'Task not found' } }),
        }),
      )

      await expect(getParseResult('bad-id')).rejects.toThrow(ApiClientError)
      try {
        await getParseResult('bad-id')
      } catch (e) {
        // Already thrown above, just validate shape
      }
    })

    it('throws ApiClientError on HTTP error without JSON body', async () => {
      mockFetch.mockReturnValueOnce(
        Promise.resolve({
          ok: false,
          status: 500,
          json: () => Promise.reject(new Error('not json')),
        }),
      )

      await expect(getParseResult('bad-id')).rejects.toThrow('HTTP 500')
    })
  })

  describe('URL helpers', () => {
    it('getParseStatusUrl returns correct URL', () => {
      expect(getParseStatusUrl('abc')).toContain('/api/parse/abc/status')
    })

    it('getExportUrl returns correct URL', () => {
      expect(getExportUrl('abc')).toContain('/api/export/abc')
    })
  })
})
